"""A short-lived cache for the reads a page repeats.

The UI polls: several endpoints every few seconds, from every open tab, and a page
load fires about fourteen at once. Every one of those reads the database, and the
store has a single lock - so a busy moment becomes a queue, and a queue becomes
timeouts. That is what "the data takes forever" looks like from the browser.

This serves a repeated GET from memory for a few seconds and clears everything the
moment anything is written. A dashboard does not need a value that is one poll old
to be fetched again, and the database stops being asked the same question fourteen
times at once.

Streams and per-request routes are never cached: an answer that must be live, or
that never ends, has no business being remembered.
"""
from __future__ import annotations

import os
import threading
import time
from typing import Any, Optional

# An allowlist, not a blanket. A job's progress, the live pool view, a log tail and
# anything per-request must stay live: a cache that guesses wrong makes the UI show
# state that has already moved on. These are the reads a page repeats, where a value
# one poll old is worth not asking the database again.
CACHE_PATHS = (
    "/api/settings", "/api/profiles", "/api/launches", "/api/downloads", "/api/library",
    "/api/inventory", "/api/catalog", "/api/models", "/api/layout", "/api/notes",
    "/api/integrations", "/api/assistant", "/api/gpu/series", "/api/runs", "/api/events",
    "/api/requests", "/api/ody/",
)
# Deliberately absent: /api/schedule/live and the inventory session view are live by
# name, and the inventory list is already stale-while-rebuild.

# Kept for the tests and for anyone reading this: streams and per-request routes.
SKIP_PREFIXES = ("/api/assistant/chat", "/api/assistant/ask", "/api/engine/", "/logs/", "/v1/", "/api/captures")


def default_ttl() -> float:
    """Seconds a repeated read may be served from memory. 0 turns the cache off."""
    try:
        return max(0.0, float(os.environ.get("ARGOS_READ_CACHE_S") or 3.0))
    except ValueError:
        return 3.0


class ReadCache:
    """Keys are the request path plus its query, so a range or a filter is its own entry."""

    def __init__(self, ttl: float = 3.0):
        self.ttl = ttl
        self._lock = threading.Lock()
        self._data: dict[str, tuple[float, bytes, str]] = {}
        self.hits = 0
        self.misses = 0

    def enabled(self) -> bool:
        """The environment can turn this off for a whole process - a test suite asserts
        state immediately after a write, which a few seconds of cache deliberately does
        not give."""
        return self.ttl > 0 and default_ttl() > 0

    def get(self, key: str) -> Optional[tuple[bytes, str]]:
        if not self.enabled():
            return None
        with self._lock:
            hit = self._data.get(key)
            if hit and time.time() - hit[0] < self.ttl:
                self.hits += 1
                return hit[1], hit[2]
            if hit:
                del self._data[key]
            self.misses += 1
            return None

    def put(self, key: str, body: bytes, mimetype: str) -> None:
        if not self.enabled():
            return
        with self._lock:
            self._data[key] = (time.time(), body, mimetype)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()

    def stats(self) -> dict:
        with self._lock:
            return {"entries": len(self._data), "ttl_s": self.ttl, "hits": self.hits, "misses": self.misses}


def install(app, cache: Optional[ReadCache] = None) -> ReadCache:
    """Cache repeated GETs in front of the view, and drop the lot on any write."""
    from flask import Response, request

    cache = cache or ReadCache(default_ttl())

    def cacheable(path: str, method: str) -> bool:
        return method == "GET" and path.startswith(CACHE_PATHS) and not path.startswith(SKIP_PREFIXES)

    @app.before_request
    def _serve_from_cache():
        path = request.full_path.rstrip("?")
        if request.method != "GET":
            # A write makes every remembered read wrong, and there is no cheap way to
            # know which ones - so all of them go.
            cache.clear()
            return None
        if not cacheable(path, request.method):
            return None
        hit = cache.get(path)
        if hit is None:
            return None
        body, mimetype = hit
        return Response(body, status=200, mimetype=mimetype, headers={"X-Argos-Cache": "hit"})

    @app.after_request
    def _remember(response):
        path = request.full_path.rstrip("?")
        if (cacheable(path, request.method) and response.status_code == 200
                and not response.direct_passthrough and response.mimetype == "application/json"):
            cache.put(path, response.get_data(), response.mimetype)
        return response

    app.extensions["read_cache"] = cache
    return cache
