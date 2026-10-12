"""Flask backend for Argos.

Run with ``python -m src.app`` (or ``python src/app.py``) and open
http://localhost:7861 (Docker: http://localhost:8080, one port for the UI and the
OpenAI API; the builder passes every path it does not own to the engine). Environment variables set the defaults; most of them
can then be changed in the Settings view (stored in the database):

  ARGOS_DB                 SQLite file                      (default ./data.db)
  ARGOS_RUNS_DIR           launch logs                      (default ./runs)
  MODELS_DIR               GGUF library + download target   (default ./models)
  ARGOS_ENGINE_URL         the engine in this container      (default none; Docker sets http://127.0.0.1:8081)
  HF_ENDPOINT / HF_TOKEN   Hugging Face mirror and token for downloads
  ARGOS_SSH_DIR            SSH keys for servers          (default <db folder>/ssh)
  ARGOS_SERVERS_FILE       servers.yaml / .json that seeds the Servers view at start
  ARGOS_SERVERS            the same as JSON in the environment
  HOST / PORT              bind address                     (default 0.0.0.0:7861; Docker 8080)

The API launches arbitrary commands by design: only expose it on networks
where everyone who can reach it may run programs on this machine.
"""
from __future__ import annotations

import atexit
import os
import signal
import sys
import time

from flask import Flask, request, send_from_directory

if __package__ in (None, ""):  # `python src/app.py`
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    __package__ = "src"

from .assistant import Assistant  # noqa: E402
from .db import Store  # noqa: E402
from .downloader import Downloads  # noqa: E402
from . import engine_proxy  # noqa: E402
from .log_analyzer import parse_log_text  # noqa: E402
from .routes import assistant as assistant_routes  # noqa: E402
from .routes import core, inventory as inventory_routes, logs, models, odysseus_hub, schedule, servers as servers_routes, workspace  # noqa: E402
from .measure import Measurer  # noqa: E402
from .runner import Runner  # noqa: E402
from .scheduler import Plans  # noqa: E402
from .servers import Servers, gguf_index, load_seed  # noqa: E402
from .integrations import engine_api  # noqa: E402
from .inventory import Inventory  # noqa: E402
from .memory import Memory  # noqa: E402
from .services import Analytics, Recorder  # noqa: E402
from .settings import Settings, env_defaults  # noqa: E402
from .telemetry import Collector  # noqa: E402
from .web import Ctx, err  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UI_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ui")


def _env_config() -> dict:
    env = os.environ.get
    return {
        "ROOT": ROOT,
        "DB": env("ARGOS_DB") or env("PROFILEBUILDER_DB", os.path.join(ROOT, "data.db")),
        "RUNS_DIR": env("ARGOS_RUNS_DIR") or env("PROFILEBUILDER_RUNS_DIR", os.path.join(ROOT, "runs")),
        "MODELS_DIR": env("MODELS_DIR", ""),
        "ENGINE_URL": env("ARGOS_ENGINE_URL", ""),
        "HF_ENDPOINT": env("HF_ENDPOINT", ""),
        "HF_TOKEN": env("HF_TOKEN", ""),
        "SSH_DIR": env("ARGOS_SSH_DIR", ""),
        "SERVERS_FILE": env("ARGOS_SERVERS_FILE", ""),
        "SERVERS_JSON": env("ARGOS_SERVERS", ""),
        "TELEMETRY": (env("ARGOS_TELEMETRY") or env("PROFILEBUILDER_TELEMETRY", "1")) not in ("0", "false", "off"),
        "BACKGROUND": True,  # tests turn the background thread off and drive it by hand
    }


def create_app(config: dict | None = None) -> Flask:
    cfg = {**_env_config(), **(config or {})}
    app = Flask(__name__, static_folder=None)
    app.config.update(cfg)

    store = Store(cfg["DB"])
    settings = Settings(store, env_defaults(cfg))
    runner = Runner(cfg["RUNS_DIR"])
    analytics = Analytics(store)

    def event(kind, message, level="info", data=None):
        store.add_event(kind, message, level=level, data=data)

    def download_settings():
        s = settings.all()
        return {"models_dir": s["paths"]["models_dir"], **s["downloads"]}

    seen_exits: set[str] = set()

    def watch_exits():
        """Log launches that ended on their own (crash, OOM, bad flag)."""
        for run in runner.list():
            if run.running or run.launch_id in seen_exits:
                continue
            seen_exits.add(run.launch_id)
            if run.stopped:
                continue
            problem = parse_log_text(runner.read_log(run.launch_id, tail_bytes=20000) or "").error
            name = run.profile_name or run.base_model or run.launch_id
            event("launch", f"{name} exited with code {run.exit_code}" + (f": {problem}" if problem else ""),
                  level="error" if run.exit_code else "info", data={"launch_id": run.launch_id})

    def refresh_plan():
        with app.app_context():
            schedule.apply_plan(force=False)

    inventory = Inventory(store, event)
    memory = Memory(store, settings.all, event)
    memory.extra_sources.append(odysseus_hub.brain_source(settings.all))

    def watch_memory():
        """Keep the assistant's memory current: new records every tick, a bounded number embedded each time."""
        if settings.get("memory")["enabled"]:
            memory.sync(budget=64)

    def watch_inventory():
        """Keep the load history current, and look for new models and settings every ten minutes."""
        with app.app_context():
            inventory_routes.sync_running()
            inventory_routes.refresh_if_due()

    pool_seen: dict = {"models": None, "queue_reported": {}}

    def watch_pool():
        """Log what the pool router did between two looks: models loaded, moved or unloaded, and long waits."""
        url = settings.get("integrations")["engine_url"]
        if not url:
            return
        try:
            pool = engine_api.pool_state(url)
        except Exception:  # noqa: BLE001 - the Live view shows connection problems
            return
        if not pool.get("enabled"):
            pool_seen["models"] = None
            return
        now = {m["id"]: (m["state"], tuple(m.get("cards") or [])) for m in pool.get("models") or []}
        before = pool_seen["models"]
        pool_seen["models"] = now
        if before is None:
            return
        for mid, (state, cards) in now.items():
            was = before.get(mid, ("stopped", ()))
            on = f" on GPU {','.join(cards)}" if cards else ""
            if state in ("ready", "starting") and was[0] not in ("ready", "starting"):
                event("schedule", f"{mid} loaded{on}", data={"model": mid, "cards": list(cards)})
            elif state == "stopped" and was[0] in ("ready", "starting"):
                event("schedule", f"{mid} unloaded", data={"model": mid})
            elif state == "ready" and was[0] == "ready" and cards != was[1]:
                event("schedule", f"{mid} moved{on}", data={"model": mid, "cards": list(cards)})
        for q in pool.get("queue") or []:
            if q.get("waited_seconds", 0) > 30 and time.time() - pool_seen["queue_reported"].get(q["model"], 0) > 300:
                pool_seen["queue_reported"][q["model"]] = time.time()
                event("schedule", f"a request for {q['model']} has waited {q['waited_seconds']:.0f}s", level="warn",
                      data={"model": q["model"], "position": q.get("position")})

    def watch_assistant():
        """Unload the assistant's chat and embedding models once they sit unused for the set number of minutes."""
        app.extensions["pb"].assistant.unload_idle()

    collector = Collector(store, settings.all, event,
                          hooks=[watch_exits, refresh_plan, watch_pool, watch_inventory, watch_memory, watch_assistant])
    plans = Plans(store)
    servers = Servers(store, cfg["SSH_DIR"] or os.path.join(os.path.dirname(os.path.abspath(cfg["DB"])), "ssh"), run=cfg.get("SSH_RUN"),
                      local_models=lambda: gguf_index([settings.get("paths")["models_dir"]]))
    servers.ensure_local()
    try:
        seeded = servers.seed(load_seed(cfg["SERVERS_FILE"], cfg["SERVERS_JSON"]), source="file")
        for problem in seeded["errors"]:
            event("server", f"servers seed: {problem}", level="warn")
    except Exception as exc:  # noqa: BLE001 - a bad seed file must not stop the app
        event("server", f"servers seed ignored: {exc}", level="warn")
    app.extensions["pb"] = Ctx(
        store=store, settings=settings, runner=runner, recorder=Recorder(store, runner), analytics=analytics,
        downloads=Downloads(store, download_settings, event), collector=collector,
        assistant=Assistant(store, settings, runner, analytics, event, inventory, memory),
        plans=plans, measurer=Measurer(runner, plans, lambda: collector.sample(), event), inventory=inventory,
        memory=memory, servers=servers)
    app.extensions["runner"] = runner
    atexit.register(runner.stop_all)
    atexit.register(inventory.close_all)
    if cfg["BACKGROUND"]:
        collector.start()

    for bp in (core.bp, models.bp, logs.bp, assistant_routes.bp, schedule.bp, inventory_routes.bp, workspace.bp, odysseus_hub.bp, servers_routes.bp):
        app.register_blueprint(bp)
    # last: anything the builder does not own goes to the engine (one port for both)
    app.register_blueprint(engine_proxy.bp)

    def ui_version() -> str:
        """Changes whenever a UI file changes, so a browser never runs an old app.js against a new server."""
        stamp = 0.0
        for name in os.listdir(UI_DIR):
            try:
                stamp = max(stamp, os.path.getmtime(os.path.join(UI_DIR, name)))
            except OSError:
                pass
        return str(int(stamp))

    @app.get("/")
    def index():
        with open(os.path.join(UI_DIR, "index.html"), encoding="utf-8") as f:
            html = f.read()
        v = ui_version()
        for asset in ("app.js", "style.css", "theme.js"):
            html = html.replace(f'/static/{asset}"', f'/static/{asset}?v={v}"')
        resp = app.response_class(html, mimetype="text/html")
        resp.headers["Cache-Control"] = "no-cache"
        return resp

    @app.get("/static/<path:filename>")
    def static_files(filename):
        resp = send_from_directory(UI_DIR, filename)
        # the page asks for ?v=<version>, so a versioned file can be kept; an unversioned one is always re-checked
        resp.headers["Cache-Control"] = "public, max-age=31536000, immutable" if request.args.get("v") else "no-cache"
        return resp

    @app.errorhandler(404)
    def not_found(_e):
        if request.path.startswith("/api/"):
            return err("not found", 404)
        return "Not found", 404

    @app.errorhandler(405)
    def not_allowed(_e):
        return err("method not allowed", 405) if request.path.startswith("/api/") else ("Method not allowed", 405)

    @app.errorhandler(500)
    def server_error(e):
        if request.path.startswith("/api/"):
            return err(f"internal error: {getattr(e, 'original_exception', e)}", 500)
        return "Internal error", 500

    return app


def main() -> None:
    # Launched servers live in their own process groups; turn SIGTERM (docker
    # stop, systemd) into a normal exit so the atexit hook stops them too.
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    app = create_app()
    app.run(host=os.environ.get("HOST", "0.0.0.0"), port=int(os.environ.get("PORT", "7861")),
            debug=False, threaded=True)


if __name__ == "__main__":
    main()
