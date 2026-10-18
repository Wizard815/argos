"""Odysseus hub: its Notes, Brain, Calendar, Library, Cookbook, Tasks, Gallery and Tools from here,
and sending what the builder knows (a preset, its measured speeds) into Odysseus."""
from __future__ import annotations

import time
from datetime import datetime, timedelta
from typing import Any

from flask import Blueprint

from ..integrations.odysseus_api import AREAS, Odysseus, OdysseusError, as_list
from ..model_perf import model_perf
from ..web import body, ctx, err, ok

bp = Blueprint("odysseus_hub", __name__)


def client() -> Odysseus:
    o = ctx().settings.get("odysseus")
    return Odysseus(o["url"], o["token"])


def cookbook_presets() -> dict:
    """Odysseus' saved Cookbook presets, read through its API: ``{"presets": [...], "error": ""}``.

    Needs only the Odysseus URL and token. A short cache keeps the engine's polling from hammering Odysseus.
    """
    c = ctx()
    o = c.settings.get("odysseus")
    if not (o["url"] and o["token"] and o.get("use_presets")):
        return {"presets": [], "error": ""}
    cached = c.export_state.get("_ody_presets")
    if cached and cached["key"] == (o["url"], o["token"]) and time.time() - cached["at"] < 20:
        return cached["value"]
    try:
        value = {"presets": [p for p in as_list(client().cookbook()["presets"], "presets") if isinstance(p, dict)], "error": ""}
    except OdysseusError as exc:
        value = {"presets": [], "error": str(exc)}
    c.export_state["_ody_presets"] = {"at": time.time(), "key": (o["url"], o["token"]), "value": value}
    return value


def _fail(exc: OdysseusError):
    return err(str(exc), 502 if not exc.status else (exc.status if exc.status in (401, 403, 404) else 502))


# the cheapest read that proves an area exists on this Odysseus
_PROBES = {"notes": "notes", "brain": "memories", "calendar": "events", "library": "documents", "cookbook": "cookbook",
           "gallery": "gallery", "tasks": "tasks", "tools": "tools"}


def _probe_areas(o: Odysseus) -> dict:
    """For an Odysseus with no /capabilities: try each area, and call it available unless it answers 404."""
    found = {}
    for area, method in _PROBES.items():
        try:
            getattr(o, method)()
            found[area] = True
        except OdysseusError as exc:
            found[area] = exc.status not in (404, 405)
    return found


@bp.get("/api/ody/status")
def status():
    o = ctx().settings.get("odysseus")
    if not (o["url"] and o["token"]):
        return ok(configured=False, areas={a: False for a in AREAS})
    try:
        c = client()
        caps = c.capabilities()
    except OdysseusError as exc:
        if exc.status == 404:  # an older or different Odysseus: no capabilities route, so look for each area
            try:
                areas = _probe_areas(client())
            except OdysseusError as exc2:
                return ok(configured=True, connected=False, error=str(exc2), areas={a: False for a in AREAS})
            return ok(configured=True, connected=True, areas=areas, writable={}, scopes=[], updated=False, capabilities=False)
        return ok(configured=True, connected=False, error=str(exc), areas={a: False for a in AREAS})
    tools = caps.get("tools") or {}
    scopes = set(caps.get("token_scopes") or [])

    def has(tool: str) -> bool:
        return bool((tools.get(tool) or {}).get("read"))

    areas = {"notes": has("todos"), "brain": has("memory"), "calendar": has("calendar"), "library": has("documents"),
             "cookbook": has("cookbook"), "gallery": has("gallery"), "tasks": has("tasks"), "tools": has("skills")}
    writable = {"notes": bool((tools.get("todos") or {}).get("write")), "brain": bool((tools.get("memory") or {}).get("write")),
                "calendar": bool((tools.get("calendar") or {}).get("write")), "library": bool((tools.get("documents") or {}).get("write")),
                "tasks": bool((tools.get("tasks") or {}).get("run"))}
    return ok(configured=True, connected=True, areas=areas, writable=writable, scopes=sorted(scopes),
              updated=any(k in tools for k in ("gallery", "tasks", "skills")), capabilities=True)


@bp.get("/api/ody/<area>")
def area_list(area):
    if area not in AREAS:
        return err("unknown area", 404)
    try:
        c = client()
        if area == "notes":
            data = c.notes()
            return ok(text=data.get("results") or data.get("response") or "", items=as_list(data, "notes"))
        if area == "brain":
            return ok(items=as_list(c.memories(), "memories"))
        if area == "calendar":
            return ok(items=as_list(c.events(), "events"))
        if area == "library":
            return ok(items=as_list(c.documents(), "documents", "library"))
        if area == "cookbook":
            cb = c.cookbook()
            return ok(items=cb["presets"], servers=as_list(cb["servers"], "servers"))
        if area == "gallery":
            return ok(items=as_list(c.gallery(), "images"))
        if area == "tasks":
            return ok(items=as_list(c.tasks(), "tasks"))
        return ok(items=as_list(c.tools(), "skills"))
    except OdysseusError as exc:
        if exc.status in (404, 405):
            return ok(items=[], unavailable=True, text="")
        return _fail(exc)


@bp.post("/api/ody/<area>")
def area_add(area):
    d = body()
    try:
        c = client()
        if area == "notes":
            if not (d.get("title") or "").strip():
                return err("a note needs a title")
            return ok(result=c.add_note(d["title"].strip(), str(d.get("content") or "")))
        if area == "brain":
            if not (d.get("text") or "").strip():
                return err("write what the Brain should remember")
            return ok(result=c.remember(d["text"].strip(), str(d.get("category") or "fact")))
        if area == "calendar":
            if not (d.get("summary") or "").strip() or not d.get("start"):
                return err("an event needs a title and a start time")
            return ok(result=c.add_event(d["summary"].strip(), d["start"], d.get("end") or "", str(d.get("description") or "")))
        if area == "library":
            if not (d.get("title") or "").strip():
                return err("a document needs a title")
            return ok(result=c.add_document(d["title"].strip(), str(d.get("content") or "")))
        return err("nothing can be added there from here", 400)
    except OdysseusError as exc:
        return _fail(exc)


@bp.post("/api/ody/tasks/<task_id>/run")
def run_task(task_id):
    try:
        return ok(result=client().run_task(task_id))
    except OdysseusError as exc:
        return _fail(exc)


# ----- from the builder ------------------------------------------------------------------------
def preset_summary(profile: dict) -> tuple[str, str]:
    """(title, markdown) describing a preset and how fast it has been."""
    mid = profile.get("model_id") or profile.get("base_model") or ""
    lines = [f"# {profile['name']} — {mid}", "", "Launch command:", "", "```", profile["launch_command"], "```", ""]
    perf = model_perf(ctx().store, mid) if mid else None
    if perf and perf["configs"]:
        lines += ["| Launch config | Requests | Prefill t/s (median) | Decode t/s (median) | Most at once |", "|---|---|---|---|---|"]
        for c in perf["configs"]:
            lines.append(f"| {c['label']} | {c['requests'] + c['benchmarks']} | {c['prefill']['median'] or '-'} | "
                         f"{c['decode']['median'] or '-'} | {c['concurrency_max'] or '-'} |")
    lines += ["", f"_From Argos, {datetime.now().strftime('%Y-%m-%d %H:%M')}_"]
    return f"{profile['name']} ({mid})", "\n".join(lines)


@bp.post("/api/ody/pull")
def pull():
    """Turn Odysseus's Cookbook presets into Argos profiles.

    Each preset becomes a profile named after its label and carrying its command, so
    it can be applied, launched, benchmarked and diffed like any other. A preset
    Argos cannot run is reported with its reason rather than dropped: a silent zero
    is what made this look like it had done nothing at all.

    Pulling is idempotent. A preset already here with the same command is skipped, and
    one that changed in Odysseus replaces the profile it made before, because the edit
    you made over there is the newer intent.
    """
    from ..command import guess_base_model, parse_command, swap_model_id
    from ..cookbook import normalize_preset

    c = ctx()
    data = cookbook_presets()
    if data.get("error"):
        return err(f"Odysseus: {data['error']}", 502)
    presets = [p for p in (data.get("presets") or []) if isinstance(p, dict)]
    if not presets:
        return ok(imported=[], updated=[], skipped=[], failed=[],
                  note="Odysseus has no Cookbook presets to pull, or presets are switched off under Settings → Odysseus.")

    here = {p.get("name"): p for p in c.store.list_profiles() if p.get("source") == "odysseus"}
    imported, updated, skipped, failed = [], [], [], []
    for raw in presets:
        p = normalize_preset(raw)
        label = (p.get("label") or p.get("name") or "").strip()
        cmd = (p.get("cmd") or "").strip()
        if not cmd:
            failed.append({"label": label or "(unnamed)", "reason": "the preset has no command"})
            continue
        try:
            parsed = parse_command(cmd)
        except ValueError as exc:
            failed.append({"label": label or "(unnamed)", "reason": str(exc)})
            continue
        model = parsed.summary()["model"]
        if not model.lower().endswith(".gguf"):
            failed.append({"label": label or "(unnamed)",
                           "reason": f"no .gguf model path in the command (found {model or 'nothing'})"})
            continue
        name = label or swap_model_id(model)
        was = here.get(name)
        if was and (was.get("launch_command") or "").strip() == cmd:
            skipped.append({"label": name, "reason": "already here, unchanged"})
            continue
        profile = c.store.save_profile({
            "profile_id": was["profile_id"] if was else None,
            "name": name, "base_model": guess_base_model(model), "model_id": swap_model_id(model),
            "source": "odysseus", "launch_command": cmd, "notes": "Pulled from Odysseus",
        })
        row = {"label": name, "profile_id": profile["profile_id"], "model_id": profile.get("model_id") or ""}
        (updated if was else imported).append(row)
    c.event("odysseus", f"pulled {len(imported)} new and {len(updated)} changed preset(s) from Odysseus"
                        + (f"; {len(failed)} could not be used" if failed else ""))
    return ok(imported=imported, updated=updated, skipped=skipped, failed=failed)


@bp.post("/api/ody/send")
def send():
    """Send a preset to Odysseus as a note, a Brain memory, a Library document, or a calendar reminder."""
    c, d = ctx(), body()
    profile = c.store.get_profile(d.get("profile_id") or "")
    if not profile:
        return err("save the preset first", 404)
    what = d.get("what")
    title, text = preset_summary(profile)
    try:
        o = client()
        if what == "note":
            result = o.add_note(title, text)
        elif what == "brain":
            perf = model_perf(c.store, profile.get("model_id") or "")
            best = max((x for x in perf["configs"] if x["decode"]["median"]), key=lambda x: x["decode"]["median"], default=None)
            fact = f"For model {profile.get('model_id')}, the preset “{profile['name']}” launches with: {profile['launch_command']}"
            if best:
                fact += f". Fastest measured config: {best['label']} at {best['decode']['median']} t/s decode."
            result = o.remember(fact, "fact")
        elif what == "document":
            result = o.add_document(title, text)
        elif what == "event":
            start = d.get("start") or (datetime.now() + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0).isoformat()
            result = o.add_event(f"Benchmark {profile['name']}", start, d.get("end") or "", text)
        else:
            return err("what must be note, brain, document or event")
    except OdysseusError as exc:
        return _fail(exc)
    c.event("odysseus", f"sent “{profile['name']}” to Odysseus as a {what}", data={"profile_id": profile["profile_id"]})
    return ok(result=result)


def brain_source(settings_fn, limit: int = 500):
    """Odysseus' Brain as a source for the assistant's memory (when connected and allowed)."""
    def odysseus_brain() -> list[tuple[str, str, float, str]]:
        s = settings_fn()
        o = s["odysseus"]
        if not (o["url"] and o["token"] and o.get("brain_in_memory")):
            return []
        items = as_list(Odysseus(o["url"], o["token"], timeout=10).memories(), "memories")[:limit]
        out = []
        for m in items:
            if not isinstance(m, dict):
                continue
            text = m.get("text") or m.get("content") or m.get("memory") or ""
            if text:
                out.append(("odysseus-brain", str(m.get("id") or hash(text)), _ts(m.get("created_at") or m.get("timestamp")),
                            f"Odysseus Brain ({m.get('category') or 'memory'}): {text}"))
        return out
    return odysseus_brain


def _ts(value: Any) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError):
        return 0.0


__all__ = ["bp", "brain_source", "preset_summary"]
