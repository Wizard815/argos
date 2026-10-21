"""The models the Schedule view lists, each with a size estimate.

A row is anything Argos can load: a GGUF in the library (its modelscan id)
or a saved profile (``<model-id>--<label>``, how the Odysseus integration names
variants). Every profile is its own row because its context size and cache type
change how much VRAM it takes.
"""
from __future__ import annotations

import os
import re
from typing import Optional

from .command import parse_command
from .cookbook import variant_id
from .gguf import read_arch
from .library import SHARD, prefab_command
from .scheduler import GIB, KV_COMPARE, effective_size, estimate_gib, kv_gib

_cache: dict[tuple, object] = {}


def _mtime(path: str) -> float:
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0.0


def file_bytes(path: str) -> int:
    """Size of a model file; every shard of a split GGUF is counted. 0 when it is not on this host."""
    m = SHARD.search(path)
    paths = [path]
    if m:
        total = int(m.group(2))
        paths = [SHARD.sub(f"-{i:05d}-of-{m.group(2)}.gguf", path) for i in range(1, total + 1)]
    try:
        return sum(os.path.getsize(p) for p in paths)
    except OSError:
        return 0


def arch_of(path: str) -> Optional[dict]:
    """GGUF header facts, cached until the file changes (a header read over a network share is not free)."""
    key = ("arch", path, _mtime(path))
    if key not in _cache:
        _cache[key] = read_arch(path)
    return _cache[key]  # type: ignore[return-value]


CTX_COMPARE = (8192, 32768, 65536, 131072, 262144)


def estimate_command(command: str, compare: bool = False) -> dict:
    """What a launch command needs in VRAM: model file + KV cache + buffers.

    With ``compare``, also the KV cache alone for each cache type at a few context lengths, so the
    builder can show "q8_0 at 128K is X GiB, turbo3 is Y".
    """
    try:
        s = parse_command(command).summary()
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    path = s.get("model") or ""
    weights = file_bytes(path) if path.lower().endswith(".gguf") else 0
    if not weights:
        return {"ok": False, "error": f"model file not found: {path}" if path else "no --model in the command", "path": path}
    extra = sum(file_bytes(p) for p in (s.get("mmproj"), s.get("model_draft")) if p)
    arch = arch_of(path)
    ctx = s.get("ctx_size") or (arch or {}).get("train_ctx") or 4096
    ck, cv = s.get("cache_type_k") or "", s.get("cache_type_v") or ""
    out = {"ok": True, "path": path, "ctx": ctx, "cache_k": ck or "f16", "cache_v": cv or ck or "f16",
           "train_ctx": (arch or {}).get("train_ctx"), "extra_gib": round(extra / GIB, 2),
           **estimate_gib(weights, arch, ctx, ck, cv, extra)}
    if compare and arch:
        ctxs = sorted({*(c for c in CTX_COMPARE if c <= max(ctx, (arch or {}).get("train_ctx") or ctx)), ctx})
        out["compare"] = {"ctx": ctxs, "types": [{"type": t, "kv_gib": [kv_gib(arch, c, t, t) for c in ctxs]} for t in KV_COMPARE]}
    return out


def _row(row_id: str, name: str, kind: str, command: str, plan: dict, known: Optional[set[str]],
         extra: dict) -> tuple[dict, str]:
    try:
        s = parse_command(command).summary()
    except ValueError:
        s = {}
    path = s.get("model") or ""
    weights = file_bytes(path) if path.lower().endswith(".gguf") else 0
    extra_bytes = sum(file_bytes(p) for p in (s.get("mmproj"), s.get("model_draft")) if p)
    arch = arch_of(path) if weights else None
    ctx = s.get("ctx_size") or (arch or {}).get("train_ctx") or 4096
    est = estimate_gib(weights, arch, ctx, s.get("cache_type_k") or "", s.get("cache_type_v") or "",
                       extra_bytes) if weights else None
    size, source = effective_size(plan, row_id, est["gib"] if est else 0.0)
    row = {
        "id": row_id, "name": name, "kind": kind, "path": path, "ctx": ctx,
        "cache_k": s.get("cache_type_k") or "", "cache_v": s.get("cache_type_v") or "",
        "file_gib": round(weights / GIB, 2), "estimate": est,
        # a .gguf the command names that is not on disk: the model cannot start, and it has no size
        "missing": bool(path.lower().endswith(".gguf") and not weights), "plan_gib": plan["sizes"].get(row_id),
        "measured": plan["measured"].get(row_id), "effective_gib": round(size, 2), "source": source,
        "persistent": row_id in plan["persistent"], "known": None if known is None else row_id in known,
        **extra,
    }
    return row, command


def build_rows(library: list[dict], presets: list[dict], plan: dict, prefab: dict,
               known: Optional[set[str]] = None) -> tuple[list[dict], dict[str, str]]:
    """Rows and the launch command for each (what Measure launches)."""
    rows: list[dict] = []
    commands: dict[str, str] = {}
    seen: set[str] = set()
    for pr in presets:
        vid = variant_id(pr)
        if not vid or vid in seen:
            continue
        seen.add(vid)
        fields = pr.get("fields") or {}
        base = re.sub(r"--.*$", "", vid)
        row, cmd = _row(vid, pr.get("label") or vid, "profile", pr.get("cmd") or "", plan, known,
                        {"base_id": base, "profile_id": fields.get("profile_id") or "",
                         "origin": "argos" if fields.get("source") in ("argos", "profilebuilder") else "odysseus",
                         "model_kind": "profile"})
        rows.append(row)
        commands[vid] = cmd
    for m in library:
        if m["id"] in seen:
            continue
        seen.add(m["id"])
        cmd = prefab_command(m, prefab).to_string()
        row, cmd = _row(m["id"], m["name"], "model", cmd, plan, known,
                        {"base_id": m["id"], "profile_id": "", "origin": "library", "model_kind": m.get("kind", "chat")})
        rows.append(row)
        commands[m["id"]] = cmd
    return rows, commands


def plan_sizes(rows: list[dict]) -> dict[str, float]:
    """id -> GiB for every row that has a size, which is what the plan exports."""
    return {r["id"]: r["effective_gib"] for r in rows if r["effective_gib"] > 0}


__all__ = ["build_rows", "plan_sizes", "file_bytes", "arch_of"]
