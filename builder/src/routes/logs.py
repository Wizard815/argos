"""Long-term logs: events, GPU history, Argos request history."""
from __future__ import annotations

import time

from flask import Blueprint, request

from ..web import ctx, err, ok

bp = Blueprint("logs", __name__)

# range -> (seconds, bucket seconds)
RANGES = {"30s": (30, 2), "5m": (300, 5), "10m": (600, 10), "30m": (1800, 15), "1h": (3600, 30), "6h": (6 * 3600, 120), "24h": (86400, 300), "7d": (7 * 86400, 3600),
          "30d": (30 * 86400, 3 * 3600), "90d": (90 * 86400, 86400), "1y": (365 * 86400, 86400)}


@bp.get("/api/events")
def events():
    a = request.args
    since = time.time() - a.get("hours", 0, type=float) * 3600 if a.get("hours") else None
    return ok(events=ctx().store.list_events(kind=a.get("kind") or None, level=a.get("level") or None,
                                             since=since, q=a.get("q") or None,
                                             limit=max(1, min(a.get("limit", 300, type=int), 5000))))


@bp.get("/api/gpu/series")
def gpu_series():
    c = ctx()
    rng = request.args.get("range", "24h")
    if rng not in RANGES:
        return err(f"range must be one of {', '.join(RANGES)}")
    span, bucket = RANGES[rng]
    since = time.time() - span
    raw_days = c.settings.get("telemetry")["raw_days"]
    if span <= raw_days * 86400:
        rows, source = c.store.gpu_series(since, bucket), "raw"
    else:
        rows, source = c.store.gpu_rollup_series(since, bucket), "hourly"
    return ok(range=rng, bucket=bucket, source=source, rows=rows, latest=c.store.gpu_latest(),
              collector={"source": c.collector.last_source,
                         "enabled": c.settings.get("telemetry")["enabled"]})


@bp.get("/api/gpu/latest")
def gpu_latest():
    """Just the newest reading per GPU: what the header bars need, without the whole series."""
    c = ctx()
    return ok(latest=c.store.gpu_latest())


@bp.get("/api/debug/threads")
def debug_threads():
    """What every thread is doing and how much CPU it has used: for finding the one that keeps the process busy.
    Only answers from the machine the app runs on (docker exec ... curl), never from the network."""
    import sys
    import threading
    import traceback
    if request.remote_addr not in ("127.0.0.1", "::1"):
        return err("this is only for the machine the app runs on", 403)
    ticks = float(__import__("os").sysconf("SC_CLK_TCK"))
    names = {t.ident: t for t in threading.enumerate()}
    out = []
    for ident, frame in sys._current_frames().items():
        t = names.get(ident)
        cpu = None
        try:
            with open(f"/proc/self/task/{t.native_id}/stat") as fh:
                f = fh.read().rsplit(")", 1)[1].split()
                cpu = round((int(f[11]) + int(f[12])) / ticks, 1)  # utime + stime, seconds
        except (OSError, AttributeError, IndexError, ValueError):
            pass
        out.append({"thread": t.name if t else str(ident), "cpu_s": cpu,
                    "stack": [f"{fs.filename.split('/src/')[-1]}:{fs.lineno} {fs.name}" for fs in traceback.extract_stack(frame)][-8:]})
    out.sort(key=lambda x: -(x["cpu_s"] or 0))
    return ok(threads=out)


@bp.get("/api/requests")
def requests_list():
    a = request.args
    since = time.time() - RANGES.get(a.get("range", "24h"), RANGES["24h"])[0]
    return ok(requests=ctx().store.list_requests(model=a.get("model") or None, since=since,
                                                 limit=max(1, min(a.get("limit", 300, type=int), 5000))),
              stats=ctx().store.request_stats(since))


# ----- what is really on the GPUs ------------------------------------------------------
def _gpu_listing() -> dict:
    from .. import gpu_procs
    from ..integrations import engine_api
    c = ctx()
    running = []
    url = c.settings.get("integrations")["engine_url"]
    if url:
        try:
            running = engine_api.running(url)
        except Exception:  # noqa: BLE001 - the list is still useful without owners
            pass
    launches = [{"port": r.port, "running": r.running, "profile_name": r.profile_name, "base_model": r.base_model,
                 "launch_id": r.launch_id} for r in c.runner.list()]
    return gpu_procs.listing(running, launches)


@bp.get("/api/gpu/processes")
def gpu_processes():
    return ok(**_gpu_listing())


@bp.post("/api/gpu/processes/<int:pid>/kill")
def gpu_process_kill(pid):
    """Stop a process that is using a GPU. One the engine owns must be unloaded from Schedule instead,
    unless ``force`` says otherwise: killing it behind the engine's back leaves the engine thinking it runs."""
    from .. import gpu_procs
    from ..web import body
    data = _gpu_listing()
    row = next((p for p in data["processes"] if p["pid"] == pid), None)
    if row is None:
        return err("that process is not using a GPU as far as this app can see", 404)
    b = body()
    if not row.get("visible", True):
        # its number is the host's; inside this container the same number can be another process entirely
        return err(f"PID {pid} is outside this container, so Argos cannot stop it. On the host: kill {row.get('host_pid', pid)} "
                   "(or give Argos the host's PID namespace, pid: host)", 409)
    if (row.get("owner") or "").startswith("model ") and not b.get("force"):
        return err(f"{row['owner']} is loaded by the engine: unload it from Schedule → Live instead", 409)
    try:
        result = gpu_procs.kill(pid, {p["pid"] for p in data["processes"]}, bool(b.get("hard")))
    except PermissionError as exc:
        return err(str(exc), 403)
    except OSError as exc:
        return err(f"could not stop it: {exc}", 500)
    ctx().event("launch", f"{result}: PID {pid} ({row['name']}), which held {row['vram_gib'] or '?'} GiB of GPU memory",
                level="warn", data={"pid": pid})
    return ok(result=result)
