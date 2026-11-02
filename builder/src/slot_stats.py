"""Each llama-server slot (ID) on its own, read from the model's log.

A model run with ``-np 4`` has slots 0-3, and llama.cpp names the slot on every line it prints about a request:

    10.37.538.452 I slot print_timing: id  0 | task 4451 | prompt eval time =  954.33 ms /  192 tokens (..., 201.19 tokens per second)
    10.37.538.460 I slot print_timing: id  0 | task 4451 |        eval time = 20923.41 ms /  266 tokens (..., 12.67 tokens per second)
    10.37.541.632 I slot      release: id  0 | task 4451 | stop processing: n_tokens = 45004, truncated = 0

so every number the Logs page shows for the model can be split by slot: its requests, prefill and decode speeds,
how full its context is, and how much of the model's time it took. The slots of one model share its GPUs: llama.cpp
puts all of them into each batch. A slot's "share" is the time it spent reading prompts and writing answers out of
all its slots' time - how the model's work split between them, not a GPU percentage measured per slot.

The leading timestamp is minutes.seconds.milliseconds.microseconds since the server started; ``minutes`` limits the
reading to that much of the log's own clock, counted back from its last line.
"""
from __future__ import annotations

import re
from typing import Optional

_TS = re.compile(r"^\s*(\d+)\.(\d{2})\.(\d{3})\.(\d{3})\s")
_SLOT = re.compile(r"\bid\s+(\d+)\s*\|\s*task\s+(-?\d+)\s*\|(.*)$")
_PROMPT = re.compile(r"prompt eval time\s*=\s*([\d.]+)\s*ms\s*/\s*(\d+)\s*tokens.*?([\d.]+)\s*tokens per second")
_EVAL = re.compile(r"(?<!prompt )\beval time\s*=\s*([\d.]+)\s*ms\s*/\s*(\d+)\s*tokens.*?([\d.]+)\s*tokens per second")
_GEN = re.compile(r"n_gen\s*=\s*(\d+),\s*tg\s*=\s*([\d.]+)")
_PREFILL = re.compile(r"prompt processing, n_tokens\s*=\s*(\d+), progress\s*=\s*([\d.]+), t\s*=\s*([\d.]+)\s*s\s*/\s*([\d.]+)")
_RELEASE = re.compile(r"stop processing: n_tokens\s*=\s*(\d+)")
_NCTX = re.compile(r"n_ctx(?:_slot)?\s*=\s*(\d+)")


def _seconds(line: str) -> Optional[float]:
    m = _TS.match(line)
    if not m:
        return None
    return int(m.group(1)) * 60 + int(m.group(2)) + int(m.group(3)) / 1000 + int(m.group(4)) / 1e6


def _slot(slots: dict, sid: int) -> dict:
    return slots.setdefault(sid, {
        "id": sid, "state": "idle", "task": None, "requests": 0, "n_ctx": None, "ctx_used": None, "last_t": None,
        "prefill": {"last": None, "tokens": 0, "ms": 0.0, "low": None, "high": None},
        "decode": {"last": None, "tokens": 0, "ms": 0.0, "low": None, "high": None},
        "progress": None, "busy_ms": 0.0, "_gen": {}})


def _speed(d: dict, ms: float, tokens: int, tps: float) -> None:
    d["last"] = tps
    d["tokens"] += tokens
    d["ms"] += ms
    d["low"] = tps if d["low"] is None else min(d["low"], tps)
    d["high"] = tps if d["high"] is None else max(d["high"], tps)


def parse(text: str, minutes: Optional[float] = None) -> list[dict]:
    """Every slot the log mentions, by ID: what it did, and what it is doing as of the log's last line."""
    lines = (text or "").splitlines()
    since = None
    if minutes:
        last = next((t for t in (_seconds(x) for x in reversed(lines)) if t is not None), None)
        since = last - minutes * 60 if last is not None else None
    slots: dict[int, dict] = {}
    for line in lines:
        m = _SLOT.search(line)
        if not m:
            continue
        sid, task, rest = int(m.group(1)), int(m.group(2)), m.group(3)
        s = _slot(slots, sid)
        t = _seconds(line)
        if t is not None:
            s["last_t"] = t
        n = _NCTX.search(rest)
        if n and ("new slot" in rest or "n_ctx_slot" in rest):
            s["n_ctx"] = int(n.group(1))
        if "processing task" in rest:
            s.update(state="prefill", task=task, progress=None)
            continue
        r = _RELEASE.search(rest)
        if r:
            s.update(state="idle", task=None, progress=None, ctx_used=int(r.group(1)))
            if since is None or t is None or t >= since:
                s["requests"] += 1
                gen = s["_gen"].pop(task, None)  # a build with no timing block: its last n_gen line
                if gen and not s.get("_timed", {}).get(task):
                    s["decode"]["last"] = gen[1]
                    s["decode"]["low"] = gen[1] if s["decode"]["low"] is None else min(s["decode"]["low"], gen[1])
                    s["decode"]["high"] = gen[1] if s["decode"]["high"] is None else max(s["decode"]["high"], gen[1])
            continue
        p = _PREFILL.search(rest)
        if p:
            s.update(state="prefill", task=task, progress=float(p.group(2)))
            continue
        g = _GEN.search(rest)
        if g:
            s.update(state="generating", task=task, progress=None)
            s["_gen"][task] = (int(g.group(1)), float(g.group(2)))
            continue
        if since is not None and t is not None and t < since:
            continue
        pm = _PROMPT.search(rest)
        if pm:
            _speed(s["prefill"], float(pm.group(1)), int(pm.group(2)), float(pm.group(3)))
            s["busy_ms"] += float(pm.group(1))
            s["state"] = "generating"
            continue
        em = _EVAL.search(rest)
        if em:
            _speed(s["decode"], float(em.group(1)), int(em.group(2)), float(em.group(3)))
            s["busy_ms"] += float(em.group(1))
            s.setdefault("_timed", {})[task] = True
    total = sum(s["busy_ms"] for s in slots.values()) or 0.0
    out = []
    for sid in sorted(slots):
        s = slots[sid]
        s.pop("_gen", None)
        s.pop("_timed", None)
        for k in ("prefill", "decode"):
            d = s[k]
            d["avg"] = round(d["tokens"] / (d["ms"] / 1000), 2) if d["ms"] else None
        s["share"] = round(s["busy_ms"] / total, 4) if total else None
        s["busy_s"] = round(s.pop("busy_ms") / 1000, 1)
        out.append(s)
    return out


__all__ = ["parse"]
