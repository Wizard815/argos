"""Flask backend for llamacpp-profilebuilder.

Run with ``python -m src.app`` (or ``python src/app.py``) and open
http://localhost:7861. Environment variables set the defaults; most of them
can then be changed in the Settings view (stored in the database):

  PROFILEBUILDER_DB        SQLite file                      (default ./data.db)
  PROFILEBUILDER_RUNS_DIR  launch logs                      (default ./runs)
  MODELS_DIR               GGUF library + download target   (default ./models)
  ODYSSEUS_COOKBOOK        Odysseus cookbook_state.json path or URL
  LLAMA_SWAP_CONFIG        llama-swap config.yaml (or a directory of yaml files)
  LLAMA_SWAP_URL           running llama-swap, e.g. http://llama-swap:8080
  COOKBOOK_EXPORT_FILE     also write the Odysseus-format state to this file
  HF_ENDPOINT / HF_TOKEN   Hugging Face mirror and token for downloads
  HOST / PORT              bind address                     (default 0.0.0.0:7861)

The API launches arbitrary commands by design: only expose it on networks
where everyone who can reach it may run programs on this machine.
"""
from __future__ import annotations

import atexit
import os
import signal
import sys

from flask import Flask, request, send_from_directory

if __package__ in (None, ""):  # `python src/app.py`
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    __package__ = "src"

from .assistant import Assistant  # noqa: E402
from .db import Store  # noqa: E402
from .downloader import Downloads  # noqa: E402
from .log_analyzer import parse_log_text  # noqa: E402
from .routes import assistant as assistant_routes  # noqa: E402
from .routes import core, logs, models  # noqa: E402
from .runner import Runner  # noqa: E402
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
        "DB": env("PROFILEBUILDER_DB", os.path.join(ROOT, "data.db")),
        "RUNS_DIR": env("PROFILEBUILDER_RUNS_DIR", os.path.join(ROOT, "runs")),
        "MODELS_DIR": env("MODELS_DIR", ""),
        "ODYSSEUS_COOKBOOK": env("ODYSSEUS_COOKBOOK", ""),
        "LLAMA_SWAP_CONFIG": env("LLAMA_SWAP_CONFIG", ""),
        "LLAMA_SWAP_URL": env("LLAMA_SWAP_URL", ""),
        "COOKBOOK_EXPORT_FILE": env("COOKBOOK_EXPORT_FILE", ""),
        "HF_ENDPOINT": env("HF_ENDPOINT", ""),
        "HF_TOKEN": env("HF_TOKEN", ""),
        "TELEMETRY": env("PROFILEBUILDER_TELEMETRY", "1") not in ("0", "false", "off"),
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

    def refresh_export():
        with app.app_context():
            core.write_export()

    collector = Collector(store, settings.all, event, hooks=[watch_exits, refresh_export])
    app.extensions["pb"] = Ctx(
        store=store, settings=settings, runner=runner, recorder=Recorder(store, runner), analytics=analytics,
        downloads=Downloads(store, download_settings, event), collector=collector,
        assistant=Assistant(store, settings, runner, analytics, event))
    app.extensions["runner"] = runner
    atexit.register(runner.stop_all)
    if cfg["BACKGROUND"]:
        collector.start()

    for bp in (core.bp, models.bp, logs.bp, assistant_routes.bp):
        app.register_blueprint(bp)

    @app.get("/")
    def index():
        return send_from_directory(UI_DIR, "index.html")

    @app.get("/static/<path:filename>")
    def static_files(filename):
        return send_from_directory(UI_DIR, filename)

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
