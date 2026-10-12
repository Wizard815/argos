"""Local model library and per-model prefab commands.

``scan_models`` walks the models directory the way llama-swap-rescan's
modelscan does: every ``*.gguf`` except non-first shards of a split file and
the multimodal projectors, which are paired with the models next to them
(Odysseus looks for ``mmproj-*.gguf`` beside the model the same way). IDs are
llama-swap's, so a profile saved here lines up with the model llama-swap
generated from the same file.

``prefab_command`` is the starting point the Models view offers for each
file: the user's defaults from Settings, adjusted for the kind of model.
"""
from __future__ import annotations

import os
import re
import shlex
from typing import Optional

from .command import LaunchCommand, parse_command, swap_model_id, to_odysseus

SHARD = re.compile(r"-(\d{5})-of-(\d{5})\.gguf$", re.IGNORECASE)
QUANT = re.compile(
    r"(?i)(?<![A-Za-z0-9])((?:UD-)?(?:I?Q\d(?:_[A-Z0-9]+)*|BF16|F16|F32|MXFP4(?:_MOE)?))(?![A-Za-z0-9])")
EMBED = re.compile(r"(?i)embed|(?<![a-z])(bge|e5|gte)-")
RERANK = re.compile(r"(?i)rerank")
VISION = re.compile(r"(?i)(-vl|vl-|vision|llava|pixtral|minicpm-v)")


def quant_of(name: str) -> str:
    m = None
    for m in QUANT.finditer(os.path.basename(name)):
        pass  # the last match is the quant; earlier ones can be model-size words
    return m.group(1).upper() if m else ""


def is_mmproj(name: str) -> bool:
    return "mmproj" in os.path.basename(name).lower()


def kind_of(name: str, has_mmproj: bool) -> str:
    base = os.path.basename(name).lower()
    if RERANK.search(base):
        return "reranker"
    if EMBED.search(base):
        return "embedding"
    if "mtp" in base:
        return "mtp"
    if has_mmproj or VISION.search(base):
        return "vision"
    return "chat"


def scan_models(models_dir: str) -> list[dict]:
    if not models_dir or not os.path.isdir(models_dir):
        return []
    by_dir: dict[str, dict[str, list[str]]] = {}
    for root, dirs, files in os.walk(models_dir, followlinks=True):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for f in files:
            if f.lower().endswith(".gguf") and not f.startswith("."):
                group = by_dir.setdefault(root, {"models": [], "mmproj": []})
                group["mmproj" if is_mmproj(f) else "models"].append(os.path.join(root, f))
    out = []
    for root, group in by_dir.items():
        mmprojs = sorted(group["mmproj"], key=lambda p: (quant_of(p) not in ("F16", "BF16"), p))
        for path in sorted(group["models"]):
            m = SHARD.search(path)
            if m and m.group(1) != "00001":
                continue
            size, shards = _size(path), 1
            if m:
                total = int(m.group(2))
                siblings = [SHARD.sub(f"-{i:05d}-of-{m.group(2)}.gguf", path) for i in range(1, total + 1)]
                present = [p for p in siblings if os.path.exists(p)]
                size, shards = sum(_size(p) for p in present), total
                if len(present) < total:
                    shards = -total  # incomplete split: flagged in the UI
            name = re.sub(r"\.gguf$", "", os.path.basename(path), flags=re.IGNORECASE)
            out.append({
                "id": swap_model_id(path), "name": name, "path": path,
                "dir": os.path.relpath(root, models_dir), "size": size, "shards": shards,
                "quant": quant_of(path), "mmproj": mmprojs, "kind": kind_of(path, bool(mmprojs)),
                "mtime": _mtime(path),
            })
    return sorted(out, key=lambda m: m["name"].lower())


def _size(p: str) -> int:
    try:
        return os.path.getsize(p)
    except OSError:
        return 0


def _mtime(p: str) -> float:
    try:
        return os.path.getmtime(p)
    except OSError:
        return 0.0


def _env_lines(text: str) -> list[str]:
    out = []
    for line in (text or "").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            out.append(f"{k.strip()}={shlex.quote(v.strip())}")
    return out


def prefab_command(model: dict, prefab: dict, port: Optional[str] = None) -> LaunchCommand:
    """A launch command for ``model`` built from the Settings -> Prefab defaults."""
    kind = model.get("kind", "chat")
    env = _env_lines(prefab.get("env", ""))
    args = ["llama-server", "--model", shlex.quote(model["path"]),
            "--host", prefab.get("host") or "0.0.0.0", "--port", str(port or prefab.get("port") or 8080)]
    if prefab.get("ngl"):
        args += ["-ngl", str(prefab["ngl"])]
    ctx = str(prefab.get("ctx") or "")
    if kind in ("embedding", "reranker"):
        args += ["-c", str(min(int(ctx or 8192), 8192))]
        args += ["--embedding", "--pooling", "mean"] if kind == "embedding" else ["--reranking"]
    else:
        if ctx:
            args += ["-c", ctx]
        if prefab.get("flash_attn"):
            args += ["--flash-attn", prefab["flash_attn"]]
        if prefab.get("cache_type"):
            args += ["--cache-type-k", prefab["cache_type"], "--cache-type-v", prefab["cache_type"]]
    if prefab.get("load_mode"):
        args += ["-lm", prefab["load_mode"]]
    if prefab.get("split_mode"):
        args += ["--split-mode", prefab["split_mode"]]
    if kind not in ("embedding", "reranker") and prefab.get("jinja", True):
        args.append("--jinja")
    if kind == "mtp":
        env.append("LLAMA_ENABLE_MTP_OPT=1")
        args += ["--spec-type", "draft-mtp", "--spec-draft-n-max", "3"]
    if model.get("mmproj") and kind != "embedding":
        args += ["--mmproj", shlex.quote(model["mmproj"][0]), "--image-max-tokens", "1024"]
    extra = (prefab.get("extra") or "").strip()
    cmd = parse_command(" ".join(env + args + ([extra] if extra else [])))
    return to_odysseus(cmd, port=port or prefab.get("port") or 8080, host=prefab.get("host") or "0.0.0.0")


def library_with_profiles(models: list[dict], profiles: list[dict]) -> list[dict]:
    """Attach the saved-profile count to each scanned model (joined on the llama-swap ID)."""
    counts: dict[str, int] = {}
    for p in profiles:
        counts[p.get("model_id") or ""] = counts.get(p.get("model_id") or "", 0) + 1
    return [{**m, "profiles": counts.get(m["id"], 0)} for m in models]


def model_id_for_command(command: str) -> str:
    try:
        model = parse_command(command).summary()["model"]
    except ValueError:
        return ""
    return swap_model_id(model) if model.lower().endswith(".gguf") else ""


__all__ = ["scan_models", "prefab_command", "quant_of", "kind_of", "library_with_profiles",
           "model_id_for_command"]
