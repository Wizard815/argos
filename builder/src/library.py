"""Local model library and per-model prefab commands.

``scan_models`` walks the models directory the way Argos's
modelscan does: every ``*.gguf`` except non-first shards of a split file and
the multimodal projectors, which are paired with the models next to them
(Odysseus looks for ``mmproj-*.gguf`` beside the model the same way). IDs are
Argos's, so a profile saved here lines up with the model Argos
generated from the same file.

``scan_media`` finds the audio and image models in the same folder (whisper,
TTS voices, Stable Diffusion, Flux and their VAEs and text encoders). They are
listed so you can see what you have; llama-server does not run them, so they
get no prefab, profile or launch.

``prefab_command`` is the starting point the Models view offers for each
file: the user's defaults from Settings, adjusted for the kind of model.
"""
from __future__ import annotations

import copy
import os
import re
import shlex
import threading
import time
from typing import Callable, Optional

from .command import LaunchCommand, parse_command, swap_model_id, to_odysseus

SHARD = re.compile(r"-(\d{5})-of-(\d{5})\.gguf$", re.IGNORECASE)
QUANT = re.compile(
    r"(?i)(?<![A-Za-z0-9])((?:UD-)?(?:I?Q\d(?:_[A-Z0-9]+)*|BF16|F16|F32|MXFP4(?:_MOE)?))(?![A-Za-z0-9])")
EMBED = re.compile(r"(?i)embed|(?<![a-z])(bge|e5|gte)-")
RERANK = re.compile(r"(?i)rerank")
VISION = re.compile(r"(?i)(-vl|vl-|vision|llava|pixtral|minicpm-v)")

# Audio and image models: matched on the file name and the folders above it.
STT = re.compile(r"(?i)whisper|ggml-(tiny|base|small|medium|large)|parakeet|canary-|moonshine|wav2vec|sensevoice|voxtral")
TTS = re.compile(r"(?i)kokoro|piper|outetts|orpheus|xtts|(?<![a-z])bark|(?<![a-z])vits|(?<![a-z])tts(?![a-z])|f5-tts|chatterbox|(?<![a-z])dia-\d")
IMAGE_PART = re.compile(r"(?i)(?<![a-z])(vae|ae|clip_[lg]|clip-vit|t5xxl|umt5|text_encoder|llava_llama3)(?![a-z])")
VIDEO = re.compile(r"(?i)(?<![a-z])wan2?[._-]?\d|hunyuan-?video|ltx-?video|cogvideo|mochi")
IMAGE = re.compile(r"(?i)stable-?diffusion|(?<![a-z])sd[_-]?(xl|3|v?1[._-]5|2[._-]1|turbo)|sdxl|flux|z-image|hidream|qwen-image|chroma|lumina|"
                   r"pixart|kolors|playground-v2|juggernaut|dreamshaper|realvis|animagine|pony|illustrious|noobai|stable-cascade|controlnet")
MEDIA_EXT = (".gguf", ".bin", ".safetensors", ".ckpt", ".onnx", ".pt", ".pth")


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


def media_kind(rel_path: str) -> Optional[tuple[str, str]]:
    """``(kind, task)`` for an audio or image model file, or None for anything else.

    kind is "audio" or "image"; task says what it does: speech-to-text, text-to-speech, image, video,
    or vae / text encoder for the parts an image model loads beside its main file.
    """
    p = rel_path.replace("\\", "/").lower()
    name = p.rsplit("/", 1)[-1]
    if STT.search(p):
        return "audio", "speech-to-text"
    if TTS.search(p):
        return "audio", "text-to-speech"
    is_image = IMAGE.search(p) or VIDEO.search(p)
    if IMAGE_PART.search(name) and (is_image or not name.endswith(".gguf")):
        part = re.search(r"(?i)vae|(?<![a-z])ae(?![a-z])", name)
        return "image", "vae" if part else "text encoder"
    if VIDEO.search(p):
        return "image", "video"
    if is_image:
        return "image", "image"
    return None


# Walking the models folder is cheap on a local disk and slow on a network or FUSE share (Unraid's
# /mnt/user), where every listing and stat is a round trip. A page load asks for the library from a dozen
# places, so the walk is shared: one at a time, kept for SCAN_TTL_S, and dropped as soon as this app changes
# the folder (a download finishing, a delete, a rescan).
SCAN_TTL_S = 60.0
_scan_lock = threading.RLock()
_scans: dict[tuple[str, str], tuple[float, list[dict]]] = {}


def forget_scans() -> None:
    """The models folder changed: the next scan reads it again."""
    with _scan_lock:
        _scans.clear()


def _cached(kind: str, fn: Callable[[str], list[dict]], models_dir: str) -> list[dict]:
    key = (kind, models_dir)
    with _scan_lock:
        hit = _scans.get(key)
        if hit is None or time.time() - hit[0] > SCAN_TTL_S:
            hit = (time.time(), fn(models_dir))
            _scans[key] = hit
        return copy.deepcopy(hit[1])


def scan_models(models_dir: str) -> list[dict]:
    """GGUF models llama-server can run (cached; see ``forget_scans``). Audio and image models are in ``scan_media``."""
    return _cached("models", _scan_models, models_dir)


def scan_media(models_dir: str) -> list[dict]:
    """Audio and image models, listed only (cached; see ``forget_scans``)."""
    return _cached("media", _scan_media, models_dir)



def _scan_models(models_dir: str) -> list[dict]:
    """GGUF models llama-server can run. Audio and image models are in ``scan_media``."""
    if not models_dir or not os.path.isdir(models_dir):
        return []
    by_dir: dict[str, dict[str, list[str]]] = {}
    for root, dirs, files in os.walk(models_dir, followlinks=True):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for f in files:
            if f.lower().endswith(".gguf") and not f.startswith(".")                     and not media_kind(os.path.relpath(os.path.join(root, f), models_dir)):
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


def _scan_media(models_dir: str) -> list[dict]:
    """Audio and image models, listed only. A diffusers folder (one with model_index.json) is one model."""
    if not models_dir or not os.path.isdir(models_dir):
        return []
    out = []
    for root, dirs, files in os.walk(models_dir, followlinks=True):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        rel_root = os.path.relpath(root, models_dir)
        if "model_index.json" in files and root != models_dir:
            kind = media_kind(rel_root) or ("image", "image")
            size = sum(_size(os.path.join(r, f)) for r, _, fs in os.walk(root) for f in fs)
            out.append(_media_entry(root, models_dir, kind, size, "diffusers"))
            dirs[:] = []
            continue
        for f in files:
            if f.startswith(".") or not f.lower().endswith(MEDIA_EXT):
                continue
            path = os.path.join(root, f)
            kind = media_kind(os.path.relpath(path, models_dir))
            if not kind:
                continue
            m = SHARD.search(path)
            if m and m.group(1) != "00001":
                continue
            ext = os.path.splitext(f)[1].lower().lstrip(".")
            fmt = "ggml" if ext == "bin" and f.lower().startswith("ggml-") else ext
            out.append(_media_entry(path, models_dir, kind, _size(path), fmt))
    return sorted(out, key=lambda m: (m["kind"], m["name"].lower()))


def _media_entry(path: str, models_dir: str, kind: tuple[str, str], size: int, fmt: str) -> dict:
    name = os.path.basename(path.rstrip("/\\"))
    stem = re.sub(r"\.(gguf|bin|safetensors|ckpt|onnx|pt|pth)$", "", name, flags=re.IGNORECASE)
    return {"id": swap_model_id(stem), "name": stem, "path": path,
            "dir": os.path.relpath(os.path.dirname(path) if fmt != "diffusers" else path, models_dir),
            "size": size, "shards": 1, "quant": quant_of(name), "mmproj": [], "kind": kind[0], "task": kind[1],
            "format": fmt, "listed_only": True, "profiles": 0, "mtime": _mtime(path)}


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
    # A prefab is launched as a process, so the program is part of it. Empty
    # keeps the usual llama-server; a build whose llama.cpp lives in another
    # container sets this to its launcher instead, since there is no
    # llama-server here to run (the wrapper script takes the backend word and
    # passes the rest through).
    program = (prefab.get("program") or "").strip() or "llama-server"
    args = [program, "--model", shlex.quote(model["path"]),
            "--host", prefab.get("host") or "0.0.0.0", "--port", str(port or prefab.get("port") or 8080)]
    if prefab.get("ngl"):
        args += ["-ngl", str(prefab["ngl"])]
    ctx = str(prefab.get("ctx") or "")
    if kind in ("embedding", "reranker"):
        # one slot: the builder sends batches one at a time, and parallel slots on
        # gfx906 hit "MUL_MAT ... unspecified launch failure" under a burst
        args += ["-c", str(min(int(ctx or 8192), 8192)), "--parallel", "1"]
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


def prefab_with_launcher(prefab: dict, launcher: str) -> dict:
    """The prefab a command is built from: an unset program takes the launcher the
    default server implies.

    The program is part of the prefab because the prefab is what gets run, so a build
    whose llama.cpp lives in another container binds Launch to that host by naming
    its server rather than by setting the program on every prefab by hand.
    """
    out = dict(prefab or {})
    if not (out.get("program") or "").strip() and launcher:
        out["program"] = launcher
    return out


def library_with_profiles(models: list[dict], profiles: list[dict]) -> list[dict]:
    """Attach the saved-profile count to each scanned model (joined on the Argos ID)."""
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


__all__ = ["scan_models", "scan_media", "forget_scans", "media_kind", "prefab_command", "quant_of", "kind_of",
           "library_with_profiles", "model_id_for_command"]
