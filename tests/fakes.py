"""In-process fakes of the Hugging Face Hub and of llama-swap-rescan's API."""
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


def make_llama_swap(gpu_stats: list[dict], activity: list[dict]):
    """llama-swap-rescan's /api/performance and /api/metrics/activity."""

    class Swap(_Base):
        def do_GET(self):
            url = urllib.parse.urlparse(self.path)
            q = urllib.parse.parse_qs(url.query)
            if url.path == "/health":
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"OK")
            elif url.path == "/running":
                self.send_json({"running": []})
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

    return Swap
