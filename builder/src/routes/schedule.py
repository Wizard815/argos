"""Scheduling: the plan, a simulator, VRAM measuring, and the live pool. Saving the plan applies it to the engine."""
from __future__ import annotations

import time
from typing import Optional

from flask import Blueprint, request

from ..always import apply_always
from ..command import parse_command
from ..integrations import engine_api
from ..library import scan_models
from ..scheduler import (Engine, PlanError, clean_plan, effective_size, engine_file, render_fragment, simulate,
                         write_fragment)
from ..schedule_rows import build_rows, plan_sizes
from ..web import body, check_id, ctx, err, ok
from . import core

bp = Blueprint("schedule", __name__)
APPLY_CHECK_S = 300  # how often the background tick re-checks the engine's copy (it scans the library)
APPLY_RETRY_S = 60   # ...and how often while models are left out because the engine does not list them yet
KNOWN_TTL_S = 30


# ----- shared pieces -----------------------------------------------------------------
def known_ids() -> tuple[Optional[set[str]], str]:
    """Model ids the engine lists at /v1/models, or (None, "") when it cannot be asked.

    Only the live list is trusted: the engine makes models from files and from profiles itself, so
    they are not in its config file. Without it nothing is filtered. Cached for a few seconds because
    this is an HTTP call and the Schedule view loads often.
    """
    c = ctx()
    url = c.settings.get("integrations")["engine_url"]
    cached = c.schedule_state.get("_known")
    if cached and cached["key"] == url and time.time() - cached["at"] < KNOWN_TTL_S:
        return cached["value"]
    value: tuple[Optional[set[str]], str] = (None, "")
    if url:
        try:
            value = (engine_api.model_ids(url), "live")
        except Exception:  # noqa: BLE001 - the Live view and Integrations page show connection problems
            pass
    c.schedule_state["_known"] = {"at": time.time(), "key": url, "value": value}
    return value


def compute(plan: Optional[dict] = None) -> dict:
    c = ctx()
    plan = plan or c.plans.get()
    known, known_from = known_ids()
    library = scan_models(c.settings.get("paths")["models_dir"])
    presets = core.cookbook_state()["presets"]
    rows, commands = build_rows(library, presets, plan, c.settings.get("prefab"), known)
    sizes = plan_sizes(rows)
    text, skipped = render_fragment(plan, sizes, known)
    return {"plan": plan, "rows": rows, "commands": commands, "sizes": sizes, "fragment": text,
            "skipped": skipped, "known_from": known_from}


def detected_cards() -> list[dict]:
    """The GPUs the telemetry has seen, with their VRAM in GiB."""
    return [{"gpu": g["gpu"], "name": g.get("name") or f"GPU {g['gpu']}",
             "total_gib": round((g.get("mem_total_mb") or 0) / 1024, 2)} for g in ctx().store.gpu_latest()]


def apply_plan(force: bool = True, data: Optional[dict] = None) -> None:
    """Give the plan to the engine: write its routing block into the engine's config folder, which the engine
    watches. Called on every save and profile change; the background tick passes force=False.

    ``data`` is a compute() result the caller already has, to avoid scanning the library twice.
    """
    c = ctx()
    path = engine_file()
    if not path:
        return
    plan = data["plan"] if data else c.plans.get()
    wait = APPLY_RETRY_S if c.schedule_state.get("_retry") else APPLY_CHECK_S
    if not force and time.time() - c.schedule_state.get("_checked", 0.0) < wait:
        return
    c.schedule_state["_checked"] = time.time()
    try:
        data = data or compute(plan)
        c.schedule_state["_retry"] = bool(data["skipped"])  # a new profile reaches the engine within its refresh
        if write_fragment(path, data["fragment"]):
            c.schedule_state.update(written_at=time.time(), error="")
            c.event("schedule", "applied the scheduling plan to the engine",
                    data={"models": len(data["sizes"]), "skipped": data["skipped"], "file": path})
        c.schedule_state.update(error="")
    except OSError as exc:
        if c.schedule_state.get("error") != str(exc):
            c.event("schedule", f"could not apply the plan to the engine ({path}): {exc}", level="error")
        c.schedule_state.update(error=str(exc))


def _payload(data: dict) -> dict:
    c = ctx()
    return {"plan": data["plan"], "rows": data["rows"], "fragment": data["fragment"], "skipped": data["skipped"],
            "known_from": data["known_from"], "detected_cards": detected_cards(),
            "applied": {"connected": bool(engine_file()), "at": c.schedule_state.get("written_at"),
                        "error": c.schedule_state.get("error", "")},
            "live_configured": bool(c.settings.get("integrations")["engine_url"])}


# ----- plan --------------------------------------------------------------------------
@bp.get("/api/schedule")
def schedule_get():
    return ok(**_payload(compute()))


@bp.put("/api/schedule")
def schedule_put():
    c = ctx()
    try:
        c.plans.update(body())
    except PlanError as exc:
        return err(str(exc))
    c.event("schedule", "saved the scheduling plan")
    data = compute()
    apply_plan(data=data)
    return ok(**_payload(data))


# ----- simulator ---------------------------------------------------------------------
@bp.post("/api/schedule/simulate")
def schedule_simulate():
    """Run steps through the same rules the pool router uses. ``plan`` overrides the saved plan for this run."""
    c, d = ctx(), body()
    steps = d.get("steps")
    if not isinstance(steps, list) or not steps or not all(isinstance(s, dict) for s in steps):
        return err("steps must be a non-empty list of {op, model}")
    if len(steps) > 200:
        return err("at most 200 steps")
    try:
        plan = {**c.plans.get(), **clean_plan(d.get("plan") or {})}
        overrides = {str(k): float(v) for k, v in (d.get("sizes") or {}).items()}
    except (PlanError, TypeError, ValueError) as exc:
        return err(str(exc))
    data = compute(plan)
    sizes = {r["id"]: r["effective_gib"] for r in data["rows"]}
    sizes.update(plan["sizes"])  # models Argos has that this app has no file or profile for
    for mid, m in plan["measured"].items():
        sizes.setdefault(mid, m["gib"])
    sizes.update(overrides)
    cards = [x["total_gib"] for x in detected_cards()]
    if not plan["cards"] and not cards:
        return err("no cards: set the card sizes in the plan, or let the GPU telemetry see them first")
    eng = Engine(plan, sizes, detected_gib=cards)
    measured = {k: v["load_seconds"] for k, v in plan["measured"].items() if v.get("load_seconds")}
    return ok(results=simulate(eng, steps, measured), cards=[{"id": i, "cap_gib": round(cap, 2)}
                                                              for i, cap in eng.cards],
              budget_gib=round(eng.budget, 2), placement=bool(eng.cards),
              unknown=[m for m in sizes if not sizes[m]][:50])


# ----- measure -----------------------------------------------------------------------
@bp.post("/api/schedule/measure")
def measure_start():
    c, d = ctx(), body()
    row_id = (d.get("id") or "").strip()
    data = compute()
    command = data["commands"].get(row_id)
    if not command:
        return err("unknown model: save it as a profile or put the file in the models folder first", 404)
    try:
        parse_command(command)
        timeout = max(10.0, min(float(d.get("timeout_s", 900)), 3600.0))
    except (ValueError, TypeError) as exc:
        return err(str(exc))
    if any(j["state"] in ("queued", "baseline", "loading", "settling") for j in c.measurer.jobs.values()):
        return err("a measurement is already running; wait for it or cancel it", 409)
    always = c.settings.get("always")
    if always["on_launch"]:
        command = apply_always(command, always)[0]
    job = c.measurer.start(command, row_id, label=row_id, timeout_s=timeout,
                           settle_s=float(d.get("settle_s", 6.0)))
    return ok(job=c.measurer.get(job))


@bp.get("/api/schedule/measure/<job_id>")
def measure_status(job_id):
    job = ctx().measurer.get(check_id(job_id))
    return ok(job=job) if job else err("not found", 404)


@bp.post("/api/schedule/measure/<job_id>/cancel")
def measure_cancel(job_id):
    return ok(cancelled=ctx().measurer.cancel(check_id(job_id)))


@bp.post("/api/schedule/measured/<path:model_id>/use")
def measure_use(model_id):
    """Copy a measurement into the plan as a size set by hand."""
    c = ctx()
    plan = c.plans.get()
    m = plan["measured"].get(model_id)
    if not m:
        return err("nothing measured for this model", 404)
    c.plans.update({"sizes": {**plan["sizes"], model_id: m["gib"]}})
    data = compute()
    apply_plan(data=data)
    return ok(**_payload(data))


# ----- live --------------------------------------------------------------------------
@bp.get("/api/schedule/live")
def schedule_live():
    """Argos's /api/pool, passed through so the browser needs no access to Argos."""
    url = ctx().settings.get("integrations")["engine_url"]
    if not url:
        return ok(connected=False, error="set the Argos URL under Integrations", pool=None)
    try:
        return ok(connected=True, error="", pool=engine_api.pool_state(url), at=time.time())
    except Exception as exc:  # noqa: BLE001
        return ok(connected=False, error=str(exc), pool=None)


__all__ = ["bp", "apply_plan", "compute"]
