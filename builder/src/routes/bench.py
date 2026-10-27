"""Playground -> llama-bench: run llama.cpp's own benchmark on a model, live, and keep the results."""
from __future__ import annotations

from flask import Blueprint, request

from ..bench import build, params_from_command
from ..gpu_pick import resolve as resolve_gpus
from ..command import parse_command
from ..servers import launch_parts
from ..web import body, check_id, ctx, err, ok

bp = Blueprint("bench", __name__)


def _base_command(c, d) -> tuple[str, dict]:
    """The launch command a run starts from (a profile's, or one given) and what it says about the model."""
    pid = (d.get("profile_id") or "").strip()
    if pid:
        p = c.store.get_profile(check_id(pid))
        if not p:
            raise LookupError("no such profile")
        return p["launch_command"], {"profile_name": p["name"], "base_model": p.get("base_model") or "", "model_id": p.get("model_id") or ""}
    command = (d.get("command") or "").strip()
    if not command:
        program = (c.settings.get("prefab").get("program") or "llama-server").strip()
        command = f"{program} -m {d.get('model') or 'model.gguf'}"
    return command, {}


def _server_parts(c, server_id: str):
    """Where it runs: "" = where Profiles launch (the default server), "local" = this machine, or a server id."""
    if server_id == "local":
        return None
    if not server_id:
        return c.servers.default_parts()
    server = next((s for s in c.store.list_servers() if str(s.get("id")) == str(server_id)), None)
    if not server:
        raise LookupError("no such server")
    return launch_parts(server, c.servers.key_path(server["id"]) if c.servers.has_key(server["id"]) else "")


@bp.get("/api/bench/defaults")
def bench_defaults():
    """The settings a profile (or command) implies, to fill the form."""
    c = ctx()
    try:
        command, meta = _base_command(c, request.args)
        cmd = parse_command(command)
        resolve_gpus(cmd)
        params = params_from_command(cmd.to_string())
    except LookupError as exc:
        return err(str(exc), 404)
    except ValueError as exc:
        return err(f"that command does not parse: {exc}")
    return ok(params=params, meta=meta, command=command)


@bp.post("/api/bench/preview")
def bench_preview():
    c, d = ctx(), body()
    try:
        command, _ = _base_command(c, d)
        argv, env = build(d.get("params") or {}, command, _server_parts(c, str(d.get("server_id") or "")))
    except (LookupError, ValueError) as exc:
        return err(str(exc))
    return ok(argv=argv, env={k: v for k, v in env.items() if k != "REMOTE_SSH_KEY"})


@bp.post("/api/bench")
def bench_start():
    """Start a run: ``{profile_id | command, params, server_id, unload_first}``."""
    c, d = ctx(), body()
    try:
        command, meta = _base_command(c, d)
        argv, env = build(d.get("params") or {}, command, _server_parts(c, str(d.get("server_id") or "")))
    except (LookupError, ValueError) as exc:
        return err(str(exc))
    if c.benches.current():
        return err("a benchmark is already running: stop it first, or wait for it", 409)
    url = c.settings.get("integrations")["engine_url"]
    if d.get("unload_first") and url:
        from ..integrations import engine_api
        try:  # llama-bench loads the model itself: the engine's models would leave it no room, and slow it down
            engine_api._post(url.rstrip("/") + "/api/models/unload", 120.0)
        except Exception as exc:  # noqa: BLE001
            return err(f"could not unload the engine's models first: {exc}", 502)
    meta = {**meta, "params": d.get("params") or {}, "server_id": d.get("server_id") or ""}
    try:
        run = c.benches.start(argv, env, meta)
    except RuntimeError as exc:
        return err(str(exc), 409)
    c.event("bench", f"llama-bench started for {meta.get('profile_name') or (d.get('params') or {}).get('model')}",
            data={"bench_id": run.bench_id})
    return ok(bench=run.to_dict())


@bp.get("/api/bench")
def bench_list():
    return ok(benches=[r.to_dict(with_output=False) for r in ctx().benches.list()])


@bp.get("/api/bench/<bench_id>")
def bench_get(bench_id):
    run = ctx().benches.get(bench_id)
    if not run:
        return err("no such run", 404)
    return ok(bench=run.to_dict())


@bp.post("/api/bench/<bench_id>/stop")
def bench_stop(bench_id):
    if not ctx().benches.stop(bench_id):
        return err("that run is not running", 404)
    return ok()
