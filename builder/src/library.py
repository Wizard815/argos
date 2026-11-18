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
import logging
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


# ----- helper files: what is added onto a main model's command, not run on their own -------------------------------
# A vision projector (mmproj-*.gguf, --mmproj) and an MTP head: a small GGUF with a model's multi-token-prediction
# layers alone, used to draft tokens for it. A file named "...mtp..." can also be a whole model with its MTP layers
# inside (Qwen3.6-35B-A3B-MTP), so a head is told apart by its header: a few layers only.
HELPERS_FILE = ".argos-helpers.json"
MTP_NAME = re.compile(r"(?i)(^|[^a-z])(mtp|nextn)([^a-z]|$)")
MTP_HEAD_MAX_LAYERS = 4
_NAME_NOISE = re.compile(r"(?i)(-\d{5}-of-\d{5})|\.gguf$|(^|[-_.])(mtp|nextn|draft|head|mmproj|ud|imatrix|i?q\d[\w]*|bf16|f16|f32|fp16)(?=$|[-_.])")


def _name_key(path: str) -> str:
    """A file's model name without quant, shard, "mtp" and separators: what a helper and its model share."""
    base = os.path.basename(path)
    for _ in range(3):  # the noise words can sit next to each other
        base = _NAME_NOISE.sub("", base)
    return re.sub(r"[^a-z0-9]", "", base.lower())


def is_mtp_head(path: str, layers: Optional[int] = None) -> bool:
    """A separate MTP head: "mtp" (or "nextn") in its name and only a few layers in its header."""
    if not MTP_NAME.search(os.path.basename(path)):
        return False
    if layers is None:
        from .gguf import read_arch
        arch = read_arch(path) or {}
        layers = arch.get("layers")
    return bool(layers) and layers <= MTP_HEAD_MAX_LAYERS


def read_helpers_file(models_dir: str) -> dict:
    """{model path relative to the models folder: {"mtp": rel path or "", "mmproj": rel path or ""}}: helpers attached
    by hand (Models -> a model -> Helpers). "" detaches one that was paired on its own."""
    import json
    try:
        with open(os.path.join(models_dir, HELPERS_FILE), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def helper_files(models_dir: str) -> dict:
    """Every file that can be added onto a model, relative to the models folder: {"mmproj": [...], "mtp": [...]}."""
    out: dict[str, list[str]] = {"mmproj": [], "mtp": []}
    if not models_dir or not os.path.isdir(models_dir):
        return out
    for root, dirs, files in os.walk(models_dir, followlinks=True):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for f in files:
            if not f.lower().endswith(".gguf") or f.startswith("."):
                continue
            path = os.path.join(root, f)
            rel = os.path.relpath(path, models_dir)
            if is_mmproj(f):
                out["mmproj"].append(rel)
            elif is_mtp_head(path):
                out["mtp"].append(rel)
    return {k: sorted(v) for k, v in out.items()}


def write_helpers_file(models_dir: str, data: dict) -> None:
    import json
    tmp = os.path.join(models_dir, HELPERS_FILE + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({k: v for k, v in sorted(data.items()) if v}, f, indent=2)
    os.replace(tmp, os.path.join(models_dir, HELPERS_FILE))
    forget_scans()


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
log = logging.getLogger("argos.library")
SCAN_TTL_S = 60.0
_scan_lock = threading.RLock()
_scans: dict[tuple[str, str], tuple[float, list[dict]]] = {}
_rebuilding: set[tuple[str, str]] = set()


def forget_scans() -> None:
    """The models folder changed: the next scan reads it again."""
    with _scan_lock:
        _scans.clear()


def _cached(kind: str, fn: Callable[[str], list[dict]], models_dir: str) -> list[dict]:
    """The scan, from cache, without ever making a request wait for a walk.

    This used to re-walk inside the lock whenever the entry was older than
    SCAN_TTL_S. On a share where the walk takes longer than that TTL it means
    every single request re-walks, and every other thread queues behind the
    lock while it does - so one slow folder stopped the whole app. The pool
    filled, the task queue grew without limit, and the page timed out.

    Now an entry past its TTL is served as it is and rebuilt by one background
    thread. Only a scan with nothing to serve - the first ask, or the one right
    after forget_scans() - waits, which is what keeps a delete or a rescan
    visible immediately.
    """
    key = (kind, models_dir)
    with _scan_lock:
        hit = _scans.get(key)
        if hit is None:
            value = fn(models_dir)
            _scans[key] = (time.time(), value)
            return copy.deepcopy(value)
        value = copy.deepcopy(hit[1])
        start = time.time() - hit[0] > SCAN_TTL_S and key not in _rebuilding
        if start:
            _rebuilding.add(key)
    if start:
        # Outside the lock, so readers are never held up by a walk.
        threading.Thread(target=_rebuild, args=(key, fn, models_dir), daemon=True,
                         name="library-scan").start()
    return value


def _rebuild(key: tuple[str, str], fn: Callable[[str], list[dict]], models_dir: str) -> None:
    try:
        value = fn(models_dir)
    except Exception:  # noqa: BLE001 - a failed walk keeps the last good list
        log.exception("library scan failed; keeping the last list")
        value = None
    finally:
        with _scan_lock:
            _rebuilding.discard(key)
    if value is not None:
        with _scan_lock:
            _scans[key] = (time.time(), value)


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
                group = by_dir.setdefault(root, {"models": [], "mmproj": [], "mtp": []})
                path = os.path.join(root, f)
                group["mmproj" if is_mmproj(f) else "mtp" if is_mtp_head(path) else "models"].append(path)
    all_heads = [p for g in by_dir.values() for p in g["mtp"]]
    attached = read_helpers_file(models_dir)

    def heads_for(path: str, root: str) -> list[str]:
        """Its MTP heads: in its own folder, else ones named like it anywhere under the models folder."""
        here = by_dir[root]["mtp"]
        key = _name_key(path)
        if len(here) == 1 and len(by_dir[root]["models"]) == 1:
            return list(here)
        pool = here or all_heads
        same = [h for h in pool if _name_key(h) and _name_key(h) == key]  # GLM-4.6-MTP for GLM-4.6: any length
        if same:
            return same
        # a longer or shorter name of the same model; at least 6 letters, so "qwen-mtp" does not take every Qwen
        return [h for h in pool if len(_name_key(h)) >= 6 and len(key) >= 6 and (key.startswith(_name_key(h)) or _name_key(h).startswith(key))]

    def by_hand(path: str, which: str):
        """A helper attached (path) or detached ("") by hand, or None when nothing was said."""
        rel = os.path.relpath(path, models_dir)
        if rel not in attached or which not in (attached[rel] or {}):
            return None
        v = attached[rel][which]
        return os.path.join(models_dir, v) if v else ""

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
            name = display_name(path, re.sub(r"\.gguf$", "", os.path.basename(path), flags=re.IGNORECASE))
            vision = by_hand(path, "mmproj")
            its_mmproj = mmprojs if vision is None else ([vision] if vision else [])
            head = by_hand(path, "mtp")
            heads = heads_for(path, root) if head is None else ([head] if head else [])
            out.append({
                "id": swap_model_id(path), "name": name, "path": path,
                "dir": os.path.relpath(root, models_dir), "size": size, "shards": shards,
                "quant": quant_of(path), "mmproj": its_mmproj, "kind": kind_of(path, bool(its_mmproj)),
                "mtp_head": heads, "helpers_by_hand": sorted(k for k in ("mtp", "mmproj") if by_hand(path, k) is not None),
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


# file names that say nothing about the model: the folder names it instead
GENERIC_STEM = re.compile(r"(?i)^(model|ggml-model|pytorch_model|diffusion_pytorch_model|consolidated|weights|checkpoint|"
                          r"model[-_.](fp16|bf16|f16|fp32|q\d\w*)|model-\d{5}-of-\d{5})$")
HF_REPO = re.compile(r"(?:^|[\\/])models--([^\\/]+?)--([^\\/]+)(?:[\\/]|$)")


def display_name(path: str, stem: str) -> str:
    """A readable name for a model file.

    Hugging Face's cache stores files as models--<org>--<repo>/snapshots/<hash>/model.safetensors, which
    read as a file called "model" in a folder named by a hash. The repo name is the model's name there,
    and a generic file name anywhere else takes its folder's name."""
    m = HF_REPO.search(path)
    if m:
        repo = m.group(2)
        return repo if GENERIC_STEM.match(stem) or stem.lower() in repo.lower() else stem
    if GENERIC_STEM.match(stem):
        parent = os.path.basename(os.path.dirname(path.rstrip("/\\")))
        return parent or stem
    return stem


def _media_entry(path: str, models_dir: str, kind: tuple[str, str], size: int, fmt: str) -> dict:
    name = os.path.basename(path.rstrip("/\\"))
    stem = display_name(path, re.sub(r"\.(gguf|bin|safetensors|ckpt|onnx|pt|pth)$", "", name, flags=re.IGNORECASE))
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
    if model.get("mtp_head") and kind not in ("embedding", "reranker", "mtp"):
        # a separate MTP head drafts for this model: the template says how this build takes one
        env += _env_lines(prefab.get("mtp_env", ""))
        args += [a.replace("{file}", shlex.quote(model["mtp_head"][0])) for a in shlex.split(prefab.get("mtp_args") or "")]
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


NAMES_KEY = "_model_names"


def with_names(models: list[dict], names: dict) -> list[dict]:
    """Your own names for models (Models -> Rename) over the ones taken from the files. Only the shown name changes:
    the file, its id and its profiles stay as they are."""
    out = []
    for m in models:
        own = (names or {}).get(m["id"])
        out.append({**m, "name": own, "file_name": m["name"], "renamed": True} if own else m)
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
