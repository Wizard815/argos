"""Comparing launch configs: what differs between them (flags, env vars, the model file) and what it did to speed and memory.

A config is one distinct way a model was launched (see ``model_perf.signature``): its flags and env vars, with the
ones that never change performance left out. Anything can be compared with anything: two configs of one model where
only the context changed, two profiles that differ in one env var and three flags, or two different models.

The same diffs feed the assistant: ``change_lines`` turns each model's configs into short "this changed, and this is
what happened" sentences for its memory and its LIVE DATA.
"""
from __future__ import annotations

import json
import re
from typing import Any, Optional

from .command import swap_model_id
from .db import Store
from .model_perf import UNTRACKED, model_perf, signature

ENV_KEY = re.compile(r"^[A-Z_][A-Z0-9_]*$")
# what "better" means for each result: +1 higher is better, -1 lower is better
RESULTS = [("decode", "Decode t/s", 1), ("prefill", "Prefill t/s", 1), ("vram_gib", "VRAM GiB", -1),
           ("sessions", "Times loaded", 0), ("requests", "Requests", 0), ("benchmarks", "Benchmarks", 0)]


class CompareError(ValueError):
    pass


def kind_of(key: str) -> str:
    if key in ("model", "mmproj", "model-draft"):
        return "file"
    return "env" if ENV_KEY.match(key) else "flag"


def show(value: Any) -> str:
    if value is None:
        return "not set"
    if value is True:
        return "on"
    return str(value)


def diff_settings(settings: list[dict]) -> list[dict]:
    """One row per setting any config has: its value in each, and whether they differ. Files first, then flags, then env."""
    keys = sorted({k for s in settings for k in s}, key=lambda k: ({"file": 0, "flag": 1, "env": 2}[kind_of(k)], k))
    rows = []
    for k in keys:
        values = [s.get(k) for s in settings]
        rows.append({"key": k, "kind": kind_of(k), "values": [show(v) for v in values],
                     "differs": len({json.dumps(v, default=str) for v in values}) > 1})
    return rows


def describe_change(before: dict, after: dict) -> str:
    """"ctx-size 32768→65536, +HIP_VISIBLE_DEVICES=0,1, -no-mmap": what turns ``before`` into ``after``."""
    parts = []
    for row in diff_settings([before, after]):
        if not row["differs"]:
            continue
        a, b = before.get(row["key"]), after.get(row["key"])
        sep = "=" if row["kind"] == "env" else " "
        if a is None:
            parts.append(f"+{row['key']}" + ("" if b is True else f"{sep}{b}"))
        elif b is None:
            parts.append(f"-{row['key']}")
        else:
            parts.append(f"{row['key']} {show(a)}→{show(b)}")
    return ", ".join(parts) or "no setting changed"


def _pct(a: Optional[float], b: Optional[float]) -> Optional[float]:
    return round((b - a) / a * 100, 1) if a and b is not None else None


def _result_values(cfg: dict) -> dict:
    return {"decode": (cfg.get("decode") or {}).get("median"), "prefill": (cfg.get("prefill") or {}).get("median"),
            "vram_gib": cfg.get("vram_gib"), "concurrency_max": cfg.get("concurrency_max"), "sessions": cfg.get("sessions"),
            "requests": cfg.get("requests"), "benchmarks": cfg.get("benchmarks")}


def describe_result(a: dict, b: dict) -> str:
    """"decode 62→58 t/s (-6%), prefill 790→720 t/s, VRAM 25.5→27.1 GiB" for two configs' results."""
    ra, rb = _result_values(a), _result_values(b)
    parts = []
    for key, name, unit in (("decode", "decode", " t/s"), ("prefill", "prefill", " t/s"), ("vram_gib", "VRAM", " GiB")):
        x, y = ra[key], rb[key]
        if x is None or y is None:
            continue
        pct = _pct(x, y)
        parts.append(f"{name} {x:.1f}→{y:.1f}{unit}" + (f" ({pct:+.0f}%)" if pct is not None and abs(pct) >= 1 else ""))
    return ", ".join(parts) or "no results to compare yet"


# ----- what can be compared ----------------------------------------------------------------
def options(store: Store, model_ids: list[str], days: float = 90) -> list[dict]:
    """Every config of every model, for the pickers."""
    out = []
    for mid in model_ids:
        for c in model_perf(store, mid, days=days)["configs"]:
            if c["id"] == UNTRACKED:
                continue
            out.append({"model_id": mid, "config": c["id"], "label": c["label"], "decode": (c["decode"] or {}).get("median"),
                        "sessions": c["sessions"], "benchmarks": c["benchmarks"], "last_used": c["last_used"]})
    return out


def _resolve(store: Store, item: dict, days: float) -> dict:
    """An item is {model_id, config}, {run_id} or {session_id}. Returns the config with its model."""
    model_id, config = str(item.get("model_id") or ""), str(item.get("config") or "")
    if item.get("run_id"):
        run = store.get_run(str(item["run_id"]))
        if not run:
            raise CompareError(f"no run {item['run_id']}")
        model_id = run.get("model_id") or swap_model_id(run.get("base_model") or "")
        config = signature(run["launch_command"])[0]
    elif item.get("session_id"):
        s = next((x for x in store.list_sessions(limit=100000) if str(x["id"]) == str(item["session_id"])), None)
        if not s or not s.get("command"):
            raise CompareError(f"load {item['session_id']} has no recorded command")
        model_id, config = s["model_id"], signature(s["command"])[0]
    if not model_id or not config:
        raise CompareError("pick a model and a config")
    for c in model_perf(store, model_id, days=days)["configs"]:
        if c["id"] == config:
            return {"model_id": model_id, **c}
    raise CompareError(f"{model_id} has no config {config} in the last {days:g} days")


def compare(store: Store, items: list[dict], days: float = 90) -> dict:
    if not isinstance(items, list) or not 2 <= len(items) <= 6:
        raise CompareError("compare 2 to 6 configs")
    cfgs = [_resolve(store, it, days) for it in items]
    rows = diff_settings([c["settings"] for c in cfgs])
    base = _result_values(cfgs[0])
    results = []
    for key, label, better in RESULTS:
        values = [_result_values(c)[key] for c in cfgs]
        if all(v is None for v in values):
            continue
        results.append({"key": key, "label": label, "better": better, "values": values,
                        "change_pct": [None] + [_pct(base[key], v) for v in values[1:]]})
    a = cfgs[0]
    summary = [f"{c['model_id']} [{c['label']}] vs {a['model_id']} [{a['label']}]: "
               f"{describe_change(a['settings'], c['settings'])}; {describe_result(a, c)}" for c in cfgs[1:]]
    return {
        "items": [{"model_id": c["model_id"], "config": c["id"], "label": c["label"], "command": c["command"], "gpus": c["gpus"],
                   "first_seen": c["first_seen"], "last_used": c["last_used"], "loaded_s": c["loaded_s"]} for c in cfgs],
        "settings": rows, "differences": sum(1 for r in rows if r["differs"]), "results": results, "summary": summary,
    }


def change_lines(store: Store, model_id: str, days: float = 90, limit: int = 6) -> list[str]:
    """Each config of a model next to the one used most: what changed, and what it did. For the assistant."""
    return change_lines_from(model_id, model_perf(store, model_id, days=days)["configs"], limit)


def change_lines_from(model_id: str, configs: list[dict], limit: int = 6) -> list[str]:
    configs = [c for c in configs if c["id"] != UNTRACKED]
    if len(configs) < 2:
        return []
    usual = max(configs, key=lambda c: (c["sessions"] + c["benchmarks"] + c["requests"], c["last_used"] or 0))
    out = []
    for c in sorted(configs, key=lambda c: -(c["last_used"] or 0)):
        if c is usual:
            continue
        out.append(f"{model_id}: [{c['label']}] vs the usual [{usual['label']}]: changed {describe_change(usual['settings'], c['settings'])}; "
                   f"{describe_result(usual, c)}")
        if len(out) >= limit:
            break
    return out


__all__ = ["compare", "options", "change_lines", "change_lines_from", "diff_settings", "describe_change", "describe_result", "CompareError"]
