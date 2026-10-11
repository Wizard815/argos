"""Shared helpers for parsing llama.cpp launch commands.

A launch command is an opaque shell string such as::

    llama-server --model /models/qwen2-7b-instruct-q4_k_m.gguf \
        --port 8080 --ctx-size 8192 --n-gpu-layers 35 \
        --mmap 1 --flash-attn

This module tokenizes it into structured fields so callers can copy a profile
from one model onto another while editing only the bits that must change (the
model path, port, gpu layers, etc.). Both the Odysseus cookbook reader and the
llama-swap config reader rely on this.
"""
from __future__ import annotations

import shlex
from dataclasses import dataclass, field
from typing import Optional


def _quote(tok: str) -> str:
    """Quote only when needed (spaces/quotes present); keep flags/values tidy."""
    if any(c in tok for c in (" ", "'", '"')):
        return shlex.quote(tok)
    return tok


@dataclass
class LaunchCommand:
    program: str = "llama-server"
    args: list[str] = field(default_factory=list)
    raw: str = ""
    env_vars: dict[str, str] = field(default_factory=dict)

    @property
    def model(self) -> Optional[str]:
        return _flag_value("model", self.args) or _flag_value("model-path", self.args)

    @property
    def port(self) -> Optional[int]:
        v = _flag_value("port", self.args)
        try:
            return int(v)
        except (TypeError, ValueError):
            return None

    @property
    def n_gpu_layers(self) -> Optional[int]:
        v = _flag_value("n-gpu-layers", self.args)
        if isinstance(v, str) and v.lstrip("-").isdigit():
            return int(v)
        return None

    @property
    def ctx_size(self) -> Optional[int]:
        for k in ("ctx-size", "--context-length"):
            v = _flag_value(k, self.args)
            if v is not None and v.isdigit():
                return int(v)
        return None

    @property
    def n_batch(self) -> Optional[int]:
        v = _flag_value("n-batch", self.args)
        return int(v) if v is not None else None

    @property
    def n_ubatch(self) -> Optional[int]:
        v = _flag_value("n-ubatch", self.args)
        return int(v) if isinstance(v, str) and v.isdigit() else None

    @property
    def n_threads(self) -> Optional[int]:
        v = _flag_value("n-threads", self.args)
        return int(v) if v is not None else None

    @property
    def mmproj(self) -> Optional[str]:
        return _flag_value("mmproj", self.args)

    @property
    def mtp(self) -> Optional[str]:
        return _flag_value("mtp", self.args) or _flag_value("mtp-file", self.args)

    @property
    def load_mode(self) -> Optional[str]:
        for k in ("load-mode", "-lm"):
            v = _flag_value(k, self.args)
            if v:
                return v.split()[0]
        return None

    @property
    def kv_size(self) -> Optional[str]:
        return _flag_value("kv-size-tokens", self.args) or _flag_value("kv-size", self.args)

    @property
    def rope_freq_scale(self) -> Optional[float]:
        v = _flag_value("rope-freq-scale", self.args)
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    @property
    def flash_attention(self) -> bool:
        return "--flash-attn" in self.args or "flash-attn" in self.args

    @property
    def mmap(self) -> int:
        return 1 if _flag_value("mmap", self.args) == "1" else 0

    def clone_with(self, **overrides) -> "LaunchCommand":
        """Return a copy with selected flags replaced/added.

        Overrides are given as flag names (e.g. ``model="/new/path.gguf"``,
        ``port=9090``) applied *after* the originals, so unknown flags pass through
        untouched. This is what makes copy/paste across models safe: only the
        fields you name change; everything else carries over verbatim. Use
        ``env_var={...}`` to add/replace environment variables.
        """
        env_from_override = overrides.pop("env_var", None) or {}
        cmd = LaunchCommand(
            program=self.program,
            args=list(self.args),
            raw=self.raw or self.to_string(),
            env_vars=dict(self.env_vars),
        )
        if env_from_override:
            cmd.env_vars.update(env_from_override)
        FLAG_ALIASES = {"ctx_size": "ctx-size", "n_batch": "n-batch",
                        "n_ubatch": "n-ubatch", "n_threads": "n-threads",
                        "n_gpu_layers": "n-gpu-layers"}
        for key, value in overrides.items():
            if value is None:
                continue
            key = FLAG_ALIASES.get(key, key)
            token = str(value) if not isinstance(value, bool) else ("1" if value else "0")
            # remove existing "--key=" style occurrences and bare "--key <value>" pairs
            cleaned, i = [], 0
            while i < len(cmd.args):
                a = cmd.args[i]
                if a == f"--{key}" and i + 1 < len(cmd.args):
                    i += 2
                    continue
                if a.startswith(f"--{key}="):
                    i += 1
                    continue
                cleaned.append(a)
                i += 1
            cleaned.append(f"--{key}")
            cleaned.append(token)
            cmd.args = cleaned
        return cmd

    def to_string(self, sep=" ") -> str:
        parts = [self.program] + list(self.args)
        joined = sep.join(_quote(p) for p in parts)
        for k, v in sorted(self.env_vars.items()):
            joined = f"{k}={_quote(str(v))} {joined}"
        return joined


def _flag_value(flag: str, args: list[str]) -> Optional[str]:
    """Return the value following ``--flag`` (or bare ``--flag``)."""
    for i, a in enumerate(args):
        if a == f"--{flag}" or a == f"-{flag}":
            return args[i + 1] if i + 1 < len(args) else ""
        if a.startswith(f"--{flag}="):
            return a.split("=", 1)[1]
    return None


def parse_launch_command(raw: str) -> LaunchCommand:
    """Parse a shell launch string into a :class:`LaunchCommand`.

    Handles leading environment assignments like
    ``HIP_VISIBLE_DEVICES=0 llama-server ...``.
    """
    raw = (raw or "").strip()
    tokens = shlex.split(raw)
    env_vars: dict[str, str] = {}
    body: list[str] = []
    for tok in tokens:
        if "=" in tok and not tok.startswith("--"):
            key, _, val = tok.partition("=")
            if key.isidentifier():
                env_vars[key] = val
                continue
        body.append(tok)
    prog = body[0] if body else "llama-server"
    return LaunchCommand(program=prog, args=body[1:], raw=raw, env_vars=env_vars)


__all__ = ["LaunchCommand", "parse_launch_command", "_flag_value"]
