"""Parse, edit and re-emit llama.cpp launch commands.

A launch command is the shell string you would type, for example::

    CUDA_VISIBLE_DEVICES=0 llama-server -m /models/qwen3-8b-q4_k_m.gguf \\
        --port 8080 -c 8192 -ngl 99 -fa on --no-mmap

``parse_command`` turns it into a :class:`LaunchCommand` (env vars, program,
ordered flag/value pairs). Edits keep each flag where it was and keep the
spelling the user chose (``-ngl`` stays ``-ngl``), so copying a profile from
one model to another only changes what you asked to change.
"""
from __future__ import annotations

import re
import shlex
from dataclasses import dataclass, field
from typing import Any, Optional

# canonical name -> (spellings, kind). kind: "value" takes one argument,
# "bool" takes none, "optional" takes one only when it is in OPTIONAL_VALUES.
FLAGS: dict[str, tuple[tuple[str, ...], str]] = {
    "model": (("-m", "--model"), "value"),
    "hf-repo": (("-hf", "-hfr", "--hf-repo"), "value"),
    "alias": (("-a", "--alias"), "value"),
    "host": (("--host",), "value"),
    "port": (("--port",), "value"),
    "ctx-size": (("-c", "--ctx-size"), "value"),
    "n-gpu-layers": (("-ngl", "--gpu-layers", "--n-gpu-layers"), "value"),
    "batch-size": (("-b", "--batch-size"), "value"),
    "ubatch-size": (("-ub", "--ubatch-size"), "value"),
    "threads": (("-t", "--threads"), "value"),
    "threads-batch": (("-tb", "--threads-batch"), "value"),
    "parallel": (("-np", "--parallel"), "value"),
    "n-predict": (("-n", "--n-predict"), "value"),
    "flash-attn": (("-fa", "--flash-attn"), "optional"),
    "load-mode": (("-lm", "--load-mode"), "value"),
    "fit": (("-fit", "--fit"), "optional"),
    "mmap": (("--mmap",), "bool"),
    "no-mmap": (("--no-mmap",), "bool"),
    "mlock": (("--mlock",), "bool"),
    "cache-type-k": (("-ctk", "--cache-type-k"), "value"),
    "cache-type-v": (("-ctv", "--cache-type-v"), "value"),
    "mmproj": (("-mm", "--mmproj"), "value"),
    "image-max-tokens": (("--image-max-tokens",), "value"),
    "model-draft": (("-md", "--model-draft"), "value"),
    "spec-type": (("--spec-type",), "value"),
    "spec-draft-n-max": (("--spec-draft-n-max",), "value"),
    "gpu-layers-draft": (("-ngld", "--gpu-layers-draft", "--n-gpu-layers-draft"), "value"),
    "draft-max": (("--draft-max", "--draft", "--draft-n"), "value"),
    "draft-min": (("--draft-min", "--draft-n-min"), "value"),
    "n-cpu-moe": (("-ncmoe", "--n-cpu-moe"), "value"),
    "cpu-moe": (("-cmoe", "--cpu-moe"), "bool"),
    "override-tensor": (("-ot", "--override-tensor"), "value"),
    "split-mode": (("-sm", "--split-mode"), "value"),
    "tensor-split": (("-ts", "--tensor-split"), "value"),
    "main-gpu": (("-mg", "--main-gpu"), "value"),
    "device": (("-dev", "--device"), "value"),
    "rope-scaling": (("--rope-scaling",), "value"),
    "rope-freq-scale": (("--rope-freq-scale",), "value"),
    "rope-freq-base": (("--rope-freq-base",), "value"),
    "jinja": (("--jinja",), "bool"),
    "cont-batching": (("-cb", "--cont-batching"), "bool"),
    "no-cont-batching": (("-nocb", "--no-cont-batching"), "bool"),
    "no-kv-offload": (("-nkvo", "--no-kv-offload"), "bool"),
    "kv-unified": (("-kvu", "--kv-unified"), "bool"),
    "swa-full": (("--swa-full",), "bool"),
    "no-warmup": (("--no-warmup",), "bool"),
    "no-webui": (("--no-webui",), "bool"),
    "metrics": (("--metrics",), "bool"),
    "embedding": (("--embedding", "--embeddings"), "bool"),
    "pooling": (("--pooling",), "value"),
    "reranking": (("--reranking", "--rerank"), "bool"),
    "reasoning-budget": (("--reasoning-budget",), "value"),
    "chat-template-kwargs": (("--chat-template-kwargs",), "value"),
    "verbose": (("-v", "--verbose", "--log-verbose"), "bool"),
}
OPTIONAL_VALUES = {"on", "off", "auto", "1", "0", "true", "false", "enabled", "disabled"}

_SPELLING = {s: name for name, (spellings, _) in FLAGS.items() for s in spellings}
_NEGATIVE_NUMBER = re.compile(r"^-\d+(\.\d+)?$")
_ENV_ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


def canonical(flag: str) -> str:
    """``-ngl`` / ``--gpu-layers`` -> ``n-gpu-layers``; unknown flags lose their dashes."""
    return _SPELLING.get(flag) or flag.lstrip("-")


# How a newly added flag is written: the short forms Argos configs and the
# Cookbook use for these, the long form for everything else.
PREFERRED_SPELLING = {"n-gpu-layers": "-ngl", "ctx-size": "-c", "load-mode": "-lm"}


def _spelling_for(name: str) -> str:
    if name in PREFERRED_SPELLING:
        return PREFERRED_SPELLING[name]
    if name in FLAGS:
        return next(s for s in FLAGS[name][0] if s.startswith("--"))
    return name if name.startswith("-") else f"--{name}"


def _kind(flag: str) -> str:
    name = _SPELLING.get(flag)
    return FLAGS[name][1] if name else "unknown"


def _key(name: str) -> str:
    return canonical(name) if name.startswith("-") else name


def _is_flag(tok: str) -> bool:
    return tok.startswith("-") and len(tok) > 1 and not _NEGATIVE_NUMBER.match(tok)


@dataclass
class Arg:
    flag: str                     # as written ("-ngl"); "" for a bare positional
    value: Optional[str] = None   # None for boolean flags
    joined: bool = False          # written as --flag=value

    @property
    def name(self) -> str:
        return canonical(self.flag) if self.flag else ""

    def tokens(self) -> list[str]:
        if not self.flag:
            return [self.value or ""]
        if self.value is None:
            return [self.flag]
        if self.joined:
            return [f"{self.flag}={self.value}"]
        return [self.flag, self.value]


_PATH_FLAGS = {"model", "mmproj", "model-draft"}


def _dquote(tok: str) -> str:
    if any(c in tok for c in '"$`\\') or not tok:
        return _quote(tok)
    return f'"{tok}"'


def _quote(tok: str) -> str:
    if tok and re.fullmatch(r"[\w@%+=:,./${}-]+", tok):
        return tok
    return shlex.quote(tok)


def _int(v: Optional[str]) -> Optional[int]:
    try:
        return int(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


@dataclass
class LaunchCommand:
    program: str = "llama-server"
    args: list[Arg] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)

    # ----- lookup ---------------------------------------------------------
    def _find(self, name: str) -> int:
        key = _key(name)
        for i, a in enumerate(self.args):
            if a.flag and a.name == key:
                return i
        return -1

    def has(self, name: str) -> bool:
        return self._find(name) >= 0

    def get(self, name: str) -> Optional[str]:
        """Value of a flag, ``""`` for a present boolean flag, None if absent."""
        i = self._find(name)
        if i < 0:
            return None
        return self.args[i].value if self.args[i].value is not None else ""

    # ----- edits ----------------------------------------------------------
    def set(self, name: str, value: Any = True) -> None:
        """Set a flag. ``True`` = bare flag, None/False/"" = remove it."""
        if value is None or value is False or value == "":
            self.remove(name)
            return
        val = None if value is True else str(value)
        i = self._find(name)
        if i >= 0:
            self.args[i].value = val
            if val is None:
                self.args[i].joined = False
        else:
            self.args.append(Arg(_spelling_for(name), val))
        # --mmap and --no-mmap are mutually exclusive
        other = {"mmap": "no-mmap", "no-mmap": "mmap"}.get(_key(name))
        if other:
            self.remove(other)

    def remove(self, name: str) -> None:
        key = _key(name)
        self.args = [a for a in self.args if not (a.flag and a.name == key)]

    def copy(self) -> "LaunchCommand":
        return LaunchCommand(self.program, [Arg(a.flag, a.value, a.joined) for a in self.args],
                             dict(self.env))

    def with_edits(self, edits: Optional[dict] = None, env: Optional[dict] = None,
                   arg_edits: Optional[dict] = None) -> "LaunchCommand":
        """Copy with edits applied.

        ``arg_edits`` ({index: value|None}) targets one argument by position, for
        flags that repeat (several ``-ot``); None deletes it. It runs first, so
        indices refer to this command. ``edits`` ({name: value}) then goes through
        :meth:`set`, and ``env`` replaces all env vars.
        """
        cmd = self.copy()
        by_index = {int(k): v for k, v in (arg_edits or {}).items()}
        for idx, value in sorted(by_index.items(), reverse=True):
            if not 0 <= idx < len(cmd.args):
                raise ValueError(f"argument index {idx} out of range")
            if value is None:
                del cmd.args[idx]
            elif cmd.args[idx].value is not None:
                cmd.args[idx].value = str(value)
        for name, value in (edits or {}).items():
            cmd.set(name, value)
        if env is not None:
            cmd.env = {str(k): str(v) for k, v in env.items() if str(k).strip()}
        return cmd

    # ----- output ---------------------------------------------------------
    def argv(self) -> list[str]:
        out = [self.program]
        for a in self.args:
            out += a.tokens()
        return out

    def to_string(self, multiline: bool = False, style: str = "plain") -> str:
        """``style="odysseus"`` double-quotes file paths the way Odysseus' Cookbook does."""
        env = [f"{k}={_quote(v)}" for k, v in self.env.items()]
        head = " ".join(env + [_quote(self.program)])
        dq = style == "odysseus"
        parts = [" ".join(_dquote(t) if dq and a.value is not None and t is a.value and a.name in _PATH_FLAGS
                          else _quote(t) for t in a.tokens()) for a in self.args]
        if multiline and parts:
            return " \\\n  ".join([head] + parts)
        return " ".join([head] + parts)

    def summary(self) -> dict:
        """Structured view of the settings that matter for performance."""
        fa = self.get("flash-attn")
        mmap = False if self.has("no-mmap") else (True if self.has("mmap") else None)
        model = self.get("model") or self.get("hf-repo") or ""
        return {
            "model": model,
            "port": _int(self.get("port")),
            "host": self.get("host"),
            "ctx_size": _int(self.get("ctx-size")),
            "n_gpu_layers": self.get("n-gpu-layers"),
            "batch_size": _int(self.get("batch-size")),
            "ubatch_size": _int(self.get("ubatch-size")),
            "threads": _int(self.get("threads")),
            "parallel": _int(self.get("parallel")),
            "flash_attn": None if fa is None else (fa or "on"),
            "mmap": mmap,
            "cache_type_k": self.get("cache-type-k"),
            "cache_type_v": self.get("cache-type-v"),
            "mmproj": self.get("mmproj"),
            "model_draft": self.get("model-draft"),
            "spec_type": self.get("spec-type"),
            "spec_draft_n_max": _int(self.get("spec-draft-n-max")),
            "load_mode": self.get("load-mode"),
            "fit": None if self.get("fit") is None else (self.get("fit") or "on"),
            "split_mode": self.get("split-mode"),
            "embedding": self.has("embedding"),
        }

    def to_dict(self) -> dict:
        return {
            "program": self.program,
            "env": dict(self.env),
            "args": [{"flag": a.flag, "name": a.name, "value": a.value} for a in self.args],
            "summary": self.summary(),
            "command": self.to_string(),
            "multiline": self.to_string(multiline=True),
        }


_EXPORT = re.compile(r"^\s*export\s+([A-Za-z_][A-Za-z0-9_]*)=(\"[^\"]*\"|'[^']*'|[^\s;&]*)\s*(?:;|&&)?\s*")
_PRINTF = re.compile(r"""\$\(\s*printf\s+(?:%s|["']%s["'])\s+(?:["']([^"']*)["']|([^\s)]+))\s*\)""")
_SHELL_OPS = {"&&", "||", ";", "|"}


def _clean(raw: str) -> str:
    """Normalise the shell forms Odysseus writes into one plain command line.

    Drops comment lines, joins backslash continuations, turns ``export K=V``
    (own line, or ``export K=V; cmd``) into a leading ``K=V`` assignment and
    unwraps ``$(printf %s '/path')`` -- the same translations Argos's
    Odysseus integration applies.
    """
    lines = [ln for ln in (raw or "").splitlines() if not ln.strip().startswith("#")]
    text = re.sub(r"\\[ \t]*\n", " ", "\n".join(lines)).strip()
    env = []
    while True:
        m = _EXPORT.match(text)
        if not m:
            break
        val = m.group(2)
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            val = val[1:-1]
        env.append(f"{m.group(1)}={shlex.quote(val)}")
        text = text[m.end():].lstrip()
    text = _PRINTF.sub(lambda m: m.group(1) if m.group(1) is not None else m.group(2), text)
    return " ".join(env + [text]).strip()


def parse_command(raw: str) -> LaunchCommand:
    """Parse a shell launch string. Raises ValueError on unbalanced quotes."""
    tokens = shlex.split(_clean(raw))
    ops = _SHELL_OPS.intersection(tokens)
    if ops:
        raise ValueError(f"shell operators ({' '.join(sorted(ops))}) are not supported; use one command")
    env: dict[str, str] = {}
    i = 0
    if tokens and tokens[0] == "env":
        i = 1
    while i < len(tokens) and _ENV_ASSIGN.match(tokens[i]):
        k, _, v = tokens[i].partition("=")
        env[k] = v
        i += 1
    if i >= len(tokens):
        raise ValueError("no program found in command")
    cmd = LaunchCommand(program=tokens[i], env=env)
    rest = tokens[i + 1:]
    j = 0
    while j < len(rest):
        tok = rest[j]
        nxt = rest[j + 1] if j + 1 < len(rest) else None
        if not _is_flag(tok):
            cmd.args.append(Arg("", tok))
            j += 1
            continue
        if "=" in tok and tok.startswith("--"):
            flag, _, val = tok.partition("=")
            cmd.args.append(Arg(flag, val, joined=True))
            j += 1
            continue
        kind = _kind(tok)
        takes = nxt is not None and (
            (kind == "value") or
            (kind == "optional" and nxt.lower() in OPTIONAL_VALUES) or
            (kind == "unknown" and not _is_flag(nxt))
        )
        cmd.args.append(Arg(tok, nxt if takes else None))
        j += 2 if takes else 1
    return cmd


def swap_model_id(model_path: str) -> str:
    """The model ID Argos's modelscan gives a GGUF file (its SanitizeModelID).

    ``/app/models/Qwen3-8B-Q4_K_M.gguf`` -> ``qwen3-8b-q4_k_m``. Shard suffixes
    stay, exactly as modelscan keeps them.
    """
    stem = re.sub(r"\.gguf$", "", re.split(r"[\\/]", (model_path or "").rstrip("/\\"))[-1], flags=re.IGNORECASE)
    return re.sub(r"[^a-zA-Z0-9._-]+", "-", stem.lower()).strip("-") or "model"


_CORE = (("model", "--model"), ("host", "--host"), ("port", "--port"),
         ("n-gpu-layers", "-ngl"), ("ctx-size", "-c"))


def to_odysseus(cmd: LaunchCommand, port: Any = 8080, host: str = "0.0.0.0") -> LaunchCommand:
    """Reorder into the shape Odysseus' Cookbook writes::

        [ENV=..] llama-server --model "<gguf>" --host 0.0.0.0 --port N -ngl N -c N <extras>

    ``--host``/``--port`` are added when missing (Argos health-checks the
    port); ``-ngl``/``-c`` move to the front only when set. Every other flag
    keeps its order and spelling. Anything that is not llama-server (a wrapper
    script, a benchmark tool) is returned unchanged: reordering its arguments
    could move flags in front of the wrapper's own positional arguments.
    """
    c = cmd.copy()
    if not c.program.rsplit("/", 1)[-1].startswith("llama-server"):
        return c
    core: list[Arg] = []
    for name, spelling in _CORE:
        i = c._find(name)
        if i >= 0:
            core.append(Arg(spelling, c.args[i].value))
        elif name == "host":
            core.append(Arg(spelling, host))
        elif name == "port":
            core.append(Arg(spelling, str(port)))
    names = {n for n, _ in _CORE}
    lead = []  # bare positional arguments right after the program stay there
    for a in c.args:
        if a.flag:
            break
        lead.append(a)
    rest = c.args[len(lead):]
    c.args = lead + core + [a for a in rest if not (a.flag and a.name in names)]
    return c


def guess_base_model(model: str) -> str:
    """``/models/Qwen3-8B-Q4_K_M.gguf`` -> ``Qwen3-8B-Q4_K_M``; ``org/repo:Q4`` -> ``repo:Q4``."""
    name = (model or "").rstrip("/").split("/")[-1]
    return re.sub(r"(-0+1-of-\d+)?\.gguf$", "", name, flags=re.IGNORECASE)


__all__ = ["Arg", "LaunchCommand", "FLAGS", "canonical", "parse_command", "guess_base_model",
           "swap_model_id", "to_odysseus"]
