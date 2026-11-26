"""The six gateway additions end to end through the builder: per-app rules, dollar budgets, MCP tool rules and the
tool-call log, retries and cooldown, Responses/Messages conversion, request traces."""
import json
import textwrap
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

STATE = {"flaky": 0, "engine": [], "cloud": [], "spans": []}


def _send(h, status, obj, ctype="application/json"):
    data = obj if isinstance(obj, bytes) else json.dumps(obj).encode()
    h.send_response(status)
    h.send_header("Content-Type", ctype)
    h.send_header("Content-Length", str(len(data)))
    h.end_headers()
    h.wfile.write(data)


def _chat_answer(body, text="hello"):
    if body.get("stream"):
        return (b'data: {"choices":[{"index":0,"delta":{"content":"hel"}}]}\n\n'
                b'data: {"choices":[{"index":0,"delta":{"content":"lo"},"finish_reason":"stop"}]}\n\n'
                b'data: {"choices":[],"usage":{"prompt_tokens":1000,"completion_tokens":500}}\n\ndata: [DONE]\n\n'), "text/event-stream"
    return {"id": "x", "choices": [{"message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1000, "completion_tokens": 500}}, "application/json"


class Engine(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        _send(self, 200, {"data": [{"id": m} for m in ("fast", "flaky", "down", "coder", "gemma")]})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        STATE["engine"].append((self.path, body.get("model")))
        STATE.setdefault("bodies", []).append(body)
        if self.path in ("/v1/responses", "/v1/messages"):
            return _send(self, 404, {"error": "unknown path"})
        m = body.get("model")
        if m == "down":
            return _send(self, 503, {"error": {"message": "upstream failed to start"}})
        if m == "flaky":
            STATE["flaky"] += 1
            if STATE["flaky"] % 2 == 1:
                return _send(self, 503, {"error": {"message": "loading, try again"}})
        if m == "guarded":
            return _send(self, 502, {"error": {"message": "guarded: stopped by the loop guard"}})
        obj, ctype = _chat_answer(body)
        _send(self, 200, obj, ctype)


class Cloud(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        STATE["cloud"].append((self.path, body.get("model"), body))
        if body.get("model") == "busy" and len([c for c in STATE["cloud"] if c[1] == "busy"]) % 2 == 1:
            return _send(self, 429, {"error": {"message": "rate limited"}})
        if body.get("model") == "dead":
            return _send(self, 500, {"error": {"message": "boom"}})
        obj, ctype = _chat_answer(body, "from the cloud")
        _send(self, 200, obj, ctype)


class Collector(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        d = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
        for rs in d["resourceSpans"]:
            for ss in rs["scopeSpans"]:
                STATE["spans"].extend(ss["spans"])
        _send(self, 200, {})


def _serve(h):
    s = ThreadingHTTPServer(("127.0.0.1", 0), h)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    return s


MCP = textwrap.dedent('''
    import json, sys
    for line in sys.stdin:
        m = json.loads(line)
        if "id" not in m:
            continue
        r = {}
        if m["method"] == "initialize":
            r = {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}}, "serverInfo": {"name": "t"}}
        elif m["method"] == "tools/list":
            r = {"tools": [{"name": "read", "inputSchema": {"type": "object"}}, {"name": "rm", "inputSchema": {"type": "object"}}]}
        elif m["method"] == "tools/call":
            r = {"content": [{"type": "text", "text": "did " + m["params"]["name"]}]}
        sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": m["id"], "result": r}) + "\\n")
        sys.stdout.flush()
''')


@pytest.fixture
def gw(tmp_path, monkeypatch):
    for v in ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy"):
        monkeypatch.delenv(v, raising=False)
    from src.app import create_app
    eng, cloud, col = _serve(Engine), _serve(Cloud), _serve(Collector)
    app = create_app({"DB": str(tmp_path / "t.db"), "RUNS_DIR": str(tmp_path / "runs"), "MODELS_DIR": str(tmp_path / "m"),
                      "ENGINE_URL": f"http://127.0.0.1:{eng.server_address[1]}", "HF_ENDPOINT": "http://127.0.0.1:9",
                      "TELEMETRY": False, "BACKGROUND": False, "UI_PASSWORD": ""})
    c = app.test_client()
    r = c.post("/api/providers", json={"name": "cloud", "url": f"http://127.0.0.1:{cloud.server_address[1]}/v1",
                                       "api_key": "k", "models": ["paid", "busy", "dead", "free"],
                                       "prices": "paid: 10, 20\nbusy: 10, 20\ndead: 10, 20"})
    assert r.status_code == 200, r.get_json()
    (tmp_path / "mcp.py").write_text(MCP)
    import sys
    r = c.post("/api/mcp/servers", json={"name": "files", "command": f"{sys.executable} {tmp_path / 'mcp.py'}"})
    assert r.status_code == 200, r.get_json()
    app.collector = f"http://127.0.0.1:{col.server_address[1]}"
    app.cloud_url = f"http://127.0.0.1:{cloud.server_address[1]}/v1"
    for k in ("engine", "cloud", "spans"):
        STATE[k].clear()
    STATE["flaky"] = 0
    yield app
    app.extensions["traces"].close()
    app.extensions["pb"].mcp.close()
    app.extensions["pb"].store.close()
    for s in (eng, cloud, col):
        s.shutdown()


def _key(app, name="litellm", rules=None):
    c = app.test_client()
    k = c.post("/api/security/keys", json={"name": name}).get_json()["key"]
    if rules is not None:
        r = c.put(f"/api/security/keys/{k['id']}", json={"rules": rules})
        assert r.status_code == 200, r.get_json()
    return k


def _as(app, key, alias=None):
    c = app.test_client()
    c.environ_base.update({"REMOTE_ADDR": "192.168.20.7", "HTTP_AUTHORIZATION": f"Bearer {key['key']}"})
    if alias:
        c.environ_base["HTTP_X_ARGOS_APP"] = alias
    return c


def _chat(c, model, **kw):
    r = c.post("/v1/chat/completions", json={"model": model, "messages": [{"role": "user", "content": "hi"}], **kw})
    r.get_data()  # read it all: usage is counted as the answer ends
    return r


# ----- per-app rules ------------------------------------------------------------------------------------------------
def test_Flow_app_rules_under_one_key(gw):
    k = _key(gw)
    admin = gw.test_client()
    assert admin.put(f"/api/security/keys/{k['id']}/apps/codex", json={"models": "coder", "rpm": 2}).status_code == 200
    assert admin.put(f"/api/security/keys/{k['id']}/apps/spam", json={"blocked": True}).status_code == 200
    codex, hermes, spam = _as(gw, k, "codex"), _as(gw, k, "hermes"), _as(gw, k, "spam")
    assert _chat(codex, "coder").status_code == 200
    r = _chat(codex, "gemma")
    assert r.status_code == 403 and "the app codex on this key may not use the model gemma" in r.get_json()["error"]["message"]
    assert _chat(codex, "coder").status_code == 200
    r = _chat(codex, "coder")
    assert r.status_code == 429 and "2 requests a minute for the app codex" in r.get_json()["error"]["message"]
    assert _chat(hermes, "gemma").status_code == 200, "the key's other apps are not limited"
    assert _chat(spam, "fast").status_code == 403
    ids = [m["id"] for m in codex.get("/v1/models").get_json()["data"]]
    assert "coder" in ids and "gemma" not in ids, "the app sees only its models"
    # the key's own form saved without apps keeps them
    admin.put(f"/api/security/keys/{k['id']}", json={"rules": {"rpm": 100}})
    rules = admin.get("/api/security").get_json()["keys"][0]["rules"]
    assert set(rules["apps"]) == {"codex", "spam"} and rules["rpm"] == 100
    assert admin.delete(f"/api/security/keys/{k['id']}/apps/spam/rules").status_code == 200
    assert _chat(spam, "fast").status_code == 200


def test_Flow_app_daily_tokens(gw):
    k = _key(gw)
    gw.test_client().put(f"/api/security/keys/{k['id']}/apps/codex", json={"daily_tokens": 1200})
    codex = _as(gw, k, "codex")
    assert _chat(codex, "fast").status_code == 200  # 1500 tokens
    r = _chat(codex, "fast")
    assert r.status_code == 429 and "tokens a day for the app codex" in r.get_json()["error"]["message"]
    assert _chat(_as(gw, k, "other"), "fast").status_code == 200


# ----- dollar budgets ---------------------------------------------------------------------------------------------
def test_Flow_dollar_budget_stops_cloud_only(gw):
    k = _key(gw, rules={"daily_usd": 0.015})
    c = _as(gw, k, "hermes")
    assert _chat(c, "cloud/paid").status_code == 200  # 1000 in at $10/M + 500 out at $20/M = $0.02
    usage = gw.test_client().get("/api/security/usage").get_json()
    assert usage["rows"][0]["cost_usd"] == pytest.approx(0.02)
    r = _chat(c, "cloud/paid")
    assert r.status_code == 429 and "budget" in r.get_json()["error"]["message"]
    assert _chat(c, "fast").status_code == 200, "local models still answer"
    assert _chat(c, "cloud/free").status_code == 200, "a cloud model with no price costs nothing"
    s = _chat(c, "cloud/paid", stream=True)
    assert s.status_code == 429


def test_Flow_streamed_cloud_cost_is_counted(gw):
    k = _key(gw)
    c = _as(gw, k, "codex")
    r = _chat(c, "cloud/paid", stream=True)
    assert r.status_code == 200 and b"[DONE]" in r.data
    apps = {a["app"]: a for a in gw.test_client().get("/api/security/usage").get_json()["apps"]}
    assert apps["codex"]["input_tokens"] == 1000
    rows = gw.test_client().get("/api/security/usage").get_json()["rows"]
    assert rows[0]["cost_usd"] == pytest.approx(0.02)


def test_Flow_app_budget(gw):
    k = _key(gw)
    gw.test_client().put(f"/api/security/keys/{k['id']}/apps/codex", json={"daily_usd": 0.01})
    codex = _as(gw, k, "codex")
    assert _chat(codex, "cloud/paid").status_code == 200
    r = _chat(codex, "cloud/paid")
    assert r.status_code == 429 and "the app codex" in r.get_json()["error"]["message"]
    assert _chat(_as(gw, k, "hermes"), "cloud/paid").status_code == 200


def test_Flow_budget_spent_keeps_off_the_paid_fallback(gw):
    admin = gw.test_client()
    admin.post("/api/gateway/fallbacks", json={"name": "fb", "match": "down", "url": gw.cloud_url, "model": "paid",
                                               "price": "10, 20", "on_down": True})
    k = _key(gw, rules={"daily_usd": 0.01})
    c = _as(gw, k)
    r = _chat(c, "down")
    assert r.status_code == 200 and r.headers.get("X-Argos-Fallback") == "fb"
    r = _chat(c, "down")
    assert r.status_code == 429 and "fallback is not allowed" in r.get_json()["error"]["message"]


# ----- retries and cooldown -------------------------------------------------------------------------------------------
def test_Flow_a_failed_local_request_is_tried_again(gw):
    r = _chat(gw.test_client(), "flaky")
    assert r.status_code == 200 and STATE["flaky"] == 2, "503 then 200"
    gw.test_client().put("/api/gateway/retry", json={"retries": 0})
    STATE["flaky"] = 0
    assert _chat(gw.test_client(), "flaky").status_code == 503


def test_Flow_argos_own_refusals_are_not_retried(gw):
    n = len(STATE["engine"])
    r = _chat(gw.test_client(), "guarded")
    assert r.status_code == 502 and len(STATE["engine"]) == n + 1


def test_Flow_a_failing_model_rests_and_goes_straight_to_its_fallback(gw):
    admin = gw.test_client()
    admin.put("/api/gateway/retry", json={"retries": 1, "fails": 2, "window_s": 60, "cooldown_s": 60})
    admin.post("/api/gateway/fallbacks", json={"name": "fb", "match": "down", "url": gw.cloud_url, "model": "free", "on_down": True})
    r = _chat(admin, "down")
    assert r.status_code == 200 and r.headers.get("X-Argos-Fallback") == "fb"
    cooling = admin.get("/api/gateway").get_json()["cooling"]
    assert cooling and cooling[0]["target"] == "model:down"
    n = len(STATE["engine"])
    r = _chat(admin, "down")
    assert r.status_code == 200 and len(STATE["engine"]) == n, "resting: the engine is not asked"
    admin.post("/api/gateway/cooling/reset", json={})
    assert admin.get("/api/gateway").get_json()["cooling"] == []


def test_Flow_a_provider_is_retried_then_rests(gw):
    admin = gw.test_client()
    r = _chat(admin, "cloud/busy")
    assert r.status_code == 200, "429 then 200"
    admin.put("/api/gateway/retry", json={"retries": 1, "fails": 2, "window_s": 60, "cooldown_s": 60})
    r = _chat(admin, "cloud/dead")
    assert r.status_code == 500
    r = _chat(admin, "cloud/dead")
    assert r.status_code == 503 and "cools down" in r.get_json()["error"]["message"]


# ----- API conversion ---------------------------------------------------------------------------------------------------
def test_Flow_responses_api_to_a_cloud_model(gw):
    c = gw.test_client()
    r = c.post("/v1/responses", json={"model": "cloud/free", "instructions": "be brief", "input": "hi"})
    j = r.get_json()
    assert r.status_code == 200 and j["object"] == "response" and j["output_text"] == "from the cloud", j
    assert "a cloud provider" in r.headers["X-Argos-Converted"]
    path, _, sent = STATE["cloud"][-1]
    assert path.endswith("/chat/completions") and sent["messages"][0] == {"role": "system", "content": "be brief"}
    r = c.post("/v1/responses", json={"model": "cloud/free", "input": "hi", "stream": True})
    events = [l[7:] for l in r.data.decode().splitlines() if l.startswith("event: ")]
    assert events[0] == "response.created" and events[-1] == "response.completed" and "response.output_text.delta" in events


def test_Flow_messages_api_falls_back_to_chat_on_a_local_404(gw):
    c = gw.test_client()
    r = c.post("/v1/messages", json={"model": "fast", "max_tokens": 50, "messages": [{"role": "user", "content": "hi"}]},
               headers={"anthropic-version": "2023-06-01"})
    j = r.get_json()
    assert r.status_code == 200 and j["type"] == "message" and j["content"][0]["text"] == "hello", j
    assert [p for p, _ in STATE["engine"][-2:]] == ["/v1/messages", "/v1/chat/completions"]
    r = c.post("/v1/messages", json={"model": "fast", "max_tokens": 50, "stream": True, "messages": [{"role": "user", "content": "hi"}]})
    ev = [l[7:] for l in r.data.decode().splitlines() if l.startswith("event: ")]
    assert ev[0] == "message_start" and ev[-1] == "message_stop"


def test_Flow_conversion_never_and_errors(gw):
    c = gw.test_client()
    c.put("/api/gateway/convert", json={"messages": "never"})
    r = c.post("/v1/messages", json={"model": "fast", "max_tokens": 5, "messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 404
    assert c.put("/api/gateway/convert", json={"responses": "sometimes"}).status_code == 400
    r = c.post("/v1/responses", json={"model": "cloud/dead", "input": "hi"})
    assert r.status_code == 500 and r.get_json()["error"]["message"]
    r = c.post("/v1/responses", data=b"{not json", content_type="application/json")
    assert r.status_code in (400, 404, 502)  # passed through as is (the real engine answers 400)


# ----- traces -----------------------------------------------------------------------------------------------------------
def test_Flow_each_request_is_one_span(gw):
    admin = gw.test_client()
    assert admin.put("/api/gateway/traces", json={"enabled": True, "endpoint": gw.collector}).status_code == 200
    k = _key(gw)
    c = _as(gw, k, "codex")
    _chat(c, "fast")
    _chat(c, "cloud/paid", stream=True)
    _chat(c, "nope-blocked-later")
    _chat(gw.test_client(), "fast", stream=True)  # no key: its tokens are still read
    gw.extensions["traces"].flush()
    spans = STATE["spans"]
    assert len(spans) == 4, [s["name"] for s in spans]
    last = {a["key"]: list(a["value"].values())[0] for a in spans[3]["attributes"]}
    assert str(last.get("gen_ai.usage.input_tokens")) == "1000" and "argos.key" not in last
    attrs = [{a["key"]: list(a["value"].values())[0] for a in s["attributes"]} for s in spans]
    assert attrs[0]["gen_ai.request.model"] == "fast" and attrs[0]["argos.app"] == "codex" and attrs[0]["argos.key"] == "litellm"
    assert str(attrs[1]["gen_ai.usage.input_tokens"]) == "1000" and float(attrs[1]["argos.cost_usd"]) == pytest.approx(0.02)
    assert not any("gen_ai.prompt" in a for a in attrs), "no prompts unless asked"
    st = admin.get("/api/gateway").get_json()["traces"]
    assert st["stats"]["sent"] == 4 and st["enabled"]
    assert admin.post("/api/gateway/traces/test", json={}).status_code == 200
    assert admin.put("/api/gateway/traces", json={"enabled": True, "endpoint": "ftp://x"}).status_code == 400


# ----- MCP tool rules and the log ---------------------------------------------------------------------------------------
def _rpc(c, path, method, params=None):
    return c.post(path, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}).get_json()


def test_Flow_mcp_tool_rules_and_log(gw):
    k = _key(gw, rules={"mcp_tools_deny": ["files__rm"]})
    c = _as(gw, k, "codex")
    names = [t["name"] for t in _rpc(c, "/mcp", "tools/list")["result"]["tools"]]
    assert names == ["files__read"]
    assert [t["name"] for t in _rpc(c, "/mcp/files", "tools/list")["result"]["tools"]] == ["read"]
    bad = _rpc(c, "/mcp", "tools/call", {"name": "files__rm", "arguments": {"path": "/"}})["result"]
    assert bad["isError"] and "may not use the tool rm" in bad["content"][0]["text"]
    assert _rpc(c, "/mcp/files", "tools/call", {"name": "rm"})["result"]["isError"], "the single link too"
    ok = _rpc(c, "/mcp", "tools/call", {"name": "files__read", "arguments": {"path": "/etc"}})["result"]
    assert not ok.get("isError")
    gw.test_client().put(f"/api/security/keys/{k['id']}/apps/codex", json={"mcp_tools_deny": ["files__read"]})
    assert _rpc(c, "/mcp", "tools/list")["result"]["tools"] == [], "the app's own deny"
    assert _rpc(_as(gw, k, "hermes"), "/mcp", "tools/list")["result"]["tools"], "not for the key's other apps"
    log = gw.test_client().get("/api/mcp/calls").get_json()["calls"]
    assert [(x["tool"], x["ok"], x["app"], x["key_name"]) for x in log][:3] == [("read", 1, "codex", "litellm"),
                                                                                ("rm", 0, "codex", "litellm"),
                                                                                ("rm", 0, "codex", "litellm")]
    assert '"/etc"' in log[0]["args"]


# ----- Settings -> General -> Features: Working off skips a feature on every request ---------------------------------
def _off(app, *names):
    from src.routes.security import _TRACE_CACHE
    r = app.test_client().put("/api/settings/enabled", json={n: False for n in names})
    assert r.status_code == 200, r.get_json()
    _TRACE_CACHE.clear()


def test_Flow_features_off_temperature_and_app_rules(gw):
    admin = gw.test_client()
    assert admin.put("/api/temps/sampling", json={"model": "fast", "mode": "force", "temperature": 0.2}).status_code == 200
    _chat(admin, "fast", temperature=1.0)
    assert STATE["bodies"][-1]["temperature"] == 0.2
    k = _key(gw)
    admin.put(f"/api/security/keys/{k['id']}/apps/spam", json={"blocked": True})
    assert _chat(_as(gw, k, "spam"), "fast").status_code == 403
    _off(gw, "temperature", "app_rules")
    _chat(admin, "fast", temperature=1.0)
    assert STATE["bodies"][-1]["temperature"] == 1.0, "off: what the app sent"
    assert _chat(_as(gw, k, "spam"), "fast").status_code == 200, "off: the app's rules are not used"
    rules = admin.get("/api/security").get_json()["keys"][0]["rules"]
    assert rules["apps"]["spam"]["blocked"], "its rules are kept for when it is back on"
    admin.put("/api/settings/enabled", json={"app_rules": True})
    assert _chat(_as(gw, k, "spam"), "fast").status_code == 403


def test_Flow_features_off_budgets_retries_formats(gw):
    k = _key(gw, rules={"daily_usd": 0.015})
    c = _as(gw, k, "hermes")
    _chat(c, "cloud/paid")
    assert _chat(c, "cloud/paid").status_code == 429
    _off(gw, "budgets", "retries", "formats")
    assert _chat(c, "cloud/paid").status_code == 200, "off: never refused for money"
    rows = gw.test_client().get("/api/security/usage").get_json()["rows"]
    assert rows[0]["cost_usd"] == pytest.approx(0.04), "still counted"
    STATE["flaky"] = 0
    assert _chat(gw.test_client(), "flaky").status_code == 503 and STATE["flaky"] == 1, "off: asked once"
    r = gw.test_client().post("/v1/messages", json={"model": "fast", "max_tokens": 5, "messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 404 and "X-Argos-Converted" not in r.headers, "off: passed through as is"


def test_Flow_features_off_traces_and_mcp_tools(gw):
    admin = gw.test_client()
    admin.put("/api/gateway/traces", json={"enabled": True, "endpoint": gw.collector})
    k = _key(gw, rules={"mcp_tools_deny": ["files__rm"]})
    c = _as(gw, k, "codex")
    assert [t["name"] for t in _rpc(c, "/mcp", "tools/list")["result"]["tools"]] == ["files__read"]
    _off(gw, "traces", "mcp_tools")
    _chat(c, "fast")
    gw.extensions["traces"].flush()
    assert STATE["spans"] == [], "off: nothing sent"
    assert admin.get("/api/gateway").get_json()["traces"]["enabled"], "its own setting is kept"
    assert sorted(t["name"] for t in _rpc(c, "/mcp", "tools/list")["result"]["tools"]) == ["files__read", "files__rm"]
    assert not _rpc(c, "/mcp", "tools/call", {"name": "files__rm", "arguments": {}})["result"].get("isError")
    assert admin.get("/api/mcp/calls").get_json()["calls"] == [], "off: no call log"
