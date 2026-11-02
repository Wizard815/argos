"""The memory's link graph: records joined by what they share, with no model to run.

Each record is linked to the things it names: a model, a profile of it, a flag, an environment variable, a GPU,
a kind of failure, the day it happened. Two records are alike when they share things, and the rarer the shared
thing the more it counts (a GGML_ASSERT links two crashes more than "GPU 0" does). Finding records like a
question or like another record is then a lookup in a table, cheap enough to do on every question, where the
embedding model has to read every record first. The embedding model still adds what links cannot (two
records that mean the same in different words); the two are fused in the memory's search.
"""
from __future__ import annotations

import math
import re
from datetime import datetime
from typing import Iterable, Optional

VARIANT = re.compile(r"\b([a-z0-9][a-z0-9._]*(?:-[a-z0-9._]+)+)--([a-z0-9][a-z0-9._-]*)", re.I)
FLAG = re.compile(r"(?<![\w-])(--?[a-z][a-z0-9-]{1,40})(?=[\s=]|$)")
ENV = re.compile(r"\b([A-Z][A-Z0-9]*_[A-Z0-9_]{2,})\b")
GPU = re.compile(r"\bGPUs? ?((?:\d+)(?:,\d+)*)\b")
TROUBLE = {
    "oom": re.compile(r"(?i)out of memory|\boom\b|cudaMalloc failed|failed to allocate"),
    "crash": re.compile(r"(?i)crash|GGML_ASSERT|segmentation fault|core dumped|exited (while starting|without)"),
    "evict": re.compile(r"(?i)\bevict"),
    "timeout": re.compile(r"(?i)timed? ?out|timeout"),
    "slow": re.compile(r"(?i)\bslow"),
    "error": re.compile(r"(?i)\berror\b|failed"),
}
# words of a flag that say nothing about the setup
SKIP_FLAGS = {"--port", "--host", "-m", "--model", "--alias", "--api-key", "-p"}


def entities(text: str, ts: Optional[float] = None, models: Iterable[str] = ()) -> set[str]:
    """The things a record names, as "kind:value"."""
    low = (text or "").lower()
    out: set[str] = set()
    for base, label in VARIANT.findall(low):
        out.add(f"model:{base}")
        out.add(f"profile:{base}--{label}")
    for m in models:
        if m and len(m) >= 6 and m.lower() in low:
            out.add(f"model:{m.lower()}")
    for f in FLAG.findall(text or ""):
        if f not in SKIP_FLAGS and not f.startswith("---"):
            out.add(f"flag:{f.lstrip('-')}")
    for e in ENV.findall(text or ""):
        out.add(f"env:{e}")
    for cards in GPU.findall(text or ""):
        for c in cards.split(","):
            out.add(f"gpu:{c}")
    for kind, rx in TROUBLE.items():
        if rx.search(text or ""):
            out.add(f"trouble:{kind}")
    if ts:
        out.add(f"day:{datetime.fromtimestamp(ts).strftime('%Y-%m-%d')}")
    return out


def query_entities(query: str, models: Iterable[str] = ()) -> set[str]:
    """What a question names: the same things, plus models named by the words of their name ("qwen coder")."""
    out = {e for e in entities(query, None, models) if not e.startswith("day:")}
    words = [w for w in re.split(r"[^a-z0-9.]+", (query or "").lower()) if len(w) >= 3]
    for m in models:
        parts = re.split(r"[^a-z0-9.]+", m.lower())
        hits = [w for w in words if any(p.startswith(w) for p in parts)]
        if len(hits) >= 2 or (hits and len(hits[0]) >= 5 and not hits[0].isdigit()):
            out.add(f"model:{m.lower()}")
    return out


def rank(wanted: set[str], links: list[tuple[int, str]], df: dict[str, int], total: int,
         exclude: Optional[int] = None) -> list[tuple[int, float, list[str]]]:
    """Records by what they share with ``wanted``: each shared thing counts by how rare it is (idf).
    ``links`` are (record id, entity) rows for the wanted entities; returns (id, score, shared things)."""
    score: dict[int, float] = {}
    shared: dict[int, list[str]] = {}
    for rid, ent in links:
        if rid == exclude or ent not in wanted:
            continue
        w = math.log(1 + total / max(1, df.get(ent, 1)))
        if ent.startswith("day:"):
            w *= 0.3  # the same day is a weak link
        score[rid] = score.get(rid, 0.0) + w
        shared.setdefault(rid, []).append(ent)
    return sorted(((rid, s, sorted(shared[rid])) for rid, s in score.items()), key=lambda x: -x[1])


__all__ = ["entities", "query_entities", "rank"]
