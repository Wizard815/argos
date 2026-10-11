"""Flask backend for llamacpp-profile-builder.

Wires the web dashboard to the services layer (profile copy/paste, launch/run
lifecycle, log analysis, analytics) plus the three integration adapters:
  - Odysseus cookbook_state.json  -> command authoring / presets
  - llama-swap-rescan config      -> profiles/selectors/macros + live activity
  - local catalog               -> MTP / vision MMJPG files per base model

All endpoints return JSON so the SPA in src/ui/ can drive everything.
"""
import os
import sys
from flask import Flask, request, jsonify, send_from_directory, Response

_SRC = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_SRC)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
if _SRC not in sys.path:
    sys.path.insert(0, __file__.rsplit("/", 1)[0])

from src.db import Store
from src.services import (ProfileService, LaunchService, LogAnalysisService,
                          AnalyticsService, RunRecord)

app = Flask(__name__, static_folder=_SRC, static_url_path="/ui")

DB_PATH = os.environ.get("PROFILEBUILDER_DB", os.path.join(_ROOT, "data.db"))
store = Store(DB_PATH)
profile_svc = ProfileService(store)
launch_svc = LaunchService(store)          # uses RUNS_DIR for logs
log_svc = LogAnalysisService(store)
analytics = AnalyticsService(store)


def _ok(**kw):
    return jsonify({"ok": True, **kw})


def _err(msg: str, code: int = 400):
    return jsonify({"ok": False, "error": msg}), code


@app.route("/")
def index():
    return send_from_directory(os.path.join(_SRC, "ui"), "index.html")


@app.route("/<path:filename>")
def serve_ui(filename):
    """Serve SPA pages from src/ui/."""
    if filename.endswith(".html") or "/" not in filename:
        target = os.path.join(_SRC, "ui", filename)
        if os.path.exists(target):
            return send_from_directory(os.path.join(_SRC, "ui"), filename)
    abort(404)




# ---------------------------------------------------------------------------
# Profiles / copy-paste
# ---------------------------------------------------------------------------
@app.route("/api/models", methods=["GET"])
def list_models():
    rows = store.list_runs(limit=10_000)
    models = {}
    for r in rows:
        bm = (r.get("base_model") or "").strip()
        if bm and bm not in models:
            models[bm] = {"base_model": bm}
    for m in store.list_mtp():
        bm = m.get("base_model")
        if bm:
            models.setdefault(bm, {"base_model": bm})["mtps"] =                 models.get(bm, {}).get("mtps", []) + [m]
    for v in store.list_mmjogs():
        bm = v.get("base_model")
        if bm:
            models.setdefault(bm, {"base_model": bm})["mmjogs"] =                 models.get(bm, {}).get("mmjogs", []) + [v]
    return _ok(models=list(models.values()))


@app.route("/api/profiles", methods=["GET"])
def list_profiles():
    return _ok(profiles=store.list_profiles())


@app.route("/api/copy-profile", methods=["POST"])
def copy_profile():
    data = request.get_json(force=True, silent=True) or {}
    source_cmd = data.get("source_command") or ""
    target = data.get("target_base_model") or ""
    edits = data.get("edits") or {}
    copied_from = data.get("copied_from", "manual")
    if not source_cmd or not target:
        return _err("source_command and target_base_model are required")
    try:
        prof = profile_svc.copy_profile(source_cmd, target, edits=edits,
                                        copied_from=copied_from)
    except Exception as exc:  # noqa: BLE001 - surface to UI
        return _err(str(exc))
    return _ok(command=prof["launch_command"], base_model=prof["base_model"],
               copied_from=prof["copied_from"], flags=prof.get("flags_json", []),
               env=prof.get("env_json", {}))


# ---------------------------------------------------------------------------
# Catalog: MTP + vision MMJPG files
# ---------------------------------------------------------------------------
@app.route("/api/mtps", methods=["GET", "POST"])
def mtp_routes():
    if request.method == "POST":
        d = request.get_json(force=True, silent=True) or {}
        bm = d.get("base_model") or d.get("model") or ""
        if not bm:
            return _err("base_model required for MTP catalog entry")
        mid = profile_svc.add_mtp(bm, hf_repo=d.get("hf_repo", ""),
                                  file_path=d.get("file_path", ""),
                                  name=d.get("name", ""), notes=d.get("notes", ""))
        return _ok(id=mid, base_model=bm)
    model_filter = request.args.get("base_model")
    return _ok(mtps=store.list_mtp(model_filter))


@app.route("/api/mpps/<int:mtp_id>", methods=["DELETE"])
def mtp_delete(mtp_id):
    ok = store.delete_mtp(mtp_id)
    return _ok(removed=ok) if ok else _err("not found", 404)


@app.route("/api/mmjogs", methods=["GET", "POST"])
def mmjog_routes():
    if request.method == "POST":
        d = request.get_json(force=True, silent=True) or {}
        bm = d.get("base_model") or d.get("model") or ""
        if not bm:
            return _err("base_model required for vision MMJPG entry")
        mjid = profile_svc.add_mmjog(bm, type_=d.get("type", "mmjog"),
                                     hf_repo=d.get("hf_repo", ""),
                                     file_path=d.get("file_path", ""),
                                     modalities=d.get("modalities", "image"),
                                     name=d.get("name", ""), notes=d.get("notes", ""))
        return _ok(id=mjid, base_model=bm)
    model_filter = request.args.get("base_model")
    return _ok(mmjogs=store.list_mmjogs(model_filter))


@app.route("/api/mmps/<int:mmjog_id>", methods=["DELETE"])
def mmjog_delete(mmjog_id):
    ok = store.delete_mmjog(mmjog_id)
    return _ok(removed=ok) if ok else _err("not found", 404)


# ---------------------------------------------------------------------------
# Launch / run lifecycle
# ---------------------------------------------------------------------------
@app.route("/api/launch", methods=["POST"])
def launch():
    d = request.get_json(force=True, silent=True) or {}
    cmd = d.get("command") or d.get("launch_command") or ""
    base_model = d.get("base_model") or d.get("model") or "unknown"
    env_vars = d.get("env_vars") or {}
    n_predict = int(d.get("n_predict", 512))
    background = bool(d.get("background", True))
    if not cmd:
        return _err("command required to launch")
    try:
        rec = RunRecord(base_model=base_model, launch_command=cmd,
                        env_vars=env_vars)
    except Exception as exc:  # noqa: BLE001
        return _err(str(exc))
    res = launch_svc.launch(rec, background=background, n_predict=n_predict)
    rd = res.to_dict()
    log_path = os.path.relpath(res.log_path, _ROOT) \
        if hasattr(res, "log_path") and res.log_path else None
    return _ok(run_id=rd["run_id"], ok=res.ok, error=rd.get("error"),
               log_path=log_path)


@app.route("/api/stop/<run_id>", methods=["POST"])
def stop_run(run_id):
    ok = launch_svc.stop(run_id)
    return _ok(stopped=ok) if ok else _err("no such active run", 404)


@app.route("/api/status/<run_id>", methods=["GET"])
def status_run(run_id):
    res = launch_svc._active.get(run_id)
    if res is None:
        return jsonify({"running": False, "exit_code": 0})
    proc = getattr(res, "proc", None)
    rc = proc.poll() if proc else None
    return jsonify({"running": rc is None, "exit_code": rc,
                    "started_at": res.started_at})


# ---------------------------------------------------------------------------
# Logs + recording / analysis
# ---------------------------------------------------------------------------
@app.route("/api/logs/<run_id>", methods=["GET"])
def read_logs(run_id):
    text = launch_svc.read_log(run_id)
    if not text:
        path = os.path.join(os.path.dirname(__file__), "..", "runs", f"{run_id}.log")
        if os.path.exists(path):
            try:
                with open(path, encoding="utf-8", errors="ignore") as fh:
                    text = fh.read()
            except OSError:
                pass
    return Response(text, mimetype="text/plain")


@app.route("/api/analyze/<run_id>", methods=["POST"])
def analyze_run(run_id):
    d = request.get_json(force=True, silent=True) or {}
    base_model = d.get("base_model") or run_id
    cmd = d.get("command") or ""
    env_vars = d.get("env_vars") or {}
    source = d.get("source", "manual")
    rec = log_svc.record_run(run_id, base_model, cmd,
                             env_vars=env_vars, source=source)
    return _ok(record=rec)


# ---------------------------------------------------------------------------
# Analytics + env-scale table
# ---------------------------------------------------------------------------
@app.route("/api/analytics/best-by-base", methods=["GET"])
def analytics_best_by_base():
    return _ok(rows=analytics.best_by_base_model())


@app.route("/api/analytics/env-table", methods=["GET"])
def analytics_env_table():
    bf = request.args.get("base_model")
    rows = analytics.env_table(bf) if bf else analytics.env_table()
    return _ok(env_rows=rows)


@app.route("/api/analytics/runs", methods=["GET"])
def analytics_runs():
    bf = request.args.get("base_model")
    limit = int(request.args.get("limit", 500))
    return _ok(runs=store.list_runs(base_model=bf, limit=limit))


if __name__ == "__main__":
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "7861"))
    app.run(host=host, port=port, debug=False, threaded=True)
