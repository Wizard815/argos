"""Flask backend for llamacpp-profilebuilder.

Run with ``python -m src.app`` (or ``python src/app.py``) and open
http://localhost:7861. Configuration is by environment variable:

  PROFILEBUILDER_DB        SQLite file              (default ./data.db)
  PROFILEBUILDER_RUNS_DIR  launch logs              (default ./runs)
  ODYSSEUS_COOKBOOK        cookbook_state.json path or URL
  LLAMA_SWAP_CONFIG        llama-swap config.yaml (or a directory of yaml files)
  LLAMA_SWAP_URL           running llama-swap, e.g. http://127.0.0.1:8080
  HOST / PORT              bind address            (default 0.0.0.0:7861)

The API launches arbitrary commands by design: only expose it on networks
where everyone who can reach it may run programs on this machine.
"""
from __future__ import annotations

import atexit
import os
import re
import signal
import sys

from flask import Flask, abort, jsonify, request, send_from_directory

if __package__ in (None, ""):  # `python src/app.py`
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    __package__ = "src"

from .command import guess_base_model, parse_command, to_llama_swap_yaml  # noqa: E402
from .db import Store  # noqa: E402
from .integrations import llama_swap, odysseus  # noqa: E402
from .runner import LaunchError, Runner  # noqa: E402
from .services import Analytics, Recorder  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UI_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ui")
_ID = re.compile(r"^[A-Za-z0-9._-]{1,80}$")
CATALOG_KINDS = ("mtp", "mmproj")


def ok(**kw):
    return jsonify({"ok": True, **kw})


def err(msg: str, code: int = 400):
    return jsonify({"ok": False, "error": msg}), code


def _body() -> dict:
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


def _check_id(value: str) -> str:
    if not _ID.match(value):
        abort(404)
    return value


def create_app(config: dict | None = None) -> Flask:
    cfg = {
        "DB": os.environ.get("PROFILEBUILDER_DB", os.path.join(ROOT, "data.db")),
        "RUNS_DIR": os.environ.get("PROFILEBUILDER_RUNS_DIR", os.path.join(ROOT, "runs")),
        "ODYSSEUS_COOKBOOK": os.environ.get("ODYSSEUS_COOKBOOK", ""),
        "LLAMA_SWAP_CONFIG": os.environ.get("LLAMA_SWAP_CONFIG", ""),
        "LLAMA_SWAP_URL": os.environ.get("LLAMA_SWAP_URL", ""),
        **(config or {}),
    }
    app = Flask(__name__, static_folder=None)
    app.config.update(cfg)
    store = Store(cfg["DB"])
    runner = Runner(cfg["RUNS_DIR"])
    recorder = Recorder(store, runner)
    analytics = Analytics(store)
    app.extensions["runner"] = runner
    atexit.register(runner.stop_all)

    # ----- UI -------------------------------------------------------------
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

    @app.errorhandler(500)
    def server_error(e):
        if request.path.startswith("/api/"):
            return err(f"internal error: {getattr(e, 'original_exception', e)}", 500)
        return "Internal error", 500

    # ----- commands -------------------------------------------------------
    @app.post("/api/command/parse")
    def command_parse():
        d = _body()
        try:
            cmd = parse_command(d.get("command") or "")
        except ValueError as exc:
            return err(str(exc))
        return ok(**cmd.to_dict(), base_model=guess_base_model(cmd.summary()["model"]))

    @app.post("/api/command/build")
    def command_build():
        """Apply ``edits`` ({flag: value|true|null}) and optional ``env`` to ``command``."""
        d = _body()
        edits, env, arg_edits = d.get("edits") or {}, d.get("env"), d.get("arg_edits") or {}
        if not all(isinstance(x, dict) for x in (edits, arg_edits, env if env is not None else {})):
            return err("edits, arg_edits and env must be objects")
        try:
            cmd = parse_command(d.get("command") or "").with_edits(edits, env, arg_edits)
        except ValueError as exc:
            return err(str(exc))
        name = d.get("name") or guess_base_model(cmd.summary()["model"]) or "model"
        return ok(**cmd.to_dict(), base_model=guess_base_model(cmd.summary()["model"]),
                  llama_swap_yaml=to_llama_swap_yaml(name, cmd))

    # ----- profiles -------------------------------------------------------
    @app.get("/api/profiles")
    def profiles_list():
        profiles = store.list_profiles()
        for p in profiles:
            try:
                p["summary"] = parse_command(p["launch_command"]).summary()
            except ValueError:
                p["summary"] = {}
        return ok(profiles=profiles)

    @app.post("/api/profiles")
    def profiles_save():
        d = _body()
        if not (d.get("name") or "").strip() or not (d.get("launch_command") or "").strip():
            return err("name and launch_command are required")
        try:
            parse_command(d["launch_command"])
        except ValueError as exc:
            return err(f"launch_command: {exc}")
        if d.get("profile_id"):
            _check_id(d["profile_id"])
        return ok(profile=store.save_profile(d))

    @app.delete("/api/profiles/<profile_id>")
    def profiles_delete(profile_id):
        return ok() if store.delete_profile(_check_id(profile_id)) else err("not found", 404)

    # ----- catalog --------------------------------------------------------
    @app.get("/api/catalog")
    def catalog_list():
        return ok(files=store.list_files(request.args.get("kind") or None,
                                         request.args.get("base_model") or None))

    @app.post("/api/catalog")
    def catalog_add():
        d = _body()
        kind, base = d.get("kind"), (d.get("base_model") or "").strip()
        if kind not in CATALOG_KINDS:
            return err(f"kind must be one of {', '.join(CATALOG_KINDS)}")
        if not base or not (d.get("file_path") or d.get("hf_repo")):
            return err("base_model and a file path or HF repo are required")
        fid = store.add_file(kind, base, name=(d.get("name") or "").strip(),
                             hf_repo=(d.get("hf_repo") or "").strip(),
                             file_path=(d.get("file_path") or "").strip(),
                             notes=(d.get("notes") or "").strip())
        return ok(id=fid)

    @app.delete("/api/catalog/<int:file_id>")
    def catalog_delete(file_id):
        return ok() if store.delete_file(file_id) else err("not found", 404)

    @app.get("/api/models")
    def models():
        return ok(models=store.base_models())

    # ----- launches -------------------------------------------------------
    @app.get("/api/launches")
    def launches():
        return ok(launches=[{**r.to_dict(), "ready": runner.health(r, timeout=0.3)}
                            for r in runner.list()])

    @app.post("/api/launches")
    def launch():
        d = _body()
        command = (d.get("command") or "").strip()
        if not command:
            return err("command is required")
        try:
            run = runner.launch(command, base_model=(d.get("base_model") or "").strip(),
                                profile_name=(d.get("profile_name") or "").strip())
        except LaunchError as exc:
            return err(str(exc))
        return ok(launch=run.to_dict())

    def _run_or_404(launch_id):
        run = runner.get(_check_id(launch_id))
        if run is None:
            abort(404)
        return run

    @app.get("/api/launches/<launch_id>")
    def launch_status(launch_id):
        run = _run_or_404(launch_id)
        return ok(launch={**run.to_dict(), "ready": runner.health(run)})

    @app.post("/api/launches/<launch_id>/stop")
    def launch_stop(launch_id):
        run = _run_or_404(launch_id)
        runner.stop(run.launch_id)
        return ok(launch=run.to_dict())

    @app.get("/api/launches/<launch_id>/log")
    def launch_log(launch_id):
        text = runner.read_log(_check_id(launch_id), tail_bytes=request.args.get("tail", 0, type=int))
        if text is None:
            abort(404)
        return app.response_class(text, mimetype="text/plain")

    @app.post("/api/launches/<launch_id>/bench")
    def launch_bench(launch_id):
        run = _run_or_404(launch_id)
        d = _body()
        try:
            words = max(1, min(int(d.get("prompt_words", 400)), 50_000))
            n_predict = max(1, min(int(d.get("n_predict", 128)), 8192))
        except (TypeError, ValueError):
            return err("prompt_words and n_predict must be integers")
        if not run.running:
            return err("process is not running")
        try:
            timings = runner.bench(run, prompt_words=words, n_predict=n_predict)
        except LaunchError as exc:
            return err(str(exc), 502)
        return ok(run=recorder.record(run.launch_id, timings=timings, note=d.get("note") or ""))

    @app.post("/api/launches/<launch_id>/record")
    def launch_record(launch_id):
        run = _run_or_404(launch_id)
        return ok(run=recorder.record(run.launch_id, note=_body().get("note") or ""))

    # ----- perf sheet -----------------------------------------------------
    @app.get("/api/runs")
    def runs_list():
        limit = max(1, min(request.args.get("limit", 1000, type=int), 100_000))
        return ok(runs=store.list_runs(request.args.get("base_model") or None, limit))

    @app.delete("/api/runs/<run_id>")
    def runs_delete(run_id):
        return ok() if store.delete_run(_check_id(run_id)) else err("not found", 404)

    @app.get("/api/analytics/best")
    def analytics_best():
        return ok(rows=analytics.best_by_base_model())

    @app.get("/api/env-flags")
    def env_flags():
        return ok(rows=analytics.env_table())

    # ----- integrations ---------------------------------------------------
    @app.get("/api/integrations")
    def integrations():
        return ok(odysseus=odysseus.describe(app.config["ODYSSEUS_COOKBOOK"]),
                  llama_swap=llama_swap.describe(app.config["LLAMA_SWAP_CONFIG"],
                                                 app.config["LLAMA_SWAP_URL"]))

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
