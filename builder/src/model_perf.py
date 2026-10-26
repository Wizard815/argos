"""Per-model performance by launch config: prefill and decode speed, and how parallel requests affect them.

A *config* is one distinct way the model was launched: its flags and env vars, with
the things that never change performance (port, host, API key, log options) left
out and the model file reduced to its name, so the same command from Argos's
container and from this app's container counts as one config.

Speeds come from two places:
  - Argos's per-request activity (``requests``), each request matched to the
    load (``model_sessions``) it ran under, which carries the command;
  - benchmarks run here (``runs``), which carry their own command.

Concurrency is measured, not assumed: Argos logs each request when it ends,
with its duration, so every request is an interval and the number of requests of
the same model overlapping it is how many were being served at once.
"""
from __future__ import annotations

import bisect
import hashlib
import json
import os
import time
from statistics import median
from typing import Any, Optional

from .command import parse_command
from .db import Store

# flags that do not change speed or memory
VOLATILE_FLAGS = {"port", "host", "alias", "api-key", "api-key-file", "log-file", "log-disable", "metrics", "slots",
                  "props", "verbose", "path", "threads-http", "no-webui", "timeout", "chat-template", "chat-template-file",
                  "jinja", "reasoning-format"}
SHORT = {"ctx-size": "ctx", "cache-type-k": "ctk", "cache-type-v": "ctv", "n-gpu-layers": "ngl", "split-mode": "sm",
         "parallel": "np", "flash-attn": "fa", "spec-type": "spec", "batch-size": "b", "ubatch-size": "ub",
         "model": "file", "load-mode": "lm", "tensor-split": "ts", "n-cpu-moe": "ncmoe", "spec-draft-n-max": "draft-n",
         "HIP_VISIBLE_DEVICES": "gpu", "CUDA_VISIBLE_DEVICES": "gpu", "ARGOS_GPUS": "gpu", "LLAMA_ENABLE_MTP_OPT": "mtp-opt"}
UNTRACKED = "untracked"
MIN_PROMPT_TOKENS = 128   # shorter prompts give noisy prefill speeds (and cache hits)
MIN_OUTPUT_TOKENS = 16
MATCH_GRACE_S = 120


def signature(command: str) -> tuple[str, dict[str, Any]]:
    """(config id, the normalised settings). An unreadable command gets its own id from its text."""
    try:
        cmd = parse_command(command or "")
    except ValueError:
        text = " ".join((command or "").split())
        return "raw-" + hashlib.sha1(text.encode()).hexdigest()[:8], {"command": text[:200]}
    settings: dict[str, Any] = {}
    for a in cmd.args:
        if not a.name or a.name in VOLATILE_FLAGS:
            continue
        value: Any = a.value if a.value is not None else True
        if a.name in ("model", "mmproj", "model-draft") and isinstance(value, str):
            value = os.path.basename(value)
        settings[a.name] = value
    for k, v in cmd.env.items():
        settings[k] = v
    blob = json.dumps(settings, sort_keys=True, default=str)
    return hashlib.sha1(blob.encode()).hexdigest()[:10], settings


# shown first, and whenever they differ anywhere; other settings only where a config departs from the usual value
PRIORITY = ("ctx-size", "parallel", "kv", "split-mode", "spec-type", "HIP_VISIBLE_DEVICES", "CUDA_VISIBLE_DEVICES", "ARGOS_GPUS")


def _display(settings: dict) -> dict:
    """Settings as the label shows them: the K and V cache types folded into one "kv"."""
    d = dict(settings)
    k, v = d.pop("cache-type-k", None), d.pop("cache-type-v", None)
    if k or v:
        d["kv"] = k if (v or k) == k else f"{k or 'f16'}/{v or 'f16'}"
    return d


def _part(key: str, value: Any) -> str:
    name = SHORT.get(key, key)
    if value is None:
        return f"default {name}"
    return name if value is True else f"{name} {value}"


def labels(configs: dict[str, dict]) -> dict[str, str]:
    """A short label per config, naming what sets it apart from the others.

    A setting that differs between configs is shown for a config when it is one of the key
    settings (PRIORITY) and is set, or when this config departs from its most common value. With one config, the
    headline settings are shown instead.
    """
    real = {k: _display(v) for k, v in configs.items() if k != UNTRACKED}
    out = {UNTRACKED: "untracked (no recorded command)"}
    if len(real) == 1:
        (cid, s), = real.items()
        out[cid] = " · ".join(_part(k, s[k]) for k in ("ctx-size", "parallel", "kv", "split-mode", "spec-type",
                                                         "n-gpu-layers", "flash-attn") if k in s) or "default settings"
        return out
    order = list(real)  # first seen first; breaks ties in "most common"
    keys = sorted({k for c in real.values() for k in c})
    differ = [k for k in keys if len({json.dumps(c.get(k), default=str) for c in real.values()}) > 1]
    usual = {}
    for k in differ:
        counts: dict[str, int] = {}
        for cid in order:
            counts[json.dumps(real[cid].get(k), default=str)] = counts.get(json.dumps(real[cid].get(k), default=str), 0) + 1
        usual[k] = max(counts, key=lambda v: (counts[v], -[json.dumps(real[c].get(k), default=str) for c in order].index(v)))
    ranked = [k for k in PRIORITY if k in differ] + [k for k in differ if k not in PRIORITY]
    for cid, s in real.items():
        parts = [_part(k, s.get(k)) for k in ranked
                 if (k in PRIORITY and s.get(k) is not None) or json.dumps(s.get(k), default=str) != usual[k]]
        out[cid] = " · ".join(parts) or "default settings"
    return out


def concurrency(intervals: list[tuple[float, float]]) -> list[int]:
    """For each (start, end): how many intervals overlap it, itself included."""
    starts = sorted(s for s, _ in intervals)
    ends = sorted(e for _, e in intervals)
    return [max(1, bisect.bisect_left(starts, e) - bisect.bisect_right(ends, s)) for s, e in intervals]


def _q(values: list[float], q: float) -> Optional[float]:
    if not values:
        return None
    vs = sorted(values)
    pos = (len(vs) - 1) * q
    lo, hi = int(pos), min(int(pos) + 1, len(vs) - 1)
    return round(vs[lo] + (vs[hi] - vs[lo]) * (pos - lo), 2)


def _stats(values: list[float]) -> dict:
    return {"n": len(values), "p10": _q(values, 0.1), "median": _q(values, 0.5), "p90": _q(values, 0.9),
            "max": round(max(values), 2) if values else None}


def model_perf(store: Store, model_id: str, base_id: str = "", days: float = 90, now: Optional[float] = None,
               max_points: int = 3000) -> dict:
    """Everything the model page shows, computed from the database."""
    now = now or time.time()
    since = now - days * 86400
    sessions = sorted(store.list_sessions(model=model_id, since=since, limit=10000), key=lambda s: s["started_at"])
    requests = sorted(store.list_requests(model=model_id, since=since, limit=50000), key=lambda r: r["ts"])
    base = base_id or model_id.split("--", 1)[0]
    runs = [r for r in store.list_runs(limit=100000) if r.get("model_id") in (model_id, base) and r["created_at"] >= since]

    configs: dict[str, dict] = {}
    first_seen: dict[str, float] = {}
    meta: dict[str, dict] = {}

    def config_for(command: str, at: float) -> str:
        cid, settings = signature(command)
        configs.setdefault(cid, settings)
        first_seen[cid] = min(first_seen.get(cid, at), at)
        meta.setdefault(cid, {"command": command, "sessions": 0, "loaded_s": 0.0, "vram": [], "gpus": set(),
                              "last_used": at})
        meta[cid]["last_used"] = max(meta[cid]["last_used"], at)
        return cid

    session_cfg = []
    for s in sessions:
        cid = config_for(s["command"], s["started_at"]) if s["command"] else UNTRACKED
        session_cfg.append(cid)
        if cid != UNTRACKED:
            m = meta[cid]
            m["sessions"] += 1
            m["loaded_s"] += (s["ended_at"] or now) - s["started_at"]
            if s["measured_gib"] or s["size_gib"]:
                m["vram"].append(s["measured_gib"] or s["size_gib"])
            m["gpus"].update(s["gpus"])
    starts = [s["started_at"] for s in sessions]

    def session_of(ts: float) -> Optional[int]:
        """The load a request ran under: the latest one started before it, if it had not ended long before."""
        i = bisect.bisect_right(starts, ts + MATCH_GRACE_S) - 1
        if i >= 0 and (sessions[i]["ended_at"] or now) + MATCH_GRACE_S >= ts:
            return i
        return None

    intervals = [(r["ts"] - (r["duration_ms"] or 0) / 1000, r["ts"]) for r in requests]
    conc = concurrency(intervals)
    points = []
    for r, (start, _), c in zip(requests, intervals, conc):
        i = session_of(start) if sessions else None
        cid = session_cfg[i] if i is not None else UNTRACKED
        points.append({"ts": r["ts"], "config": cid, "source": "live", "concurrent": c,
                       "input_tokens": r["input_tokens"], "output_tokens": r["output_tokens"],
                       "prefill": r["prompt_per_second"] if (r["input_tokens"] or 0) >= MIN_PROMPT_TOKENS else None,
                       "decode": r["tokens_per_second"] if (r["output_tokens"] or 0) >= MIN_OUTPUT_TOKENS else None})
    for r in runs:
        cid = config_for(r["launch_command"], r["created_at"])
        points.append({"ts": r["created_at"], "config": cid, "source": "bench", "concurrent": 1,
                       "input_tokens": r.get("input_tokens"), "output_tokens": r.get("output_tokens"),
                       "prefill": r.get("prompt_per_second") or None, "decode": r.get("tokens_per_second") or None})
    if any(p["config"] == UNTRACKED for p in points):
        configs.setdefault(UNTRACKED, {})
        first_seen.setdefault(UNTRACKED, min(p["ts"] for p in points if p["config"] == UNTRACKED))

    order = sorted(configs, key=lambda c: (c == UNTRACKED, first_seen.get(c, now)))
    names = labels(configs)
    out_configs = []
    for slot, cid in enumerate(order):
        pts = [p for p in points if p["config"] == cid]
        live = [p for p in pts if p["source"] == "live"]
        by_conc: dict[int, list[float]] = {}
        for p in live:
            if p["decode"]:
                by_conc.setdefault(p["concurrent"], []).append(p["decode"])
        s = configs[cid]
        m = meta.get(cid, {})
        out_configs.append({
            "id": cid, "slot": slot, "label": names.get(cid, cid), "settings": s,
            "parallel_setting": s.get("parallel"),
            "requests": len(live), "benchmarks": len(pts) - len(live),
            "prefill": _stats([p["prefill"] for p in pts if p["prefill"]]),
            "decode": _stats([p["decode"] for p in pts if p["decode"]]),
            "concurrency_max": max((p["concurrent"] for p in live), default=None),
            "decode_by_concurrency": {str(k): {"n": len(v), "median": _q(v, 0.5)} for k, v in sorted(by_conc.items())},
            "sessions": m.get("sessions", 0), "loaded_s": round(m.get("loaded_s", 0)),
            "vram_gib": round(median(m["vram"]), 2) if m.get("vram") else None,
            "gpus": sorted(m.get("gpus") or [], key=lambda x: (len(x), x)), "command": m.get("command", ""),
            "first_seen": first_seen.get(cid), "last_used": m.get("last_used") or max((p["ts"] for p in pts), default=None),
        })

    every_decode = [p["decode"] for p in points if p["decode"]]
    every_prefill = [p["prefill"] for p in points if p["prefill"]]
    live_points = [p for p in points if p["source"] == "live"]
    if len(points) > max_points:  # keep the newest for the scatter; the stats above used all of them
        points = sorted(points, key=lambda p: p["ts"])[-max_points:]
    return {
        "model_id": model_id, "days": days,
        "summary": {"requests": len(live_points), "benchmarks": len(runs), "configs": len([c for c in configs if c != UNTRACKED]),
                    "sessions": len(sessions), "loaded_s": round(sum((s["ended_at"] or now) - s["started_at"] for s in sessions)),
                    "decode_median": _q(every_decode, 0.5), "prefill_median": _q(every_prefill, 0.5),
                    "concurrency_max": max((p["concurrent"] for p in live_points), default=None),
                    "output_tokens": sum(p["output_tokens"] or 0 for p in live_points)},
        "configs": out_configs,
        "points": [{k: p[k] for k in ("ts", "config", "source", "concurrent", "prefill", "decode", "input_tokens", "output_tokens")}
                   for p in points],
        "thresholds": {"min_prompt_tokens": MIN_PROMPT_TOKENS, "min_output_tokens": MIN_OUTPUT_TOKENS},
    }


__all__ = ["model_perf", "signature", "labels", "concurrency", "UNTRACKED"]
