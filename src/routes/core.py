"""Commands, profiles, catalog, launches, perf sheet, integrations, settings."""
from __future__ import annotations

import hmac
import json
import time

from flask import Blueprint, abort, current_app, request

from ..command import guess_base_model, parse_command, swap_model_id, to_llama_swap_yaml, to_odysseus
from ..cookbook import build_state, write_state_file
from ..integrations import llama_swap, odysseus
from ..library import model_id_for_command
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
    return ok(**_render(cmd, _format(d)))


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
    out["llama_swap_yaml"] = to_llama_swap_yaml(d.get("name") or out["model_id"] or out["base_model"] or "model", cmd)
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
    c.event("profile", f"{'updated' if existed else 'saved'} profile “{profile['name']}”",
            data={"profile_id": profile["profile_id"], "model_id": profile["model_id"]})
    write_export()
    return ok(profile=profile)


@bp.delete("/api/profiles/<profile_id>")
def profiles_delete(profile_id):
    c = ctx()
    p = c.store.get_profile(check_id(profile_id))
    if not p or not c.store.delete_profile(profile_id):
        return err("not found", 404)
    c.event("profile", f"deleted profile “{p['name']}”", data={"profile_id": profile_id})
    write_export()
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
@bp.get("/api/launches")
def launches():
    r = ctx().runner
    return ok(launches=[{**x.to_dict(), "ready": r.health(x, timeout=0.3)} for x in r.list()])


@bp.post("/api/launches")
def launch():
    c, d = ctx(), body()
    command = (d.get("command") or "").strip()
    if not command:
        return err("command is required")
    name = (d.get("profile_name") or "").strip()
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
    c, run = ctx(), _run_or_404(launch_id)
    was_running = run.running
    c.runner.stop(run.launch_id)
    if was_running:
        c.event("launch", f"stopped {run.profile_name or run.base_model or run.launch_id}",
                data={"launch_id": run.launch_id})
    return ok(launch=run.to_dict())


@bp.get("/api/launches/<launch_id>/log")
def launch_log(launch_id):
    text = ctx().runner.read_log(check_id(launch_id), tail_bytes=request.args.get("tail", 0, type=int))
    if text is None:
        abort(404)
    return current_app.response_class(text, mimetype="text/plain")


@bp.post("/api/launches/<launch_id>/bench")
def launch_bench(launch_id):
    c, run, d = ctx(), _run_or_404(launch_id), body()
    try:
        words = max(1, min(int(d.get("prompt_words", 400)), 50_000))
        n_predict = max(1, min(int(d.get("n_predict", 128)), 8192))
    except (TypeError, ValueError):
        return err("prompt_words and n_predict must be integers")
    if not run.running:
        return err("process is not running")
    try:
        timings = c.runner.bench(run, prompt_words=words, n_predict=n_predict)
    except LaunchError as exc:
        c.event("bench", f"benchmark failed for {run.base_model or run.launch_id}: {exc}", level="error")
        return err(str(exc), 502)
    rec = c.recorder.record(run.launch_id, timings=timings, note=d.get("note") or "")
    c.event("bench", f"benchmark {rec['base_model']}: {rec['tokens_per_second'] or 0:.1f} t/s gen, "
                     f"{rec['prompt_per_second'] or 0:.0f} t/s prompt", data={"run_id": rec["run_id"]})
    return ok(run=rec)


@bp.post("/api/launches/<launch_id>/record")
def launch_record(launch_id):
    run = _run_or_404(launch_id)
    return ok(run=ctx().recorder.record(run.launch_id, note=body().get("note") or ""))


# ----- perf sheet -------------------------------------------------------------
@bp.get("/api/runs")
def runs_list():
    limit = max(1, min(request.args.get("limit", 1000, type=int), 100_000))
    return ok(runs=ctx().store.list_runs(request.args.get("base_model") or None, limit))


@bp.delete("/api/runs/<run_id>")
def runs_delete(run_id):
    return ok() if ctx().store.delete_run(check_id(run_id)) else err("not found", 404)


@bp.get("/api/analytics/best")
def analytics_best():
    return ok(rows=ctx().analytics.best_by_base_model())


@bp.get("/api/env-flags")
def env_flags():
    return ok(rows=ctx().analytics.env_table())


# ----- integrations + the Odysseus-compatible state llama-swap reads ------------
@bp.get("/api/integrations")
def integrations():
    c = ctx()
    i = c.settings.get("integrations")
    exp = c.settings.get("export")
    state = cookbook_state()
    return ok(odysseus=odysseus.describe(i["odysseus_cookbook"]),
              llama_swap=llama_swap.describe(i["llama_swap_config"], i["llama_swap_url"]),
              export={"file": exp["file"], "include_odysseus": exp["include_odysseus"],
                      "include_tasks": exp["include_tasks"], "token_required": bool(exp["token"]),
                      "presets": len(state["presets"]), "tasks": len(state["tasks"]),
                      "warnings": state["warnings"],
                      **{k: v for k, v in c.export_state.items() if not k.startswith("_")}})


def cookbook_state() -> dict:
    c = ctx()
    return build_state(c.store.list_profiles(), c.runner.list(), c.settings.get("export"),
                       c.settings.get("integrations")["odysseus_cookbook"])


@bp.get("/api/cookbook/state")
def cookbook_state_route():
    """Same path and shape as Odysseus, so llama-swap's odysseus.baseURL can point here."""
    token = ctx().settings.get("export")["token"]
    if token and not hmac.compare_digest(request.headers.get("X-Odysseus-Internal-Token", ""), token):
        return err("bad or missing X-Odysseus-Internal-Token", 401)
    state = cookbook_state()
    state.pop("warnings", None)
    return state


def write_export() -> None:
    """Rewrite the export file when one is configured and its content changed."""
    c = ctx()
    path = c.settings.get("export")["file"]
    if not path:
        return
    state = cookbook_state()
    blob = json.dumps({k: v for k, v in state.items() if k != "warnings"}, sort_keys=True)
    if blob == c.export_state.get("_last"):
        return
    try:
        write_state_file(path, state)
        c.export_state.update(_last=blob, written_at=time.time(), error="")
    except OSError as exc:
        if c.export_state.get("error") != str(exc):
            c.event("export", f"could not write {path}: {exc}", level="error")
        c.export_state.update(error=str(exc))


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
    if group in ("export", "integrations"):
        c.export_state.pop("_last", None)
        write_export()
    return ok(settings=c.settings.public()[group], saved=bool(saved))
