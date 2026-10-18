"""Inventory: models seen, load history, new settings to review, and the host."""
from __future__ import annotations

import csv
import io
import time
from typing import Any

from flask import Blueprint, Response, request

from ..cookbook import variant_id
from ..integrations import engine_api
from ..inventory import STATUSES
from ..library import scan_models
from ..model_perf import model_perf
from ..web import body, ctx, err, ok
from . import core

bp = Blueprint("inventory", __name__)
REFRESH_EVERY_S = 600


# ----- background work (called from the collector loop) --------------------------------
def sync_running() -> None:
    """Match the open sessions to what Argos is running. Does nothing if Argos cannot be reached."""
    c = ctx()
    url = c.settings.get("integrations")["engine_url"]
    if not url:
        return
    try:
        running = engine_api.running(url)
    except Exception:  # noqa: BLE001 - unreachable is not the same as "everything stopped"
        return
    try:
        pool = engine_api.pool_state(url)
    except Exception:  # noqa: BLE001 - not Argos, or the group router
        pool = {}
    pooled = {m["id"]: m for m in pool.get("models") or []} if pool.get("enabled") else {}
    measured = c.plans.get()["measured"]
    info: dict[str, dict[str, Any]] = {}
    for r in running:
        mid, p = r["model"], pooled.get(r["model"], {})
        info[mid] = {"cmd": r.get("cmd") or "", "state": r.get("state"), "cards": p.get("cards") or [],
                     "size_gib": p.get("size_gib") or None, "load_seconds": p.get("load_seconds") or None,
                     "measured_gib": (measured.get(mid) or {}).get("gib")}
    c.inventory.sync_sessions(info)


def run_refresh() -> dict:
    """Scan the library, profiles and Argos, and record anything new."""
    c = ctx()
    i = c.settings.get("integrations")
    library = scan_models(c.settings.get("paths")["models_dir"])
    commands = {variant_id(p): p["cmd"] for p in core.cookbook_state()["presets"] if variant_id(p)}
    live_ids = hardware = version = None
    problems = []
    if i["engine_url"]:
        for name, fn in (("model list", engine_api.model_ids), ("hardware", engine_api.hardware), ("version", engine_api.version)):
            try:
                value = fn(i["engine_url"])
            except Exception as exc:  # noqa: BLE001 - Argos has /api/hardware, plain Argos does not
                problems.append(f"Argos {name}: {exc}")
                continue
            if name == "model list":
                live_ids = value
            elif name == "hardware":
                hardware = value
            else:
                version = value
    result = c.inventory.refresh(library, commands, live_ids, hardware, version)
    result["problems"] = problems + result["problems"]
    result["at"] = time.time()
    c.store.set_setting("_inventory_refresh", result)
    return result


def refresh_if_due() -> None:
    last = (ctx().store.get_settings().get("_inventory_refresh") or {}).get("at", 0)
    if time.time() - last >= REFRESH_EVERY_S:
        run_refresh()


# ----- views --------------------------------------------------------------------------------
@bp.get("/api/inventory")
def inventory_get():
    c = ctx()
    snaps = c.store.list_host_snapshots(200)
    latest = {}
    for s in snaps:
        latest.setdefault(s["source"], s)
    last = c.store.get_settings().get("_inventory_refresh") or {}
    return ok(models=c.inventory.models_summary(), pending=len(c.store.list_traits(status="new")),
              host={k: {"ts": v["ts"], "data": v["data"]} for k, v in latest.items()},
              host_changes={k: sum(1 for s in snaps if s["source"] == k) for k in latest},
              last_refresh=last, open_sessions=c.store.count_sessions(open_only=True))


def _sessions():
    a = request.args
    since = time.time() - a.get("days", 30, type=float) * 86400
    rows = ctx().store.list_sessions(model=a.get("model") or None, since=since,
                                     limit=max(1, min(a.get("limit", 500, type=int), 5000)))
    return ctx().inventory.session_stats(rows)


@bp.get("/api/inventory/model/<path:model_id>")
def model_page(model_id):
    """One model: its launch configs side by side, request speeds over time, and speed by concurrency."""
    c = ctx()
    # Floor of an hour, not a day: after a tuning change an hour of runs is the
    # useful window, so days may be fractional (1/24 = one hour).
    days = max(1 / 24.0, min(request.args.get("days", 7, type=float), 3650.0))
    perf = model_perf(c.store, model_id, days=days)
    row = next((m for m in c.inventory.models_summary() if m["model_id"] == model_id), None)
    if row is None and not perf["configs"]:
        return err("no such model in the inventory", 404)
    return ok(model=row, **perf)


@bp.get("/api/compare/options")
def compare_options():
    """Every launch config of every model in the inventory, for the Compare pickers."""
    from ..compare import options
    c = ctx()
    # Floor of an hour, not a day: after a tuning change an hour of runs is the
    # useful window, so days may be fractional (1/24 = one hour).
    days = max(1 / 24.0, min(request.args.get("days", 7, type=float), 3650.0))
    return ok(options=options(c.store, [m["model_id"] for m in c.inventory.models_summary()], days=days))


@bp.post("/api/compare")
def compare_configs():
    """Two to six configs side by side: the settings that differ (flags, env vars, model file) and the results.
    Items are {model_id, config}, {run_id} (a perf-sheet run) or {session_id} (a load)."""
    from ..compare import CompareError, compare
    d = body()
    try:
        return ok(**compare(ctx().store, d.get("items"), days=max(1 / 24.0, min(float(d.get("days") or 7), 3650.0))))
    except CompareError as exc:
        return err(str(exc))


@bp.get("/api/inventory/similar/<path:model_id>")
def similar_models(model_id):
    """Other models that look like this one (arch, quant, size, speeds), nearest first."""
    hits = ctx().memory.similar("model", model_id, max(1, min(request.args.get("k", 6, type=int), 20)))
    return ok(results=[{"model_id": h["key"], "text": h["text"], "similarity": h["similarity"], "matched_by": h["matched_by"]} for h in hits])


@bp.get("/api/inventory/sessions")
def sessions_get():
    return ok(sessions=_sessions())


@bp.get("/api/inventory/sessions.csv")
def sessions_csv():
    """One row per load, ready for a spreadsheet."""
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["started", "ended", "duration_s", "model", "gpus", "vram_gib_planned", "vram_gib_measured", "quant", "arch", "ctx",
                "cache_k", "cache_v", "split_mode", "spec_type", "load_mode", "parallel", "gpu_util_avg", "gpu_vram_peak_gib",
                "requests", "tokens_per_second_avg", "command"])
    for s in _sessions():
        gs = s["gpu_stats"]
        w.writerow([time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(s["started_at"])),
                    time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(s["ended_at"])) if s["ended_at"] else "",
                    s["duration_s"], s["model_id"], ",".join(s["gpus"]), s["size_gib"] or "", s["measured_gib"] or "",
                    s["quant"] or "", s["arch"] or "", s["ctx"] or "", s["cache_k"] or "", s["cache_v"] or "",
                    s["split_mode"] or "", s["spec_type"] or "", s["load_mode"] or "", s["parallel"] or "",
                    round(sum(g["util_avg"] or 0 for g in gs) / len(gs), 1) if gs else "",
                    round(sum((g["mem_max_mb"] or 0) for g in gs) / 1024, 2) if gs else "",
                    s["requests"]["requests"], round(s["requests"]["tg_avg"], 1) if s["requests"]["tg_avg"] else "",
                    s["command"]])
    return Response(out.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=model-sessions.csv"})


# ----- traits -------------------------------------------------------------------------------
@bp.get("/api/inventory/traits")
def traits_get():
    a = request.args
    return ok(traits=ctx().store.list_traits(kind=a.get("kind") or None, status=a.get("status") or None))


@bp.put("/api/inventory/traits/<int:trait_id>")
def traits_put(trait_id):
    """Mark a trait known or ignored, or fold it into another value of the same kind."""
    c, d = ctx(), body()
    t = c.store.get_trait(trait_id)
    if not t:
        return err("not found", 404)
    status = d.get("status", t["status"])
    if status not in STATUSES:
        return err(f"status must be one of {', '.join(STATUSES)}")
    canonical = str(d.get("canonical") or "").strip()
    if status == "alias":
        if canonical == t["value"] or not any(o["kind"] == t["kind"] and o["value"] == canonical for o in c.store.list_traits(kind=t["kind"])):
            return err("canonical must be another value already seen for this kind")
    else:
        canonical = ""
    saved = c.store.update_trait(trait_id, status=status, canonical=canonical, note=str(d.get("note", t["note"]))[:500])
    c.inventory.invalidate()
    c.event("inventory", f"{t['kind']} {t['value']} marked {status}" + (f" → {canonical}" if canonical else ""),
            data={"trait_id": trait_id})
    return ok(trait=saved, pending=len(c.store.list_traits(status="new")))


@bp.post("/api/inventory/refresh")
def refresh():
    return ok(result=run_refresh())


# ----- the assistant helps clean up ----------------------------------------------------------
@bp.post("/api/inventory/assist")
def assist():
    """Suggestions for the traits waiting for review: the assistant's, checked, plus rule-based ones. Nothing is applied."""
    c = ctx()
    pending = c.store.list_traits(status="new")
    if not pending:
        return ok(suggestions=[], assistant="nothing to review")
    rules = c.inventory.suggestions()
    problem, llm = "", []
    try:
        llm = c.inventory.validate_suggestions(c.assistant.ask_json(*_assist_prompt(c, pending[:25])))
        if not llm:
            problem = "the assistant gave no usable suggestions"
    except Exception as exc:  # noqa: BLE001 - the assistant may be off or may answer nonsense; rules still apply
        problem = str(exc)
    covered = {s["trait_id"] for s in llm}
    return ok(suggestions=llm + [s for s in rules if s["trait_id"] not in covered], assistant=problem or "ok")


def _assist_prompt(c, pending: list[dict]) -> tuple[str, str]:
    known: dict[str, list[str]] = {}
    for t in c.store.list_traits():
        if t["status"] in ("known", "alias") or t["count"] > 1:
            known.setdefault(t["kind"], []).append(t["value"])
    system = ("You tidy a log of llama.cpp settings. Each item is a value seen for the first time. For each, choose: "
              "\"known\" (a real, normal value), \"ignore\" (noise, a typo or one-off), or \"alias\" (the same thing as "
              "another existing value with different spelling; give that value as canonical). "
              "Answer with only a JSON array: [{\"id\": 1, \"action\": \"known\", \"canonical\": \"\", \"reason\": \"short\"}]")
    user = "ITEMS:\n" + "\n".join(f"id={t['id']} kind={t['kind']} value={t['value']} uses={t['count']}"
                                  + (f" e.g. {t['example']}" if t["example"] else "") for t in pending)
    user += "\n\nEXISTING VALUES:\n" + "\n".join(f"{k}: {', '.join(v[:15])}" for k, v in known.items())
    return system, user


__all__ = ["bp", "sync_running", "run_refresh", "refresh_if_due"]
