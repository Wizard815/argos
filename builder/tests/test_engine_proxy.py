"""One public port: the builder answers its own routes and forwards the rest to the engine."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest


class FakeEngine(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, status, body, ctype="application/json"):
        data = body.encode() if isinstance(body, str) else body
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/" or self.path.startswith("/ui"):
            return self._send(200, "<html>llama-swap ui</html>", "text/html")
        if self.path.startswith("/v1/models"):
            return self._send(200, json.dumps({"engine": "models"}))
        if self.path.startswith("/api/profiles"):
            return self._send(200, json.dumps({"engine": "profiles"}))
        if self.path == "/sse":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            self.wfile.write(b"data: one\n\n")
            self.wfile.flush()
            return
        self._send(404, json.dumps({"error": "engine 404"}))

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(n)
        self._send(200, json.dumps({"engine_echo": body.decode()}))


@pytest.fixture
def engine_url():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), FakeEngine)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


@pytest.fixture
def proxied(tmp_path, engine_url):
    from src.app import create_app

    app = create_app({
        "DB": str(tmp_path / "test.db"),
        "RUNS_DIR": str(tmp_path / "runs"),
        "MODELS_DIR": str(tmp_path / "models"),
        "ENGINE_URL": engine_url,
        "HF_ENDPOINT": "http://127.0.0.1:9",
        "TELEMETRY": False,
        "BACKGROUND": False,
    })
    yield app.test_client()
    app.extensions["runner"].stop_all()


def test_builder_owns_its_routes(proxied):
    # the builder's own /api/profiles (GET) wins over the engine's
    r = proxied.get("/api/profiles")
    assert r.status_code == 200
    assert b"engine" not in r.data


def test_engine_paths_are_forwarded(proxied):
    r = proxied.get("/v1/models")
    assert r.status_code == 200 and json.loads(r.data) == {"engine": "models"}


def test_engine_post_body_is_forwarded(proxied):
    r = proxied.post("/v1/chat/completions", json={"model": "x"})
    assert r.status_code == 200
    assert json.loads(json.loads(r.data)["engine_echo"]) == {"model": "x"}


def test_engine_same_name_reachable_under_engine_prefix(proxied):
    r = proxied.get("/engine/api/profiles")
    assert r.status_code == 200 and json.loads(r.data) == {"engine": "profiles"}


def test_engine_web_page_is_blocked(proxied):
    assert proxied.get("/engine/ui").status_code == 404
    assert proxied.get("/engine/ui/models").status_code == 404
    assert proxied.get("/ui").status_code == 404
    assert proxied.get("/ui/models").status_code == 404


def test_engine_error_status_passes_through(proxied):
    r = proxied.get("/no/such/engine/path")
    assert r.status_code == 404 and json.loads(r.data) == {"error": "engine 404"}


def test_streaming_passthrough(proxied):
    r = proxied.get("/sse")
    assert r.status_code == 200
    assert r.mimetype == "text/event-stream"
    assert b"data: one" in r.data


def test_engine_unreachable_is_502(tmp_path):
    from src.app import create_app

    app = create_app({
        "DB": str(tmp_path / "t.db"), "RUNS_DIR": str(tmp_path / "r"), "MODELS_DIR": str(tmp_path / "m"),
        "ENGINE_URL": "http://127.0.0.1:9", "HF_ENDPOINT": "http://127.0.0.1:9",
        "TELEMETRY": False, "BACKGROUND": False,
    })
    try:
        r = app.test_client().get("/v1/models")
        assert r.status_code == 502
    finally:
        app.extensions["runner"].stop_all()


def test_no_engine_configured_is_builder_404(tmp_path):
    from src.app import create_app

    app = create_app({
        "DB": str(tmp_path / "t.db"), "RUNS_DIR": str(tmp_path / "r"), "MODELS_DIR": str(tmp_path / "m"),
        "ENGINE_URL": "", "HF_ENDPOINT": "http://127.0.0.1:9", "TELEMETRY": False, "BACKGROUND": False,
    })
    try:
        r = app.test_client().get("/v1/models")
        assert r.status_code == 404
    finally:
        app.extensions["runner"].stop_all()
