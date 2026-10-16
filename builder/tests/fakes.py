"""In-process fakes of the Hugging Face Hub and of the Argos engine's API."""
from __future__ import annotations

import json
import threading
import urllib.parse
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


@contextmanager
def serve(handler_cls):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{srv.server_port}"
    finally:
        srv.shutdown()
        srv.server_close()


class _Base(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send_json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def make_hub(files: dict[str, bytes], gated: bool = False, repo: str = "Qwen/Qwen3-0.6B-GGUF"):
    """A Hub serving one repo: search, model info with sizes, and ranged downloads."""
    seen_ranges: list[str] = []

    class Hub(_Base):
        def do_GET(self):
            url = urllib.parse.urlparse(self.path)
            if gated and "Authorization" not in self.headers:
                self.send_json({"error": "gated"}, 401)
                return
            if url.path == "/api/models":
                q = urllib.parse.parse_qs(url.query).get("search", [""])[0].lower()
                hits = [{"id": repo, "downloads": 1234, "likes": 5, "lastModified": "2026-01-01T00:00:00Z"}]
                self.send_json([h for h in hits if q in h["id"].lower()])
            elif url.path == f"/api/models/{repo}":
                self.send_json({"id": repo, "siblings": [{"rfilename": n, "size": len(b)} for n, b in files.items()]
                                + [{"rfilename": "README.md", "size": 10}]})
            elif url.path.startswith(f"/{repo}/resolve/main/"):
                name = urllib.parse.unquote(url.path.split("/resolve/main/", 1)[1])
                data = files.get(name)
                if data is None:
                    self.send_json({"error": "nope"}, 404)
                    return
                rng = self.headers.get("Range")
                start = 0
                if rng:
                    seen_ranges.append(rng)
                    start = int(rng.split("=")[1].split("-")[0])
                    if start >= len(data):
                        self.send_response(416)
                        self.end_headers()
                        return
                self.send_response(206 if rng else 200)
                self.send_header("Content-Length", str(len(data) - start))
                self.end_headers()
                self.wfile.write(data[start:])
            else:
                self.send_json({"error": "not found"}, 404)

    Hub.seen_ranges = seen_ranges
    return Hub


def make_engine(gpu_stats: list[dict], activity: list[dict], pool: dict = None, model_ids: list = None,
                    running: list = None, hardware: dict = None, version: dict = None, calls: list = None,
                    chat_reply: str = "ok"):
    """The engine's /api/performance, /api/metrics/activity, /api/pool, /v1/models, /running, model unloads
    and a one-line /v1/chat/completions.

    ``pool`` is served as-is and may be replaced between requests by mutating the dict. Unloading a model removes
    it from ``running``.
    """

    class Swap(_Base):
        def do_GET(self):
            url = urllib.parse.urlparse(self.path)
            q = urllib.parse.parse_qs(url.query)
            if url.path == "/health":
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"OK")
            elif url.path == "/running":
                self.send_json({"running": running if running is not None else []})
            elif url.path == "/api/hardware":
                self.send_json(hardware, 200) if hardware is not None else self.send_json({"error": "no"}, 503)
            elif url.path == "/api/version":
                self.send_json(version or {"version": "test", "commit": "abc", "build_date": "today"})
            elif url.path.startswith("/upstream/"):
                if calls is not None:
                    calls.append(("GET", url.path))
                self.send_json({"ok": True})
            elif url.path.startswith("/logs/stream/"):
                name = urllib.parse.unquote(url.path[len("/logs/stream/"):])
                body = f"log of {name}\nsrv  load_model: loaded\n".encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif url.path == "/api/pool":
                self.send_json(pool if pool is not None else {"router": "group", "enabled": False})
            elif url.path == "/v1/models":
                self.send_json({"object": "list", "data": [{"id": m, "object": "model"} for m in model_ids or []]})
            elif url.path == "/api/performance":
                self.send_json({"enabled": True, "sys_stats": [], "gpu_stats": gpu_stats})
            elif url.path == "/api/metrics/activity":
                min_id = int(q.get("min_id", ["0"])[0])
                limit = int(q.get("limit", ["25"])[0])
                rows = sorted((a for a in activity if a["id"] >= min_id), key=lambda a: a["id"],
                              reverse=q.get("order", ["desc"])[0] == "desc")[:limit]
                self.send_json({"data": rows, "page": 1, "limit": limit, "total": len(activity), "total_pages": 1})
            else:
                self.send_json({"error": "not found"}, 404)

        def do_POST(self):
            if calls is not None:
                calls.append(("POST", self.path))
            size = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(size) or b"{}") if size else {}
            if self.path == "/api/odysseus/refresh":
                self.send_json({"ok": True})
            elif self.path.startswith("/api/models/unload/"):
                name = urllib.parse.unquote(self.path[len("/api/models/unload/"):])
                if running is not None:
                    running[:] = [m for m in running if m.get("model") != name]
                self.send_json({"ok": True})
            elif self.path.endswith("/completion") and self.path.startswith("/upstream/"):
                self.send_json({"content": "x", "timings": {"prompt_n": 520, "prompt_per_second": 800.0, "predicted_n": 128,
                                                            "predicted_per_second": 42.5, "prompt_ms": 650, "predicted_ms": 3000}})
            elif self.path == "/api/models/rescan":
                self.send_json({"ok": True, "models": len(model_ids or []), "changed": False})
            elif self.path == "/v1/chat/completions" and not body.get("stream"):
                self.send_json({"choices": [{"message": {"role": "assistant", "content": chat_reply}}]})
            else:
                self.send_json({"error": "not found"}, 404)

    return Swap
