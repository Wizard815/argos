"""Read the few GGUF header fields needed to estimate KV-cache size.

Only the metadata block is parsed, and parsing stops as soon as every field we
want has been seen, so a multi-gigabyte model on a network share costs a few
kilobytes of reads. Anything unreadable returns ``None`` and the caller falls
back to a rougher estimate.
"""
from __future__ import annotations

import mmap
import struct
from typing import Optional

MAGIC = b"GGUF"

# value type -> (struct format, size); strings and arrays are handled separately
_SCALARS = {0: ("<B", 1), 1: ("<b", 1), 2: ("<H", 2), 3: ("<h", 2), 4: ("<I", 4), 5: ("<i", 4),
            6: ("<f", 4), 7: ("<?", 1), 10: ("<Q", 8), 11: ("<q", 8), 12: ("<d", 8)}
_STRING, _ARRAY = 8, 9

# arch-specific keys we care about, as suffixes after "<arch>."
_WANT = ("block_count", "attention.head_count", "attention.head_count_kv", "attention.key_length",
         "attention.value_length", "embedding_length", "full_attention_interval", "context_length",
         "attention.sliding_window", "attention.sliding_window_pattern")


_NEEDED = ("block_count", "attention.head_count", "embedding_length")


class _Reader:
    def __init__(self, buf):
        self.buf, self.pos = buf, 0

    def take(self, fmt: str, size: int):
        (v,) = struct.unpack_from(fmt, self.buf, self.pos)
        self.pos += size
        return v

    def string(self) -> str:
        n = self.take("<Q", 8)
        if n > 1 << 24:
            raise ValueError("string too long")
        s = bytes(self.buf[self.pos:self.pos + n]).decode("utf-8", "replace")
        self.pos += n
        return s

    def skip_value(self, vtype: int) -> None:
        if vtype in _SCALARS:
            self.pos += _SCALARS[vtype][1]
        elif vtype == _STRING:
            n = self.take("<Q", 8)
            self.pos += n
        elif vtype == _ARRAY:
            etype, count = self.take("<I", 4), self.take("<Q", 8)
            if etype in _SCALARS:
                self.pos += _SCALARS[etype][1] * count
            else:
                for _ in range(count):
                    self.skip_value(etype)
        else:
            raise ValueError(f"unknown value type {vtype}")

    def value(self, vtype: int):
        if vtype in _SCALARS:
            fmt, size = _SCALARS[vtype]
            return self.take(fmt, size)
        if vtype == _STRING:
            return self.string()
        if vtype == _ARRAY:
            etype, count = self.take("<I", 4), self.take("<Q", 8)
            if etype in _SCALARS and count <= 4096:  # per-layer head counts and the like
                fmt, size = _SCALARS[etype]
                return [self.take(fmt, size) for _ in range(count)]
            if etype in _SCALARS:
                self.pos += _SCALARS[etype][1] * count
            else:
                for _ in range(count):
                    self.skip_value(etype)
            return None
        self.skip_value(vtype)
        return None


def read_arch(path: str) -> Optional[dict]:
    """``{"arch", "layers", "heads", "kv_heads", "key_len", "value_len", "attn_layers", "train_ctx", "ftype"}`` or None.

    ``ftype`` is llama.cpp's ``general.file_type`` (the quant written into the file), or None.

    ``attn_layers`` is the number of layers that keep a KV cache: all of them, or
    one in ``full_attention_interval`` for hybrid models that mix in linear layers.
    """
    try:
        with open(path, "rb") as f, mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as buf:
            if bytes(buf[:4]) != MAGIC:
                return None
            rd = _Reader(buf)
            rd.pos = 4
            version = rd.take("<I", 4)
            if version < 2:
                return None
            rd.take("<Q", 8)  # tensor count
            n_kv = rd.take("<Q", 8)
            arch, found, ftype = "", {}, None
            for _ in range(n_kv):
                key = rd.string()
                vtype = rd.take("<I", 4)
                if key == "general.architecture":
                    arch = rd.value(vtype) or ""
                    continue
                if key == "general.file_type":
                    ftype = rd.value(vtype)
                    continue
                # The architecture keys come before the tokenizer's huge arrays.
                # Once we are at the tokenizer with what we need, stop reading.
                if key.startswith("tokenizer.") and all(k in found for k in _NEEDED):
                    break
                suffix = key[len(arch) + 1:] if arch and key.startswith(arch + ".") else None
                if suffix in _WANT:
                    found[suffix] = rd.value(vtype)
                else:
                    rd.skip_value(vtype)
    except (OSError, ValueError, struct.error):
        return None
    out = _summarize(arch, found)
    if out is not None:
        out["ftype"] = int(ftype) if isinstance(ftype, int) else None
    return out


def read_decision_type(path: str) -> str:
    """A llama.cpp decision model's ``<arch>.decision.type`` ("openjev", "lev", "kev", "laya", ...), "" for any
    other model. Decision models answer POST /v1/systemone (classify, score, choose)."""
    try:
        with open(path, "rb") as f, mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as buf:
            if bytes(buf[:4]) != MAGIC:
                return ""
            rd = _Reader(buf)
            rd.pos = 4
            if rd.take("<I", 4) < 2:
                return ""
            rd.take("<Q", 8)
            for _ in range(rd.take("<Q", 8)):
                key, vtype = rd.string(), rd.take("<I", 4)
                if key.endswith(".decision.type"):
                    v = rd.value(vtype)
                    return str(v or "decision") if not isinstance(v, int) else f"type-{v}"
                if key.startswith("tokenizer."):
                    return ""
                rd.skip_value(vtype)
    except (OSError, ValueError, struct.error):
        pass
    return ""


def read_architecture(path: str) -> str:
    """Only ``general.architecture`` ("llama", "clip" for a projector), "" for a missing or unreadable file."""
    try:
        with open(path, "rb") as f, mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as buf:
            if bytes(buf[:4]) != MAGIC:
                return ""
            rd = _Reader(buf)
            rd.pos = 4
            if rd.take("<I", 4) < 2:
                return ""
            rd.take("<Q", 8)
            for _ in range(rd.take("<Q", 8)):
                key, vtype = rd.string(), rd.take("<I", 4)
                if key == "general.architecture":
                    return str(rd.value(vtype) or "")
                if key.startswith("tokenizer."):
                    return ""
                rd.skip_value(vtype)
    except (OSError, ValueError, struct.error):
        pass
    return ""


def _scalar_or_max(v):
    """A per-layer array collapses to its largest entry."""
    if isinstance(v, list):
        return max(v) if v else 0
    return v


def _summarize(arch: str, found: dict) -> Optional[dict]:
    layers, emb = found.get("block_count"), found.get("embedding_length")
    heads = _scalar_or_max(found.get("attention.head_count"))
    if not (arch and layers and heads and emb):
        return None
    kv = found.get("attention.head_count_kv")
    kv_heads = _scalar_or_max(kv) or heads
    key_len = found.get("attention.key_length") or emb // heads
    value_len = found.get("attention.value_length") or key_len
    interval = found.get("full_attention_interval")
    if isinstance(kv, list) and any(n == 0 for n in kv):
        attn_layers = sum(1 for n in kv if n)  # layers with 0 KV heads keep no cache
    elif interval and interval > 1:
        attn_layers = layers // interval
    else:
        attn_layers = layers
    # Sliding-window attention (gemma 2/3/4, cohere2): most layers keep only the last
    # ``window`` tokens, so the KV cache of those layers does not grow with the context.
    window = int(_scalar_or_max(found.get("attention.sliding_window")) or 0)
    pattern = found.get("attention.sliding_window_pattern")
    swa_layers = 0
    if window:
        if isinstance(pattern, list) and pattern:
            swa_layers = sum(1 for x in pattern if x)  # true = a sliding-window layer
        else:
            n = int(pattern) if isinstance(pattern, int) and pattern > 1 else 6  # llama.cpp's default for gemma
            swa_layers = int(layers) - int(layers) // n
        swa_layers = min(swa_layers, int(attn_layers))
    return {"arch": arch, "layers": int(layers), "heads": int(heads), "kv_heads": int(kv_heads),
            "key_len": int(key_len), "value_len": int(value_len), "attn_layers": int(attn_layers),
            "train_ctx": int(found.get("context_length") or 0), "swa_window": window, "swa_layers": swa_layers}


__all__ = ["read_arch"]


# ----- mixture-of-experts layout --------------------------------------------------------------------------------
# llama-server's --n-cpu-moe N keeps the expert weights of the first N layers in system RAM; these are those tensors
# (llama.cpp's own pattern: blk.<i>.ffn_(up|down|gate|gate_up)_(ch)exps). Shared experts (_shexp) stay on the GPU.
import os as _os
import re as _re

_EXPS = _re.compile(r"^blk\.(\d+)\.ffn_(?:up|down|gate|gate_up)_(?:ch)?exps\b")
_SPLIT = _re.compile(r"^(.*)-(\d{5})-of-(\d{5})\.gguf$", _re.I)
_moe_cache: dict = {}


def _tensor_sizes(path: str) -> Optional[list]:
    """[(name, bytes)] for every tensor in one GGUF file, from the offsets in its tensor table."""
    with open(path, "rb") as f, mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as buf:
        if bytes(buf[:4]) != MAGIC:
            return None
        rd = _Reader(buf)
        rd.pos = 4
        if rd.take("<I", 4) < 2:
            return None
        n_tensors, n_kv = rd.take("<Q", 8), rd.take("<Q", 8)
        align = 32
        for _ in range(n_kv):
            key = rd.string()
            vtype = rd.take("<I", 4)
            if key == "general.alignment":
                align = int(rd.value(vtype) or 32)
            else:
                rd.skip_value(vtype)
        tensors = []
        for _ in range(n_tensors):
            name = rd.string()
            n_dims = rd.take("<I", 4)
            rd.pos += 8 * n_dims
            rd.take("<I", 4)  # type
            tensors.append((name, rd.take("<Q", 8)))
        data_start = (rd.pos + align - 1) // align * align
        end = len(buf) - data_start
    order = sorted(tensors, key=lambda t: t[1])
    sizes = {}
    for i, (name, off) in enumerate(order):
        nxt = order[i + 1][1] if i + 1 < len(order) else end
        sizes[name] = max(0, nxt - off)
    return [(n, sizes[n]) for n, _ in tensors]


def _parts(path: str) -> list:
    """Every file of a split model (model-00001-of-00003.gguf ...), or just this one."""
    m = _SPLIT.match(_os.path.basename(path))
    if not m:
        return [path]
    d, stem, total = _os.path.dirname(path), m.group(1), m.group(3)
    parts = [_os.path.join(d, f"{stem}-{i:05d}-of-{total}.gguf") for i in range(1, int(total) + 1)]
    return [p for p in parts if _os.path.exists(p)]


def read_moe(path: str) -> Optional[dict]:
    """The expert weights per layer of a mixture-of-experts model, or None for a dense model or an unreadable file:
    ``{"layers": block count, "moe_layers": [i, ...], "expert_bytes": {i: bytes}, "expert_total": bytes,
    "other_bytes": the rest of the weights}``. Cached per file and modification time."""
    try:
        parts = _parts(path)
        stamp = tuple((p, _os.path.getmtime(p), _os.path.getsize(p)) for p in parts)
    except OSError:
        return None
    if path in _moe_cache and _moe_cache[path][0] == stamp:
        return _moe_cache[path][1]
    per_layer: dict[int, int] = {}
    other, layers = 0, 0
    try:
        for p in parts:
            for name, size in _tensor_sizes(p) or []:
                m = _EXPS.match(name)
                if m:
                    i = int(m.group(1))
                    per_layer[i] = per_layer.get(i, 0) + size
                else:
                    other += size
                b = _re.match(r"^blk\.(\d+)\.", name)
                if b:
                    layers = max(layers, int(b.group(1)) + 1)
    except (OSError, ValueError, struct.error):
        return None
    out = None
    if per_layer:
        out = {"layers": layers, "moe_layers": sorted(per_layer), "expert_bytes": per_layer,
               "expert_total": sum(per_layer.values()), "other_bytes": other}
    _moe_cache[path] = (stamp, out)
    return out


def cpu_expert_bytes(moe: Optional[dict], n_cpu_moe: int) -> int:
    """Bytes --n-cpu-moe N moves to system RAM: the experts of layers 0 .. N-1."""
    if not moe or n_cpu_moe <= 0:
        return 0
    return sum(b for i, b in moe["expert_bytes"].items() if i < n_cpu_moe)
