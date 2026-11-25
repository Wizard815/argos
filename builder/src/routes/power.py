"""Profiles -> Power: each profile's power cap and top clock, the cards now, the fan monitor's temperatures, and
benchmarks compared by power setting (gpu_power.py)."""
from __future__ import annotations

from flask import Blueprint, request

from .. import gpu_power as gp
from ..web import body, ctx, err, ok

bp = Blueprint("power", __name__)


def running_profile_ids(c) -> list[str]:
    """The profiles running now: the engine's models (``<model>--<label>`` or a model's default profile) and
    launches from this app."""
    from .core import _engine_profile, _engine_running, _profile_variants
    profiles = c.store.list_profiles()
    by_name = {}
    for p in profiles:
        by_name.setdefault(p["name"], p["profile_id"])
    out: list[str] = []
    try:
        by_vid = {v: pid for pid, v in _profile_variants().items()}
    except Exception:  # noqa: BLE001 - matched by name instead
        by_vid = {}
    for m in _engine_running(c):
        if not m.get("running"):
            continue
        pid = by_vid.get(m["base_model"])
        if not pid:
            _, name = _engine_profile(c, m["base_model"], m.get("command") or "")
            pid = by_name.get(name)
        if pid and pid not in out:
            out.append(pid)
    for r in c.runner.list():
        pid = by_name.get(r.profile_name or "")
        if r.running and pid and pid not in out:
            out.append(pid)
    return out


def tick(c, running=None) -> dict:
    """Put the cards at what is running now (or at what a benchmark holds)."""
    ids = running if running is not None else running_profile_ids(c)
    return gp.GOV.tick(c.store, c.settings.get("power"), ids,
                       log=lambda msg: c.event("power", msg, data={"applied": gp.GOV.last.get("applied")}))


def needed(c) -> bool:
    """Has anyone asked for anything? Nothing set and nothing changed: the governor never runs."""
    return bool(gp.table(c.store) or gp.idle(c.store).get("on") or gp.GOV.touched or gp.GOV.hold)


def now_label(cards: list[dict]) -> str:
    """The cards' setting as a benchmark records it when none was chosen: "stock", "180 W", "178 W / 225 W"."""
    if not cards:
        return ""
    bits = []
    for x in cards:
        if x.get("cap_w") is None:
            bits.append("?")
            continue
        s = "stock" if x.get("cap_default_w") and abs(x["cap_w"] - x["cap_default_w"]) <= 1 else f"{x['cap_w']:.0f} W"
        if x.get("sclk_limited"):
            s += f", ≤{x['sclk_limit_mhz']} MHz" if x.get("sclk_limit_mhz") else ", clocks limited"
        bits.append(s)
    return " / ".join(dict.fromkeys(bits))


def _cards(c) -> tuple[list[dict], str, str]:
    """(the cards, how they were read, an error)."""
    cfg = c.settings.get("power")
    b = gp.GOV.backend(cfg)
    try:
        cards = b.read()["cards"] if b else gp.read_sysfs()
        return [{k: v for k, v in x.items() if not k.startswith("_")} for x in cards], (b.name if b else "sysfs (read only)"), ""
    except Exception as exc:  # noqa: BLE001 - shown on the page
        return [], (b.name if b else ""), str(exc)


def _payload(c):
    cfg = c.settings.get("power")
    t = gp.table(c.store)
    cards, source, error = _cards(c)
    try:
        running = set(running_profile_ids(c))
    except Exception:  # noqa: BLE001
        running = set()
    rows = []
    for p in c.store.list_profiles():
        e = t.get(p["profile_id"]) or {}
        rows.append({"profile_id": p["profile_id"], "name": p["name"], "base_model": p.get("base_model") or "",
                     "on": bool(e.get("on")), "cap_w": e.get("cap_w"), "sclk_mhz": e.get("sclk_mhz"),
                     "label": gp.label(e) if e.get("on") else "", "running": p["profile_id"] in running})
    public = c.settings.public()["power"]
    return ok(enabled=gp.enabled(c.store), presets=gp.PRESETS, idle=gp.idle(c.store), profiles=rows, cards=cards,
              source=source, error=error, last=gp.GOV.last, hold=gp.GOV.hold, settings=public,
              fans=gp.fan_state(cfg.get("fan_bridge_url") or ""), compare=gp.compare(c.store.list_runs(limit=2000)),
              writable=gp.GOV.backend(cfg) is not None)


@bp.get("/api/power")
def power_view():
    """Everything Profiles -> Power shows."""
    return _payload(ctx())


@bp.put("/api/power/enabled")
def power_enabled():
    """Body: {on}. Off: the cards go back to stock once, then Argos leaves them alone."""
    c = ctx()
    on = bool(body().get("on"))
    c.store.set_setting(gp.ENABLED_KEY, on)
    c.event("power", "GPU power profiles " + ("on" if on else "off: the cards go back to stock"))
    _apply(c)
    return _payload(c)


@bp.put("/api/power/profile")
def power_profile():
    """Body: {profile_id, on, cap_w (W or null: stock), sclk_mhz (MHz or null: no limit)}. ``on`` false keeps the
    values for next time, and the profile runs the cards at whatever else asks for."""
    c, d = ctx(), body()
    p = c.store.get_profile(str(d.get("profile_id") or ""))
    if not p:
        return err("no such profile", 404)
    try:
        entry = gp.clean(d)
    except ValueError as exc:
        return err(str(exc))
    t = gp.table(c.store)
    t[p["profile_id"]] = entry
    c.store.set_setting(gp.KEY, t)
    c.event("power", f"profile {p['name']}: power " + (gp.label(entry) if entry["on"] else f"off ({gp.label(entry)} kept)"))
    _apply(c)
    return _payload(c)


@bp.put("/api/power/idle")
def power_idle():
    """Body: {on, cap_w, sclk_mhz}: the cards while no profile with a setting runs."""
    c = ctx()
    try:
        entry = gp.clean(body())
    except ValueError as exc:
        return err(str(exc))
    c.store.set_setting(gp.IDLE_KEY, entry)
    c.event("power", "idle power " + (gp.label(entry) if entry["on"] else "off"))
    _apply(c)
    return _payload(c)


@bp.post("/api/power/apply")
def power_apply():
    """Check what is running and set the cards now, instead of on the next check."""
    c = ctx()
    _apply(c)
    return _payload(c)


@bp.get("/api/power/compare")
def power_compare():
    """Benchmarks grouped by profile and power setting (``?profile=<name>`` for one)."""
    c = ctx()
    name = request.args.get("profile") or ""
    rows = gp.compare(c.store.list_runs(limit=2000))
    return ok(compare=[r for r in rows if not name or r["profile"] == name])


def _apply(c) -> None:
    if needed(c):
        try:
            tick(c)
        except Exception as exc:  # noqa: BLE001 - saved; the next check tries again
            c.event("power", f"could not set the GPUs' power now: {exc}", level="warn")


def bench_power(c, d: dict, profile_id: str) -> tuple[dict, bool]:
    """What a benchmark runs at, from its form's ``power``: "profile" (the profile's own setting), a setting
    ({cap_w, sclk_mhz}) to compare, or nothing (the cards as they are). Returns (meta, whether a setting is held)."""
    want = d.get("power")
    entry = None
    if want == "profile" and profile_id:
        e = gp.table(c.store).get(profile_id)
        entry = e if e and e.get("on") else None
    elif isinstance(want, dict):
        entry = gp.clean({**want, "on": True})
    if entry is not None:
        gp.GOV.hold = entry
        last = tick(c)
        if last.get("errors") or last.get("held") or not last.get("backend"):
            gp.GOV.hold = None
            tick(c)
            why = "; ".join((last.get("errors") or []) + (last.get("held") or [])) or last.get("why") or "no backend"
            raise RuntimeError(f"could not set the GPUs to {gp.label(entry)} for the benchmark: {why}")
        return {"power": {"label": gp.label(entry), **entry}}, True
    cards, _, _ = _cards(c)
    return {"power": {"label": now_label(cards)}}, False
