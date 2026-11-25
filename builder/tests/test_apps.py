"""Telling apps apart behind one address or one key (apps.py): LiteLLM's key alias, X-Argos-App, X-Title, the
User-Agent; sent on to the engine as X-Argos-App, and listed per key."""
import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

SEEN = []


class Engine(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        SEEN.append({"app": self.headers.get("X-Argos-App"), "key": self.headers.get("X-Argos-Key")})
        data = json.dumps({"choices": [{"message": {"content": "x"}}], "usage": {"prompt_tokens": 4, "completion_tokens": 2}}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


@pytest.fixture
def gw(tmp_path):
    from src.app import create_app
    eng = ThreadingHTTPServer(("127.0.0.1", 0), Engine)
    threading.Thread(target=eng.serve_forever, daemon=True).start()
    app = create_app({"DB": str(tmp_path / "t.db"), "RUNS_DIR": str(tmp_path / "runs"), "MODELS_DIR": str(tmp_path / "m"),
                      "ENGINE_URL": f"http://127.0.0.1:{eng.server_address[1]}", "HF_ENDPOINT": "http://127.0.0.1:9",
                      "TELEMETRY": False, "BACKGROUND": False, "UI_PASSWORD": ""})
    SEEN.clear()
    yield app
    app.extensions["pb"].mcp.close()
    app.extensions["pb"].store.close()
    eng.shutdown()


def test_Apps_identify_in_order():
    from src.apps import identify
    lite = {"User-Agent": "AsyncOpenAI/Python 1.99.1", "x-litellm-user-api-key-alias": "codex",
            "x-litellm-user-api-key-team-alias": "coders", "X-Title": "Codex CLI"}
    assert identify(lite) == ("codex", "LiteLLM key")
    assert identify({**lite, "X-Argos-App": "my-agent"}) == ("my-agent", "X-Argos-App"), "the app's own word first"
    assert identify({"User-Agent": "AsyncOpenAI/Python 1.99.1", "x-litellm-user-api-key-team-alias": "coders"}) == ("coders", "LiteLLM team")
    assert identify({"User-Agent": "AsyncOpenAI/Python 1.99.1", "X-Title": "Hermes Agent"}) == ("Hermes Agent", "X-Title")
    assert identify({"User-Agent": "codex_cli_rs/0.40.0 (Linux; x86_64) xterm"}) == ("codex_cli_rs", "User-Agent"), "no version"
    assert identify({"User-Agent": "AsyncOpenAI/Python 1.99.1"}) == ("OpenAI/Python", "User-Agent")
    assert identify({}) == ("", "")
    assert identify({"X-Argos-App": "a\x00b\nc" + "z" * 100})[0] == "a b c" + "z" * 55, "cleaned and cut"


def test_Apps_waitress_lets_litellm_headers_through_and_nothing_else():
    """Waitress drops headers with underscores; LiteLLM's x-litellm-user_api_key_alias is let in with dashes, every
    other underscore header (X_Forwarded_For) still dropped."""
    import waitress
    from src.apps import patch_waitress
    patch_waitress()
    got = {}

    def app(environ, start):
        got.update({k: v for k, v in environ.items() if k.startswith("HTTP_X")})
        start("200 OK", [("Content-Type", "text/plain")])
        return [b"ok"]

    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    threading.Thread(target=lambda: waitress.serve(app, host="127.0.0.1", port=port, _quiet=True), daemon=True).start()
    for _ in range(50):
        try:
            c = socket.create_connection(("127.0.0.1", port))
            break
        except OSError:
            time.sleep(0.05)
    c.sendall(b"GET / HTTP/1.1\r\nHost: x\r\nx-litellm-user_api_key_alias: codex\r\nX_Forwarded_For: 6.6.6.6\r\n"
              b"X-Title: Hermes\r\nConnection: close\r\n\r\n")
    while c.recv(4096):
        pass
    c.close()
    assert got.get("HTTP_X_LITELLM_USER_API_KEY_ALIAS") == "codex"
    assert got.get("HTTP_X_TITLE") == "Hermes"
    assert "HTTP_X_FORWARDED_FOR" not in got, "other underscore headers are still dropped"


def test_Apps_behind_one_key_are_named_to_the_engine_and_listed(gw):
    c = gw.test_client()
    k = c.post("/api/security/keys", json={"name": "litellm"}).get_json()["key"]
    out = gw.test_client()
    out.environ_base.update({"REMOTE_ADDR": "192.168.20.7", "HTTP_AUTHORIZATION": f"Bearer {k['key']}",
                             "HTTP_USER_AGENT": "AsyncOpenAI/Python 1.99.1"})
    for alias in ("codex", "codex", "hermes"):
        r = out.post("/v1/chat/completions", json={"model": "fast", "messages": []},
                     headers={"x-litellm-user-api-key-alias": alias})
        assert r.status_code == 200 and r.get_json()["choices"]  # read: usage is counted as the answer ends
    r = out.post("/v1/chat/completions", json={"model": "fast", "messages": []}, headers={"X-Argos-App": "spoof", "X-Title": "Open WebUI"})
    assert r.get_json()["choices"]
    assert [s["app"] for s in SEEN] == ["codex", "codex", "hermes", "spoof"]
    assert {s["key"] for s in SEEN} == {"litellm"}
    apps = {a["app"]: a for a in c.get("/api/security/usage").get_json()["apps"]}
    assert apps["codex"]["requests"] == 2 and apps["codex"]["input_tokens"] == 8 and apps["codex"]["how"] == "LiteLLM key"
    assert apps["hermes"]["requests"] == 1 and apps["hermes"]["last_model"] == "fast"
    assert c.delete(f"/api/security/keys/{k['id']}/apps?app=hermes").status_code == 200
    assert {a["app"] for a in c.get("/api/security/usage").get_json()["apps"]} == {"codex", "spoof"}
    c.delete(f"/api/security/keys/{k['id']}")
    assert c.get("/api/security/usage").get_json()["apps"] == [], "a deleted key takes its apps along"


def test_Apps_a_client_cannot_pass_its_own_label_through_as_is(gw):
    """X-Argos-App is what the builder made out; an app's own header is cleaned and cut, never sent twice."""
    out = gw.test_client()
    out.post("/v1/chat/completions", json={"model": "fast", "messages": []}, headers={"X-Argos-App": "a\tb" + "c" * 200})
    assert SEEN[-1]["app"] == "a b" + "c" * 57


def test_Apps_a_key_lists_at_most_so_many_apps(gw):
    from src import apps
    store = gw.extensions["pb"].store
    for i in range(apps.MAX_APPS_PER_KEY + 5):
        apps.record(store, 1, f"app{i}", "X-Title", "m", 1, 1)
    apps.record(store, 1, "app0", "X-Title", "m", 1, 1)
    rows = apps.seen(store, 1)
    assert len(rows) == apps.MAX_APPS_PER_KEY
    assert next(r for r in rows if r["app"] == "app0")["requests"] == 2, "a known app keeps counting"


def test_Apps_a_utf8_name_in_a_header_is_read_as_written():
    from src.apps import identify
    raw = "ünïcode app".encode("utf-8").decode("latin-1")  # what WSGI hands over
    assert identify({"X-Argos-App": raw})[0] == "ünïcode app"
    assert identify({"X-Argos-App": "caf\xe9"})[0] == "caf\xe9", "Latin-1 that is not UTF-8 stays as it is"


def test_Apps_any_app_name_reaches_the_engine(gw):
    out = gw.test_client()
    raw = "编码 agent 🚀".encode("utf-8").decode("latin-1")
    r = out.post("/v1/chat/completions", json={"model": "fast", "messages": []}, headers={"X-Argos-App": raw})
    assert r.status_code == 200 and r.get_json()["choices"]
    assert SEEN[-1]["app"].encode("latin-1").decode("utf-8") == "编码 agent 🚀"
