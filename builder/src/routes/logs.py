"""Long-term logs: events, GPU history, llama-swap request history."""
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


@bp.get("/api/requests")
def requests_list():
    a = request.args
    since = time.time() - RANGES.get(a.get("range", "24h"), RANGES["24h"])[0]
    return ok(requests=ctx().store.list_requests(model=a.get("model") or None, since=since,
                                                 limit=max(1, min(a.get("limit", 300, type=int), 5000))),
              stats=ctx().store.request_stats(since))
