"""Commands, profiles, catalog, launches, perf sheet, integrations, settings."""
from __future__ import annotations

import hmac
import json
import os
import re
import time
from urllib.parse import quote

from flask import Blueprint, abort, current_app, request

from ..command import guess_base_model, parse_command, swap_model_id, to_odysseus
from ..always import apply_always
from ..cookbook import build_state, normalize_preset
from ..integrations import engine_api
from ..library import model_id_for_command
from ..log_analyzer import insight as log_insight
from ..model_perf import model_perf, signature
from ..runner import LaunchError
from ..web import body, check_id, ctx, err, ok

bp = Blueprint("core", __name__)
CATALOG_KINDS = ("mmproj", "draft")


def _render(cmd, fmt: str) -> dict:
    if fmt == "odysseus":
        port = cmd.summary()["port"] or (cmd.get("port") or 8080)
        cmd = to_odysseus(cmd, port=port)
    d = cmd.to_dict()
    style = "odysseus" if fmt == "odysseus" else "plain"
    d["command"], d["multiline"] = cmd.to_string(style=style), cmd.to_string(multiline=True, style=style)
    model = cmd.summary()["model"]
    d["base_model"] = guess_base_model(model)
    d["model_id"] = swap_model_id(model) if model.lower().endswith(".gguf") else ""
    d["format"] = fmt
    return d


def _format(d: dict) -> str:
    fmt = d.get("format") or ctx().settings.get("builder")["command_format"]
    return fmt if fmt in ("odysseus", "plain") else "plain"


# ----- commands -----------------------------------------------------------
@bp.post("/api/command/parse")
def command_parse():
    d = body()
    try:
        cmd = parse_command(d.get("command") or "")
    except ValueError as exc:
        return err(str(exc))
    return ok(**_render(cmd, _format(d)), always_added=apply_always(cmd.to_string(), ctx().settings.get("always"))[1])


@bp.post("/api/command/build")
def command_build():
    """Apply ``edits`` ({flag: value|true|null}), ``arg_edits`` and ``env`` to ``command``."""
    d = body()
    edits, env, arg_edits = d.get("edits") or {}, d.get("env"), d.get("arg_edits") or {}
    if not all(isinstance(x, dict) for x in (edits, arg_edits, env if env is not None else {})):
        return err("edits, arg_edits and env must be objects")
    try:
        cmd = parse_command(d.get("command") or "").with_edits(edits, env, arg_edits)
    except ValueError as exc:
        return err(str(exc))
    out = _render(cmd, _format(d))
    out["always_added"] = apply_always(cmd.to_string(), ctx().settings.get("always"))[1]
    return ok(**out)


# ----- profiles (no limit, any number per model) --------------------------
@bp.get("/api/profiles")
def profiles_list():
    profiles = ctx().store.list_profiles(request.args.get("model_id") or None)
    for p in profiles:
        try:
            p["summary"] = parse_command(p["launch_command"]).summary()
        except ValueError:
            p["summary"] = {}
    return ok(profiles=profiles)


@bp.post("/api/profiles")
def profiles_save():
    c, d = ctx(), body()
    if not (d.get("name") or "").strip() or not (d.get("launch_command") or "").strip():
        return err("name and launch_command are required")
    try:
        parse_command(d["launch_command"])
    except ValueError as exc:
        return err(f"launch_command: {exc}")
    if d.get("profile_id"):
        check_id(d["profile_id"])
    d["model_id"] = model_id_for_command(d["launch_command"])
    existed = bool(d.get("profile_id") and c.store.get_profile(d["profile_id"]))
    profile = c.store.save_profile(d)
    c.inventory.observe_command(d["launch_command"], profile["name"])
    c.event("profile", f"{'updated' if existed else 'saved'} profile “{profile['name']}”",
            data={"profile_id": profile["profile_id"], "model_id": profile["model_id"]})
    refresh_schedule()
    return ok(profile=profile)


@bp.delete("/api/profiles/<profile_id>")
def profiles_delete(profile_id):
    c = ctx()
    p = c.store.get_profile(check_id(profile_id))
    if not p or not c.store.delete_profile(profile_id):
        return err("not found", 404)
    c.event("profile", f"deleted profile “{p['name']}”", data={"profile_id": profile_id})
    refresh_schedule()
    return ok()


# ----- catalog of extra files ---------------------------------------------
@bp.get("/api/catalog")
def catalog_list():
    return ok(files=ctx().store.list_files(request.args.get("kind") or None,
                                           request.args.get("base_model") or None))


@bp.post("/api/catalog")
def catalog_add():
    d = body()
    kind, base = d.get("kind"), (d.get("base_model") or "").strip()
    if kind not in CATALOG_KINDS:
        return err(f"kind must be one of {', '.join(CATALOG_KINDS)}")
    if not base or not (d.get("file_path") or d.get("hf_repo")):
        return err("base_model and a file path or HF repo are required")
    fid = ctx().store.add_file(kind, base, name=(d.get("name") or "").strip(),
                               hf_repo=(d.get("hf_repo") or "").strip(),
                               file_path=(d.get("file_path") or "").strip(), notes=(d.get("notes") or "").strip())
    return ok(id=fid)


@bp.delete("/api/catalog/<int:file_id>")
def catalog_delete(file_id):
    return ok() if ctx().store.delete_file(file_id) else err("not found", 404)


@bp.get("/api/models")
def models():
    return ok(models=ctx().store.base_models())


# ----- launches -------------------------------------------------------------
ENGINE_PREFIX = "engine:"


def _engine_running(c) -> list[dict]:
    """Models the engine runs, as launches: the Playground benchmarks and reads them like its own."""
    url = c.settings.get("integrations").get("engine_url")
    if not url:
        return []
    try:
        running = engine_api.running(url, timeout=1.5)
    except Exception:  # noqa: BLE001 - an unreachable engine just adds nothing
        return []
    seen = {mid: {"started_at": t} for mid, t in c.store.open_session_starts().items()}
    out = []
    for m in running:
        mid, state = str(m["model"]), str(m.get("state") or "ready")
        out.append({"launch_id": ENGINE_PREFIX + mid, "command": m.get("cmd") or "", "base_model": mid,
                    "profile_name": "", "host": "engine", "port": None,
                    "started_at": (seen.get(mid) or {}).get("started_at") or time.time(),
                    "running": state in ("ready", "starting"), "ready": state == "ready", "exit_code": None,
                    "stopped": False, "engine": True, "state": state})
    return out


class _EngineRun:
    """What the Recorder needs of a launch, for a model the engine runs."""

    def __init__(self, launch: dict):
        self.launch_id = launch["launch_id"]
        self.command = launch["command"]
        self.base_model = launch["base_model"]
        self.profile_name = ""
        self.port = None
        try:
            self.env = dict(parse_command(self.command).env) if self.command else {}
        except ValueError:
            self.env = {}


def _engine_launch(launch_id: str):
    """(engine url, the launch) for an ``engine:<model>`` id, or None when it is not one; 404 when not running."""
    if not launch_id.startswith(ENGINE_PREFIX):
        return None
    c = ctx()
    found = next((x for x in _engine_running(c) if x["launch_id"] == launch_id), None)
    if found is None:
        abort(404)
    return c.settings.get("integrations")["engine_url"], found


# llama-server colours its own log; shown as text the codes are noise ("[34m0.26.458[0m").
# The ESC byte is optional: some paths drop it and leave the rest behind.
ANSI = re.compile(r"\x1b?\[\d{1,3}(?:;\d{1,3})*m")


def _engine_log(url: str, model_id: str) -> str:
    try:
        return ANSI.sub("", engine_api.model_log(url, model_id))
    except Exception as exc:  # noqa: BLE001 - shown in place of the log
        return f"(could not read the engine's log of {model_id}: {exc})"


@bp.get("/api/engine/log/<which>")
def engine_log(which):
    """The engine's own log: "proxy" (routing, loads, the scheduler) or "upstream" (every model's output)."""
    if which not in ("proxy", "upstream"):
        abort(404)
    url = ctx().settings.get("integrations").get("engine_url")
    if not url:
        return err("the engine is not running", 503)
    tail = request.args.get("tail", 0, type=int)
    text = _engine_log(url, which)
    return current_app.response_class(text[-tail:] if tail else text, mimetype="text/plain")


@bp.get("/api/launches")
def launches():
    c = ctx()
    r = c.runner
    return ok(launches=[{**x.to_dict(), "ready": r.health(x, timeout=0.3)} for x in r.list()] + _engine_running(c))


def _engine_model(c, command: str) -> str:
    """The engine's own id for the model a command starts, or "" when it is not its model.

    A build whose llama.cpp lives in another container has no llama-server here to
    run, so a model the engine already serves belongs to the engine: it loads it on
    whichever host it is configured to launch on, with no launcher string to set.
    """
    url = c.settings.get("integrations").get("engine_url")
    model_id = model_id_for_command(command)
    if not url or not model_id:
        return ""
    try:
        return model_id if model_id in engine_api.model_ids(url) else ""
    except Exception:  # noqa: BLE001 - an unreachable engine is not this route's error
        return ""


def _start_in_engine(c, model_id: str, name: str, command: str):
    """Hand the load to the engine. A large model takes minutes, and its progress and
    log live on the Schedule page, so this only starts it and reports that it did."""
    import threading
    url = c.settings.get("integrations")["engine_url"]

    def load():
        try:
            engine_api.load_model(url, model_id)
        except Exception as exc:  # noqa: BLE001 - reported as an event
            c.event("launch", f"the engine could not load {model_id}: {exc}", level="error",
                    data={"model": model_id})
            return
        c.event("launch", f"the engine loaded {model_id}", data={"model": model_id})

    threading.Thread(target=load, daemon=True).start()
    c.event("launch", f"asked the engine to load {name or model_id}",
            data={"model": model_id, "command": command[:500]})
    return ok(engine=True, model=model_id, name=name or model_id)


@bp.post("/api/launches")
def launch():
    c, d = ctx(), body()
    command = (d.get("command") or "").strip()
    if not command:
        return err("command is required")
    name = (d.get("profile_name") or "").strip()
    always = c.settings.get("always")
    if always["on_launch"]:
        command = apply_always(command, always)[0]
    served = _engine_model(c, command)
    if served:
        return _start_in_engine(c, served, name, command)
    try:
        run = c.runner.launch(command, base_model=(d.get("base_model") or "").strip(), profile_name=name)
    except LaunchError as exc:
        c.event("launch", f"launch failed: {exc}", level="error", data={"command": command[:500]})
        return err(str(exc))
    c.event("launch", f"launched {name or run.base_model or run.launch_id} on :{run.port}",
            data={"launch_id": run.launch_id, "command": run.command})
    return ok(launch=run.to_dict())


def _run_or_404(launch_id):
    run = ctx().runner.get(check_id(launch_id))
    if run is None:
        abort(404)
    return run


@bp.get("/api/launches/<launch_id>")
def launch_status(launch_id):
    run = _run_or_404(launch_id)
    return ok(launch={**run.to_dict(), "ready": ctx().runner.health(run)})


@bp.post("/api/launches/<launch_id>/stop")
def launch_stop(launch_id):
    eng = _engine_launch(launch_id)
    if eng:
        url, launch = eng
        try:
            engine_api.unload_model(url, launch["base_model"])
        except Exception as exc:  # noqa: BLE001
            return err(f"the engine could not unload {launch['base_model']}: {exc}", 502)
        ctx().event("launch", f"unloaded {launch['base_model']} from the playground", data={"model": launch["base_model"]})
        return ok(launch={**launch, "running": False, "stopped": True})
    c, run = ctx(), _run_or_404(launch_id)
    was_running = run.running
    c.runner.stop(run.launch_id)
    if was_running:
        c.event("launch", f"stopped {run.profile_name or run.base_model or run.launch_id}",
                data={"launch_id": run.launch_id})
    return ok(launch=run.to_dict())


@bp.get("/api/launches/<launch_id>/log")
def launch_log(launch_id):
    eng = _engine_launch(launch_id)
    if eng:
        tail = request.args.get("tail", 0, type=int)
        text = _engine_log(eng[0], eng[1]["base_model"])
        return current_app.response_class(text[-tail:] if tail else text, mimetype="text/plain")
    text = ctx().runner.read_log(check_id(launch_id), tail_bytes=request.args.get("tail", 0, type=int))
    if text is None:
        abort(404)
    return current_app.response_class(ANSI.sub("", text), mimetype="text/plain")


@bp.get("/api/launches/<launch_id>/insight")
def launch_insight(launch_id):
    eng = _engine_launch(launch_id)
    text = (_engine_log(eng[0], eng[1]["base_model"]) if eng
            else ctx().runner.read_log(check_id(launch_id), tail_bytes=1_000_000))
    if text is None:
        abort(404)
    return ok(**log_insight(text))


@bp.post("/api/similar")
def similar_configs():
    """Configs and presets closest to a command: by meaning when embeddings are on, by words otherwise."""
    c, d = ctx(), body()
    command = str(d.get("command") or "")
    _cid, settings = signature(command)
    model = str(d.get("model") or settings.get("model") or "")
    query = f"Config of {model}: " + ", ".join(f"{k} {v}" for k, v in settings.items() if k not in ("model", "mmproj") and v is not True)[:600]
    hits = c.memory.search(query, max(1, min(int(d.get("k") or 6), 20)), {"config", "preset"})
    out = []
    for hit in hits:
        row = {"source": hit["source"], "key": hit["key"], "text": hit["text"], "similarity": hit["similarity"], "matched_by": hit["matched_by"]}
        if hit["source"] == "config":
            mid, _, cfg = hit["key"].partition("|")
            conf = next((x for x in model_perf(c.store, mid, days=180)["configs"] if x["id"] == cfg), None)
            if conf:
                row.update(model=mid, label=conf["label"], decode=conf["decode"].get("median"), prefill=conf["prefill"].get("median"))
        out.append(row)
    return ok(results=out)


@bp.post("/api/launches/<launch_id>/bench")
def launch_bench(launch_id):
    c, d = ctx(), body()
    try:
        words = max(1, min(int(d.get("prompt_words", 400)), 50_000))
        n_predict = max(1, min(int(d.get("n_predict", 128)), 8192))
    except (TypeError, ValueError):
        return err("prompt_words and n_predict must be integers")
    eng = _engine_launch(launch_id)
    if eng:
        url, launch = eng
        mid = launch["base_model"]
        try:
            timings = c.runner.bench_url(f"{url.rstrip('/')}/upstream/{quote(mid, safe='')}/completion",
                                         prompt_words=words, n_predict=n_predict, where=f"through the engine ({mid})")
        except LaunchError as exc:
            c.event("bench", f"benchmark failed for {mid}: {exc}", level="error")
            return err(str(exc), 502)
        rec = c.recorder.record(launch_id, timings=timings, note=d.get("note") or "", run=_EngineRun(launch),
                                log_text=_engine_log(url, mid))
        c.assistant.warm_embed()  # the new numbers are worth searching: start the embedder
        c.event("bench", f"benchmark {rec['base_model']}: {rec['tokens_per_second'] or 0:.1f} t/s gen, "
                         f"{rec['prompt_per_second'] or 0:.0f} t/s prompt", data={"run_id": rec["run_id"]})
        return ok(run=rec)
    run = _run_or_404(launch_id)
    if not run.running:
        return err("process is not running")
    try:
        timings = c.runner.bench(run, prompt_words=words, n_predict=n_predict)
    except LaunchError as exc:
        c.event("bench", f"benchmark failed for {run.base_model or run.launch_id}: {exc}", level="error")
        return err(str(exc), 502)
    rec = c.recorder.record(run.launch_id, timings=timings, note=d.get("note") or "")
    c.assistant.warm_embed()  # the new numbers are worth searching: start the embedder
    c.event("bench", f"benchmark {rec['base_model']}: {rec['tokens_per_second'] or 0:.1f} t/s gen, "
                     f"{rec['prompt_per_second'] or 0:.0f} t/s prompt", data={"run_id": rec["run_id"]})
    return ok(run=rec)


@bp.post("/api/launches/<launch_id>/record")
def launch_record(launch_id):
    eng = _engine_launch(launch_id)
    if eng:
        rec = ctx().recorder.record(launch_id, note=body().get("note") or "", run=_EngineRun(eng[1]),
                                    log_text=_engine_log(eng[0], eng[1]["base_model"]))
        return ok(run=rec)
    run = _run_or_404(launch_id)
    rec = ctx().recorder.record(run.launch_id, note=body().get("note") or "")
    ctx().assistant.warm_embed()  # the new numbers are worth searching: start the embedder
    return ok(run=rec)


# ----- perf sheet -------------------------------------------------------------
@bp.get("/api/runs")
def runs_list():
    limit = max(1, min(request.args.get("limit", 1000, type=int), 100_000))
    runs = ctx().store.list_runs(request.args.get("base_model") or None, limit)
    # The count comes back either way, so a caller never has to ask for a hundred
    # thousand rows to find out how many there are.
    return ok(runs=runs, total=ctx().store.count_runs())


@bp.delete("/api/runs/<run_id>")
def runs_delete(run_id):
    return ok() if ctx().store.delete_run(check_id(run_id)) else err("not found", 404)


@bp.get("/api/analytics/best")
def analytics_best():
    return ok(rows=ctx().analytics.best_by_base_model())


@bp.get("/api/env-flags")
def env_flags():
    return ok(rows=ctx().analytics.env_table())


@bp.get("/api/flags")
def server_flags():
    return ok(rows=ctx().analytics.flag_table())


# ----- your changes to the Flags page ----------------------------------------------------
def _flag_known(kind: str) -> set[str]:
    a = ctx().analytics
    return {r["flag"] for r in (a.flag_table(edits=False) if kind == "flag" else a.env_table(edits=False))}


@bp.post("/api/flags/<kind>")
def flag_add(kind):
    """Add a flag (kind "flag") or an environment variable (kind "env") of your own."""
    from ..flag_edits import KINDS, FlagEditError, FlagEdits
    if kind not in KINDS:
        return err("kind is flag or env", 404)
    try:
        name = FlagEdits(ctx().store).add(kind, body(), _flag_known(kind))
    except FlagEditError as exc:
        return err(str(exc))
    ctx().event("settings", f"added {kind} {name} to the Flags page")
    return ok(name=name)


@bp.put("/api/flags/<kind>/<path:name>")
def flag_update(kind, name):
    from ..flag_edits import KINDS, FlagEditError, FlagEdits
    if kind not in KINDS:
        return err("kind is flag or env", 404)
    edits = FlagEdits(ctx().store)
    if name not in _flag_known(kind) and name not in edits.all()[kind]:
        return err("not on the list", 404)
    try:
        edits.update(kind, name, body())
    except FlagEditError as exc:
        return err(str(exc))
    return ok()


@bp.delete("/api/flags/<kind>/<path:name>")
def flag_remove(kind, name):
    """A row you added is deleted; a built-in one is hidden and can be restored."""
    from ..flag_edits import KINDS, FlagEdits
    if kind not in KINDS:
        return err("kind is flag or env", 404)
    edits = FlagEdits(ctx().store)
    if name not in _flag_known(kind) and name not in edits.all()[kind]:
        return err("not on the list", 404)
    result = edits.remove(kind, name)
    ctx().event("settings", f"{'deleted' if result == 'deleted' else 'hid'} {kind} {name} on the Flags page")
    return ok(result=result)


@bp.post("/api/flags/<kind>/<path:name>/restore")
def flag_restore(kind, name):
    """Bring a built-in row back as shipped: shown again, with its original texts."""
    from ..flag_edits import KINDS, FlagEdits
    if kind not in KINDS:
        return err("kind is flag or env", 404)
    return ok() if FlagEdits(ctx().store).restore(kind, name) else err("nothing to restore", 404)


# ----- Odysseus presets, and the Odysseus-compatible state the engine reads -------------
@bp.get("/api/integrations")
def integrations():
    """What the builder can import from Odysseus: its saved Cookbook presets, read through its API."""
    from .odysseus_hub import cookbook_presets

    found = cookbook_presets()
    commands = [{"source": "odysseus", "kind": "preset", "name": p.get("label") or p.get("name") or "preset",
                 "base_model": str(p.get("model") or "").split("/")[-1], "command": p["cmd"].strip()}
                for p in map(normalize_preset, found["presets"]) if p["cmd"].strip()]
    return ok(odysseus={"configured": bool(c_odysseus_url()), "ok": not found["error"], "error": found["error"], "commands": commands})


def c_odysseus_url() -> str:
    return ctx().settings.get("odysseus")["url"]


def cookbook_state() -> dict:
    from .odysseus_hub import cookbook_presets

    c = ctx()
    always = c.settings.get("always")
    return build_state(c.store.list_profiles(), c.runner.list(), cookbook_presets(), always if always["on_export"] else None)


@bp.get("/api/cookbook/state")
def cookbook_state_route():
    """Same path and shape as Odysseus: the engine's odysseus.baseURL points here. ARGOS_COOKBOOK_TOKEN, when set, is required."""
    token = os.environ.get("ARGOS_COOKBOOK_TOKEN", "")
    if token and not hmac.compare_digest(request.headers.get("X-Odysseus-Internal-Token", ""), token):
        return err("bad or missing X-Odysseus-Internal-Token", 401)
    state = cookbook_state()
    state.pop("warnings", None)
    return state


def refresh_schedule() -> None:
    """A profile was saved or deleted: that adds or removes a model in the scheduling plan."""
    from . import schedule

    schedule.apply_plan()


# ----- settings -------------------------------------------------------------------
@bp.get("/api/settings")
def settings_get():
    return ok(settings=ctx().settings.public())


@bp.put("/api/settings/<group>")
def settings_put(group):
    c = ctx()
    try:
        saved = c.settings.update(group, body())
    except (KeyError, ValueError) as exc:
        return err(str(exc).strip("'\""))
    c.event("settings", f"saved {group} settings")
    if group in ("odysseus", "always"):
        c.export_state.pop("_ody_presets", None)
    return ok(settings=c.settings.public()[group], saved=bool(saved))
