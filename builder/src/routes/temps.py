"""Profiles -> Temperature: each profile's sampling (applied to its requests, no reload), and temperature sweeps that
chart how many right answers and how varied the writing is at each temperature (temps.py)."""
from __future__ import annotations

import threading

from flask import Blueprint, request

from .. import temps as T
from ..web import body, ctx, err, ok

bp = Blueprint("temps", __name__)
SWEEPS: dict[str, T.Sweep] = {}
LOCK = threading.Lock()


def _rows(c) -> list[dict]:
    """Every profile with the name apps ask for, its full id, its sampling, whether it runs, and its last results."""
    from ..cookbook import _label_key
    from ..integrations import engine_api
    from .core import _profile_variants
    try:
        variants = _profile_variants()
    except Exception:  # noqa: BLE001 - no engine state yet: the labels still work
        variants = {}
    url = c.settings.get("integrations").get("engine_url")
    try:
        running = {m["model"] for m in engine_api.running(url, timeout=1.5)} if url else set()
    except Exception:  # noqa: BLE001
        running = set()
    rules, res = T.table(c.store), T.results(c.store)
    out = []
    for p in c.store.list_profiles():
        label = _label_key(p["name"])
        vid = variants.get(p["profile_id"]) or label
        names = {label, vid}
        out.append({"profile_id": p["profile_id"], "name": p["name"], "label": label, "model": vid,
                    "running": vid in running, "sampling": T.rule_for(vid, rules) or rules.get(label) or {},
                    "results": [r for r in res if r.get("model") in names]})
    return out


def _payload(c):
    with LOCK:
        live = [s.view() for s in SWEEPS.values()]
    return ok(profiles=_rows(c), sweeps=live, questions=len(T.QUESTIONS), temps=T.TEMPS)


@bp.get("/api/temps")
def temps_get():
    return _payload(ctx())


@bp.put("/api/temps/sampling")
def temps_sampling():
    """Body: {model (a profile label or full id), mode, temperature, top_p, top_k, min_p}; {model, clear: true} drops
    it. Applies to the model's next request."""
    c, d = ctx(), body()
    model = str(d.get("model") or "").strip()
    if not model:
        return err("say which model (a profile's label or full id)")
    rules = T.table(c.store)
    if d.get("clear"):
        rules.pop(model, None)
        c.event("profile", f"{model}: sampling back to what apps send")
    else:
        try:
            rules[model] = T.clean(d)
        except ValueError as exc:
            return err(str(exc))
        r = rules[model]
        c.event("profile", f"{model}: temperature {r.get('temperature', '-')} ({'forced' if r['mode'] == 'force' else 'when the app sends none'})")
    c.store.set_setting(T.KEY, rules)
    return _payload(c)


@bp.post("/api/temps/sweep")
def temps_sweep():
    """Body: {model, temps?, samples?, stories?, thinking?}: ask the model the sweep's questions at each temperature.
    The model must be loaded (or loadable) by the engine; it runs in the background, one at a time."""
    c, d = ctx(), body()
    model = str(d.get("model") or "").strip()
    url = c.settings.get("integrations").get("engine_url")
    if not model:
        return err("say which model")
    if not url:
        return err("no engine to ask (Settings -> Integrations)")
    try:
        temps = sorted({round(float(t), 2) for t in (d.get("temps") or T.TEMPS)})
        samples = int(d.get("samples") or 1)
        stories = int(d.get("stories") or 4)
    except (TypeError, ValueError, OverflowError):
        return err("temps is a list of numbers; samples and stories are numbers")
    if not temps or len(temps) > 12 or any(not 0 <= t <= 2 for t in temps):
        return err("1 to 12 temperatures, each from 0 to 2")
    if not (1 <= samples <= 5 and 2 <= stories <= 8):
        return err("samples is 1-5, stories 2-8")
    with LOCK:
        if any(s.state == "running" for s in SWEEPS.values()):
            return err("a sweep is running: one at a time, so they do not slow each other", 409)
        s = T.Sweep(c.store, url, model, temps, samples, stories, thinking=bool(d.get("thinking")))
        SWEEPS.clear()
        SWEEPS[s.id] = s
    threading.Thread(target=s.run, name="temp-sweep", daemon=True).start()
    c.event("profile", f"temperature sweep of {model}: {len(temps)} temperatures, {s.total_n} requests")
    return _payload(c)


@bp.post("/api/temps/sweep/<sid>/stop")
def temps_sweep_stop(sid):
    with LOCK:
        s = SWEEPS.get(sid)
    if not s:
        return err("no such sweep", 404)
    s.stop.set()
    return _payload(ctx())


@bp.post("/api/temps/results")
def temps_results_add():
    """Body: {model, label, points: [{temp, passed, total, broken?}]}: results measured elsewhere, drawn with sweeps."""
    c = ctx()
    try:
        r = T.save_result(c.store, T.clean_manual(body()))
    except ValueError as exc:
        return err(str(exc))
    c.event("profile", f"{r['model']}: added {len(r['points'])} temperature result(s) ({r['label']})")
    return _payload(c)


@bp.delete("/api/temps/results/<rid>")
def temps_results_drop(rid):
    c = ctx()
    c.store.set_setting(T.RESULTS_KEY, [r for r in T.results(c.store) if r.get("id") != rid])
    return _payload(c)
