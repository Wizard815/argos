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
         "attention.value_length", "embedding_length", "full_attention_interval", "context_length")


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
    return {"arch": arch, "layers": int(layers), "heads": int(heads), "kv_heads": int(kv_heads),
            "key_len": int(key_len), "value_len": int(value_len), "attn_layers": int(attn_layers),
            "train_ctx": int(found.get("context_length") or 0)}


__all__ = ["read_arch"]
