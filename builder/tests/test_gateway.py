"""The gateway LiteLLM used to be: key rules and limits, usage per key, the answer cache, cloud fallback, the MCP
gateway (/mcp and /mcp/<server>) and MCP tools run inside a chat request."""
import json
import sys
import textwrap
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

OUT = {"REMOTE_ADDR": "192.168.20.7"}
CALLS = []


class Engine(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, status, obj, ctype="application/json"):
        data = obj if isinstance(obj, bytes) else json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path.startswith("/v1/models"):
            return self._send(200, {"data": [{"id": "fast"}, {"id": "chat"}, {"id": "gemma--big"}]})
        self._send(404, {"error": "no"})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        CALLS.append(body)
        m = body.get("model")
        if m == "broken":
            return self._send(503, {"error": "upstream failed to start"})
        if m == "slow":
            import time
            time.sleep(1.5)
        if m == "small":
            return self._send(400, {"error": {"message": "the request exceeds the available context size"}})
        usage = {"prompt_tokens": 10, "completion_tokens": 5}
        if body.get("stream"):
            data = (b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\n'
                    b'data: {"choices":[],"usage":{"prompt_tokens":7,"completion_tokens":3}}\n\ndata: [DONE]\n\n')
            return self._send(200, data, "text/event-stream")
        msgs = body.get("messages") or []
        tools = [t["function"]["name"] for t in body.get("tools") or [] if t.get("type") == "function"]
        if tools and not any(x.get("role") == "tool" for x in msgs):
            call = {"id": "c1", "type": "function", "function": {"name": tools[0], "arguments": json.dumps({"text": "ping"})}}
            return self._send(200, {"id": "x", "model": m, "choices": [{"message": {"role": "assistant", "content": None,
                                    "tool_calls": [call]}, "finish_reason": "tool_calls"}], "usage": usage})
        said = next((x["content"] for x in msgs if x.get("role") == "tool"), "")
        self._send(200, {"id": "x", "model": m, "choices": [{"message": {"role": "assistant", "content": f"answer {said}"},
                                                              "finish_reason": "stop"}], "usage": usage})


class Cloud(Engine):
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        self._send(200, {"cloud": True, "model": body.get("model"), "path": self.path,
                         "auth": self.headers.get("Authorization"), "usage": {"prompt_tokens": 1, "completion_tokens": 1}})


def _serve(handler):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


MCP_SERVER = textwrap.dedent('''
    import json, sys
    for line in sys.stdin:
        m = json.loads(line)
        if "id" not in m:
            continue
        r = {}
        if m["method"] == "initialize":
            r = {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}}, "serverInfo": {"name": "t"}}
        elif m["method"] == "tools/list":
            r = {"tools": [{"name": "echo", "description": "say it back", "inputSchema": {"type": "object"}},
                           {"name": "rm", "description": "dangerous", "inputSchema": {"type": "object"}}]}
        elif m["method"] == "tools/call":
            r = {"content": [{"type": "text", "text": "echo:" + m["params"]["arguments"].get("text", "")}]}
        sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": m["id"], "result": r}) + "\\n")
        sys.stdout.flush()
''')


@pytest.fixture
def gw(tmp_path):
    from src.app import create_app
    eng, cloud = _serve(Engine), _serve(Cloud)
    app = create_app({"DB": str(tmp_path / "t.db"), "RUNS_DIR": str(tmp_path / "runs"), "MODELS_DIR": str(tmp_path / "m"),
                      "ENGINE_URL": f"http://127.0.0.1:{eng.server_address[1]}", "HF_ENDPOINT": "http://127.0.0.1:9",
                      "TELEMETRY": False, "BACKGROUND": False, "UI_PASSWORD": ""})
    script = tmp_path / "mcp_echo.py"
    script.write_text(MCP_SERVER)
    app.cloud_url = f"http://127.0.0.1:{cloud.server_address[1]}/v1"
    app.mcp_script = str(script)
    CALLS.clear()
    yield app
    app.extensions["pb"].mcp.close()
    app.extensions["pb"].store.close()
    eng.shutdown()
    cloud.shutdown()


def _key(app, name="Hermes", rules=None):
    c = app.test_client()
    k = c.post("/api/security/keys", json={"name": name}).get_json()["key"]
    if rules is not None:
        r = c.put(f"/api/security/keys/{k['id']}", json={"rules": rules})
        assert r.status_code == 200, r.get_json()
    return k


def _out(app, key=None):
    c = app.test_client()
    c.environ_base.update(OUT)
    if key:
        c.environ_base["HTTP_AUTHORIZATION"] = f"Bearer {key['key']}"
    return c


def test_Gateway_a_key_reaches_only_its_models_and_sees_only_them(gw):
    app = gw
    app.test_client().put("/api/gateway/groups", json={"groups": {"chatty": ["chat"]}})
    k = _key(app, rules={"models": ["fast", "@chatty"]})
    c = _out(app, k)
    assert c.post("/v1/chat/completions", json={"model": "fast", "messages": []}).status_code == 200
    assert c.post("/v1/chat/completions", json={"model": "chat", "messages": []}).status_code == 200, "through the group"
    no = c.post("/v1/chat/completions", json={"model": "gemma--big", "messages": []})
    assert no.status_code == 403 and no.get_json()["error"]["code"] == "model_not_allowed"
    assert [m["id"] for m in c.get("/v1/models").get_json()["data"]] == ["fast", "chat"]
    other = _key(app, "Codex")
    assert len(_out(app, other).get("/v1/models").get_json()["data"]) == 3, "a key without a list sees everything"


def test_Gateway_blocked_or_expired_keys_are_refused_with_the_reason(gw):
    k = _key(gw, rules={"blocked": True})
    r = _out(gw, k).post("/v1/chat/completions", json={"model": "fast"})
    assert r.status_code == 401 and "blocked" in r.get_json()["error"]["message"]
    k2 = _key(gw, "old", rules={"expires_at": 1000})
    assert "expired" in _out(gw, k2).post("/v1/chat/completions", json={"model": "fast"}).get_json()["error"]["message"]


def test_Gateway_limits_and_usage_per_key(gw):
    k = _key(gw, rules={"rpm": 2})
    c = _out(gw, k)
    assert c.post("/v1/chat/completions", json={"model": "fast", "messages": []}).get_json()["choices"]
    assert b"[DONE]" in c.post("/v1/chat/completions", json={"model": "fast", "messages": [], "stream": True}).data
    third = c.post("/v1/chat/completions", json={"model": "fast", "messages": []})
    assert third.status_code == 429 and "2 requests a minute" in third.get_json()["error"]["message"]
    rows = gw.test_client().get("/api/security/usage").get_json()["rows"]
    assert rows[0]["requests"] == 2 and rows[0]["input_tokens"] == 17 and rows[0]["output_tokens"] == 8, \
        "plain and streamed answers both counted"
    q = _key(gw, "quota", rules={"daily_tokens": 10})
    cq = _out(gw, q)
    assert cq.post("/v1/chat/completions", json={"model": "fast", "messages": []}).get_json()["choices"]
    assert cq.post("/v1/chat/completions", json={"model": "fast", "messages": []}).status_code == 429, "15 of 10 used"


def test_Gateway_cache_answers_a_repeat_with_temperature_0(gw):
    c = gw.test_client()
    c.put("/api/gateway/cache", json={"enabled": True})
    req = {"model": "fast", "messages": [{"role": "user", "content": "hi"}], "temperature": 0}
    a = c.post("/v1/chat/completions", json=req).get_json()
    b = c.post("/v1/chat/completions", json=req)
    assert a == b.get_json() and b.headers.get("X-Argos-Cache") == "hit" and len(CALLS) == 1
    c.post("/v1/chat/completions", json={**req, "temperature": 0.7}).get_data()
    c.post("/v1/chat/completions", json={**req, "temperature": 0.7}).get_data()
    assert len(CALLS) == 3, "sampled answers are never cached"


def test_Gateway_falls_back_to_the_cloud_when_local_is_down_or_too_small(gw):
    c = gw.test_client()
    r = c.post("/api/gateway/fallbacks", json={"match": "broken", "url": gw.cloud_url, "api_key": "sk-x", "model": "gpt-4o-mini"})
    assert r.status_code == 200 and r.get_json()["fallbacks"][0]["has_key"] and "api_key" not in r.get_json()["fallbacks"][0]
    c.post("/api/gateway/fallbacks", json={"name": "small", "match": "small", "url": gw.cloud_url, "on_down": False})
    got = c.post("/v1/chat/completions", json={"model": "broken", "messages": []})
    j = got.get_json()
    assert j["cloud"] and j["model"] == "gpt-4o-mini" and j["path"] == "/v1/chat/completions" and j["auth"] == "Bearer sk-x"
    assert got.headers["X-Argos-Fallback"] == "broken"
    assert c.post("/v1/chat/completions", json={"model": "small", "messages": []}).get_json()["cloud"], "prompt too long"
    assert c.post("/v1/chat/completions", json={"model": "fast", "messages": []}).get_json()["choices"], "others stay local"
    c.post("/api/gateway/fallbacks", json={"name": "slow", "match": "slow", "url": gw.cloud_url, "wait_s": 0.3})
    assert c.post("/v1/chat/completions", json={"model": "slow", "messages": []}).get_json()["cloud"], "no answer in time"


def _add_mcp(app, name="tools", disabled=None):
    r = app.test_client().post("/api/mcp/servers", json={"name": name, "transport": "stdio", "command": sys.executable,
                                                          "args": [app.mcp_script], "disabled_tools": disabled or []})
    assert r.status_code == 200, r.get_json()


def _rpc(c, path, method, params=None):
    r = c.post(path, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}})
    return r.status_code, r.get_json()


def test_Gateway_mcp_all_in_one_and_single_links(gw):
    _add_mcp(gw, "tools", disabled=["rm"])
    _add_mcp(gw, "more")
    c = gw.test_client()
    _, init = _rpc(c, "/mcp", "initialize", {"protocolVersion": "2025-06-18"})
    assert init["result"]["protocolVersion"] == "2025-06-18"
    _, lst = _rpc(c, "/mcp", "tools/list")
    assert sorted(t["name"] for t in lst["result"]["tools"]) == ["more__echo", "more__rm", "tools__echo"], "rm is off on tools"
    _, one = _rpc(c, "/mcp/tools", "tools/list")
    assert [t["name"] for t in one["result"]["tools"]] == ["echo"], "a single link: its own names"
    _, called = _rpc(c, "/mcp", "tools/call", {"name": "tools__echo", "arguments": {"text": "hi"}})
    assert called["result"]["content"][0]["text"] == "echo:hi"
    _, called = _rpc(c, "/mcp/more", "tools/call", {"name": "echo", "arguments": {"text": "yo"}})
    assert called["result"]["content"][0]["text"] == "echo:yo"
    _, skip = _rpc(c, "/mcp?skip=more", "tools/list")
    assert [t["name"] for t in skip["result"]["tools"]] == ["tools__echo"]
    _, off = _rpc(c, "/mcp/tools", "tools/call", {"name": "rm", "arguments": {}})
    assert off["result"]["isError"], "a turned-off tool cannot be called either"


def test_Gateway_a_key_can_be_kept_from_some_mcp_servers(gw):
    _add_mcp(gw, "tools")
    _add_mcp(gw, "more")
    k = _key(gw, rules={"mcp_mode": "except", "mcps": ["more"]})
    c = _out(gw, k)
    _, lst = _rpc(c, "/mcp", "tools/list")
    assert {t["name"].split("__")[0] for t in lst["result"]["tools"]} == {"tools"}
    status, _ = _rpc(c, "/mcp/more", "tools/list")
    assert status == 403
    _, called = _rpc(c, "/mcp", "tools/call", {"name": "more__echo", "arguments": {}})
    assert called["result"]["isError"]


def test_Gateway_chat_runs_mcp_tools_when_approval_is_never(gw):
    _add_mcp(gw, "tools", disabled=["rm"])
    c = gw.test_client()
    req = {"model": "fast", "messages": [{"role": "user", "content": "go"}],
           "tools": [{"type": "mcp", "server_url": "argos/tools", "require_approval": "never"}]}
    j = c.post("/v1/chat/completions", json=req).get_json()
    assert j["choices"][0]["message"]["content"] == "answer echo:ping"
    assert j["argos_mcp"]["calls"][0]["tool"] == "tools__echo" and j["argos_mcp"]["calls"][0]["ok"]
    assert [t["function"]["name"] for t in CALLS[0]["tools"]] == ["tools__echo"]
    assert j["usage"]["prompt_tokens"] == 20, "both rounds counted"
    # without approval "never" the tool call goes back to the app
    CALLS.clear()
    j = c.post("/v1/chat/completions", json={**req, "tools": [{"type": "mcp", "server_url": "argos"}]}).get_json()
    assert j["choices"][0]["message"]["tool_calls"][0]["function"]["name"] == "tools__echo" and len(CALLS) == 1
    # streamed: one chunk with the answer
    s = c.post("/v1/chat/completions", json={**req, "stream": True})
    assert s.mimetype == "text/event-stream" and b"answer echo:ping" in s.data and s.data.endswith(b"data: [DONE]\n\n")


class HttpMCP(BaseHTTPRequestHandler):
    """A streamable HTTP MCP server that wants a session and answers as an event stream."""

    def log_message(self, *a):
        pass

    def do_POST(self):
        m = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
        if m.get("method") != "initialize" and self.headers.get("Mcp-Session-Id") != "s1":
            self.send_response(400)
            self.end_headers()
            return
        if "id" not in m:
            self.send_response(202)
            self.end_headers()
            return
        r = {"protocolVersion": "2025-06-18", "capabilities": {}} if m["method"] == "initialize" else \
            {"tools": [{"name": "time", "inputSchema": {"type": "object"}}]} if m["method"] == "tools/list" else \
            {"content": [{"type": "text", "text": f"auth={self.headers.get('Authorization')}"}]}
        data = b"event: message\ndata: " + json.dumps({"jsonrpc": "2.0", "id": m["id"], "result": r}).encode() + b"\n\n"
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Mcp-Session-Id", "s1")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def test_Gateway_mcp_over_http_with_a_session_and_headers(gw, monkeypatch):
    srv = _serve(HttpMCP)
    monkeypatch.setenv("CLOCK_TOKEN", "t0k")
    c = gw.test_client()
    r = c.post("/api/mcp/servers", json={"name": "clock", "url": f"http://127.0.0.1:{srv.server_address[1]}/mcp",
                                         "headers": {"Authorization": "Bearer ${CLOCK_TOKEN}"}})
    assert r.get_json()["servers"][0]["headers"] == {"Authorization": "••••"}, "tokens are never shown again"
    _, called = _rpc(c, "/mcp/clock", "tools/call", {"name": "time", "arguments": {}})
    assert called["result"]["content"][0]["text"] == "auth=Bearer t0k"
    assert c.post("/api/mcp/servers/clock/test").get_json()["tools"][0]["name"] == "time"
    srv.shutdown()


def test_Gateway_a_cloud_fallback_goes_through_the_https_proxy(monkeypatch):
    """HTTPS_PROXY is honoured for servers outside, never for the engine on 127.0.0.1."""
    from src import engine_proxy
    seen = []

    class Conn:
        def __init__(self, host, port, timeout=None):
            seen.append(("connect", host, port))
            self.sock = None

        def set_tunnel(self, host, port):
            seen.append(("tunnel", host, port))

        def request(self, *a, **k):
            pass

        def getresponse(self):
            return "resp"

        def close(self):
            pass

    monkeypatch.setattr(engine_proxy.http.client, "HTTPSConnection", Conn)
    monkeypatch.setattr(engine_proxy.http.client, "HTTPConnection", Conn)
    for name in ("HTTPS_PROXY", "https_proxy"):
        monkeypatch.setenv(name, "http://proxy.lan:3128")
    for name in ("NO_PROXY", "no_proxy"):
        monkeypatch.setenv(name, "localhost,127.0.0.1")
    engine_proxy._open("https://openrouter.ai", "POST", "/api/v1/chat/completions", b"{}", {})
    assert seen == [("connect", "proxy.lan", 3128), ("tunnel", "openrouter.ai", 443)]
    seen.clear()
    engine_proxy._open("http://127.0.0.1:8081", "POST", "/v1/chat/completions", b"{}", {})
    assert seen == [("connect", "127.0.0.1", 8081)]


def test_Gateway_waitress_keeps_the_forwarded_headers():
    """A reverse proxy on this host connects from 127.0.0.1; without its X-Forwarded-For it would pass for a request
    from inside the container, which needs no key."""
    import inspect
    from src import app as app_module
    assert "clear_untrusted_proxy_headers=False" in inspect.getsource(app_module.main)


def test_Gateway_a_key_cannot_reach_other_models_through_upstream(gw):
    k = _key(gw, rules={"models": ["fast"]})
    c = _out(gw, k)
    assert c.get("/upstream/chat/health").status_code == 403
    assert c.get("/upstream/fast/health").status_code != 403
    assert c.get("/v1/models?model=chat").status_code == 200, "a list is not a model call"
