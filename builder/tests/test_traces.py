"""Trace spans sent as OTLP/HTTP JSON to an observability tool (traces.py)."""
import base64
import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from src import traces  # module import: a bare `test_send` would be collected as a test

SECRET = "my secret prompt text"


class Collector:
    """A tiny OTLP collector that records each POST."""

    def __init__(self, status=200):
        self.status = status
        self.posts = []  # (path, headers, json body)
        outer = self

        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                outer.posts.append((self.path, dict(self.headers), json.loads(body)))
                self.send_response(outer.status)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"error":"boom"}' if outer.status >= 400 else b"{}")

            def log_message(self, *a):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, args=(0.02,), daemon=True).start()

    def spans(self):
        return [s for _, _, b in self.posts for s in b["resourceSpans"][0]["scopeSpans"][0]["spans"]]

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture(autouse=True)
def _no_proxy(monkeypatch):
    # The local collector must be reached directly, whatever proxy the machine has.
    for k in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.delenv(k, raising=False)


@pytest.fixture
def collector():
    c = Collector()
    yield c
    c.close()


def settings(url, **kw):
    return traces.clean({"enabled": True, "endpoint": url, **kw})


def exporter(st, **kw):
    box = {"s": st}
    return traces.Exporter(lambda: box["s"], flush_s=3600, **kw), box


def a_span(**kw):
    args = dict(name="chat qwen", start=1700000000.25, end=1700000001.5, model="qwen", path="/v1/chat/completions",
                status=200, key="team-a", app="notes", input_tokens=12, output_tokens=34, cost_usd=0.0021,
                request_body=json.dumps({"messages": [{"role": "user", "content": SECRET}]}).encode(),
                response_text="the answer")
    args.update(kw)
    return traces.span(**args)


def attrs(s):
    return {a["key"]: a["value"] for a in s["attributes"]}


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_Traces_payload_shape(collector):
    ex, _ = exporter(settings(collector.url, service_name="gw"))
    ex.record(a_span(trace_id="AB" * 16))
    ex.flush()
    ex.close()
    path, headers, body = collector.posts[0]
    assert path == "/v1/traces" and headers["Content-Type"] == "application/json"
    rs = body["resourceSpans"][0]
    assert rs["resource"]["attributes"] == [{"key": "service.name", "value": {"stringValue": "gw"}}]
    assert rs["scopeSpans"][0]["scope"] == {"name": "argos"}
    s = rs["scopeSpans"][0]["spans"][0]
    assert s["traceId"] == "ab" * 16 and len(s["spanId"]) == 16
    int(s["spanId"], 16)
    assert s["startTimeUnixNano"] == "1700000000250000000" and s["endTimeUnixNano"] == "1700000001500000000"
    assert s["kind"] == 3 and s["status"] == {"code": 1}
    a = attrs(s)
    assert a["gen_ai.system"] == {"stringValue": "argos"}
    assert a["gen_ai.operation.name"] == {"stringValue": "chat"}
    assert a["gen_ai.request.model"] == {"stringValue": "qwen"}
    assert a["gen_ai.usage.input_tokens"] == {"intValue": "12"}
    assert a["gen_ai.usage.output_tokens"] == {"intValue": "34"}
    assert a["http.response.status_code"] == {"intValue": "200"}
    assert a["url.path"] == {"stringValue": "/v1/chat/completions"}
    assert a["argos.key"] == {"stringValue": "team-a"} and a["argos.app"] == {"stringValue": "notes"}
    assert a["argos.stream"] == {"boolValue": False}
    assert a["argos.cost_usd"] == {"doubleValue": 0.0021}
    assert "argos.fallback" not in a, "empty values are left out"
    assert ex.stats()["sent"] == 1 and ex.stats()["queued"] == 0 and ex.stats()["last_sent_at"] > 0


def test_Traces_span_details():
    s = traces.span(name="x", start=1.0, end=2.0, model="m", path="/v1/embeddings", status=502, provider="openai",
                    error="upstream down")
    assert len(s["traceId"]) == 32 and s["status"] == {"code": 2, "message": "upstream down"}
    assert s["attributes"]["gen_ai.system"] == "openai"
    assert s["attributes"]["gen_ai.operation.name"] == "embeddings"
    assert traces.span(name="x", start=1, end=2, model="m", path="/v1/completions",
                       status=200)["attributes"]["gen_ai.operation.name"] == "text_completion"
    assert traces.span(name="x", start=1, end=2, model="m", path="/v1/messages",
                       status=429)["status"]["code"] == 2
    assert traces.span(name="x", start=1, end=2, model="m", path="/v1/responses", status=200,
                       trace_id="nothex")["traceId"] != "nothex"


def test_Traces_content_off_never_leaks_prompt(collector):
    ex, _ = exporter(settings(collector.url))
    ex.record(a_span())
    ex.flush()
    raw = json.dumps(collector.posts[0][2])
    assert SECRET not in raw and "the answer" not in raw
    assert "gen_ai.prompt" not in raw and "langfuse.observation.input" not in raw
    assert traces.test_send(settings(collector.url)) == ""
    assert "test" not in attrs(collector.spans()[-1]).get("gen_ai.prompt", {}).get("stringValue", "")
    assert "gen_ai.prompt" not in attrs(collector.spans()[-1])


def test_Traces_content_on_includes_it_cut(collector):
    ex, _ = exporter(settings(collector.url, content=True))
    ex.record(a_span())
    ex.record(a_span(request_body=b"x" * 100_000, response_text="é" * 40_000))
    ex.flush()
    small, big = collector.spans()
    a = attrs(small)
    assert SECRET in a["gen_ai.prompt"]["stringValue"]
    assert a["gen_ai.completion"] == {"stringValue": "the answer"}
    assert a["langfuse.observation.input"] == a["gen_ai.prompt"]
    assert a["langfuse.observation.output"] == a["gen_ai.completion"]
    b = attrs(big)
    for k in ("gen_ai.prompt", "gen_ai.completion"):
        v = b[k]["stringValue"]
        assert v.endswith("(cut)") and len(v.encode()) <= traces.CONTENT_MAX + 16
    assert "�" not in b["gen_ai.completion"]["stringValue"], "no split UTF-8 character"


def test_Traces_langfuse_settings_build_auth_and_endpoint(collector):
    st = traces.clean({"enabled": True, "kind": "langfuse", "endpoint": "https://lf.example.com/",
                       "public_key": "pk-lf-1", "secret_key": "sk-lf-2"})
    assert st["endpoint"] == "https://lf.example.com/api/public/otel"
    assert traces.traces_url(st) == "https://lf.example.com/api/public/otel/v1/traces"
    assert st["headers"]["Authorization"] == "Basic " + base64.b64encode(b"pk-lf-1:sk-lf-2").decode()
    assert traces.clean({"kind": "langfuse"})["endpoint"] == "https://cloud.langfuse.com/api/public/otel"
    # A full OTLP path (a self-hosted Langfuse behind a prefix) is kept as given.
    st2 = traces.clean({"kind": "langfuse", "endpoint": collector.url + "/lf/api/public/otel",
                        "public_key": "p", "secret_key": "s", "enabled": True})
    assert st2["endpoint"] == collector.url + "/lf/api/public/otel"
    # The secret survives a save from the masked form.
    again = traces.clean(traces.public(st2), old=st2)
    assert again["secret_key"] == "s" and again["headers"]["Authorization"] == st2["headers"]["Authorization"]
    assert traces.test_send(again) == ""
    path, headers, _ = collector.posts[0]
    assert path == "/lf/api/public/otel/v1/traces"
    assert headers["Authorization"] == "Basic " + base64.b64encode(b"p:s").decode()
    with pytest.raises(ValueError, match="public key and the secret key"):
        traces.clean({"enabled": True, "kind": "langfuse", "public_key": "p"})


def test_Traces_header_masking_and_keeping():
    old = traces.clean({"endpoint": "https://otel.example.com", "headers": "X-Api-Key: abc\nX-Org: one"})
    assert old["headers"] == {"X-Api-Key": "abc", "X-Org": "one"}
    pub = traces.public(old)
    assert pub["headers"] == {"X-Api-Key": "••••", "X-Org": "••••"}
    assert old["headers"]["X-Api-Key"] == "abc", "public() does not change the settings"
    new = traces.clean({**pub, "headers": {"X-Api-Key": "••••", "x-org": "", "X-New": "n", "X-Gone": "••••"}},
                       old=old)
    assert new["headers"] == {"X-Api-Key": "abc", "x-org": "one", "X-New": "n"}
    assert traces.clean({**pub, "headers": {"X-Api-Key": "def"}}, old=old)["headers"] == {"X-Api-Key": "def"}
    assert traces.public(traces.clean({"kind": "langfuse", "public_key": "p", "secret_key": "s"}))["secret_key"] == "••••"


def test_Traces_clean_rejects_bad_input():
    for bad in ("ftp://x", "localhost:4318", "http://", "http://a b"):
        with pytest.raises(ValueError, match="http:// or https://"):
            traces.clean({"endpoint": bad})
    for bad in (-0.1, 1.5, "lots"):
        with pytest.raises(ValueError, match="Sample"):
            traces.clean({"endpoint": "http://x", "sample": bad})
    with pytest.raises(ValueError, match="endpoint"):
        traces.clean({"enabled": True})
    with pytest.raises(ValueError, match="line break"):
        traces.clean({"endpoint": "http://x", "headers": {"X-A": "a\r\nX-B: b"}})
    d = traces.clean({})
    assert d == {"enabled": False, "kind": "otlp", "endpoint": "", "headers": {}, "service_name": "argos",
                 "content": False, "sample": 1.0}
    assert traces.traces_url({"endpoint": "http://c:4318/v1/traces"}) == "http://c:4318/v1/traces"
    assert traces.traces_url({"endpoint": "http://c:4318/"}) == "http://c:4318/v1/traces"


def test_Traces_sampling_zero_and_disabled_drop(collector):
    ex, box = exporter(settings(collector.url, sample=0))
    for _ in range(5):
        ex.record(a_span())
    box["s"] = traces.clean({"endpoint": collector.url})  # disabled at runtime
    ex.record(a_span())
    ex.flush()
    assert collector.posts == []
    assert ex.stats()["dropped"] == 6 and ex.stats()["queued"] == 0


def test_Traces_queue_overflow_counts_drops(collector):
    ex, _ = exporter(settings(collector.url), max_queue=3)
    for _ in range(5):
        ex.record(a_span())
    assert ex.stats()["queued"] == 3 and ex.stats()["dropped"] == 2
    ex.flush()
    assert len(collector.spans()) == 3


def test_Traces_collector_500_counts_failed_without_raising():
    c = Collector(status=500)
    try:
        ex, _ = exporter(settings(c.url))
        ex.record(a_span())
        ex.flush()  # first failure: kept for one retry
        s = ex.stats()
        assert s["failed"] == 0 and s["queued"] == 1 and "HTTP 500" in s["last_error"]
        ex.flush()  # the retry fails too: given up
        s = ex.stats()
        assert s["failed"] == 1 and s["queued"] == 0 and s["sent"] == 0
        assert len(c.posts) == 2
        c.status = 200
        ex.flush()
        assert len(c.posts) == 2, "a given-up span is not sent again"
    finally:
        c.close()


def test_Traces_retry_succeeds_later(collector):
    collector.status = 503
    ex, _ = exporter(settings(collector.url))
    ex.record(a_span())
    ex.flush()
    collector.status = 200
    ex.flush()
    assert ex.stats()["sent"] == 1 and ex.stats()["failed"] == 0


def test_Traces_down_collector_does_not_block_record():
    ex, _ = exporter(settings(f"http://127.0.0.1:{free_port()}"))
    t = time.monotonic()
    for _ in range(500):
        ex.record(a_span())
    assert time.monotonic() - t < 0.5
    ex.flush()  # connection refused: reported, not raised
    assert "Cannot reach" in ex.stats()["last_error"]
    ex.close()


def test_Traces_background_thread_sends(collector):
    ex = traces.Exporter(lambda: settings(collector.url), flush_s=0.05)
    ex.record(a_span())
    deadline = time.monotonic() + 5
    while not collector.posts and time.monotonic() < deadline:
        time.sleep(0.02)
    ex.close()
    assert len(collector.spans()) == 1


def test_Traces_batching(collector):
    ex, _ = exporter(settings(collector.url), max_batch=200)
    for i in range(450):
        ex.record(a_span(model=f"m{i}"))
    ex.flush()
    assert [len(b["resourceSpans"][0]["scopeSpans"][0]["spans"]) for _, _, b in collector.posts] == [200, 200, 50]
    assert ex.stats()["sent"] == 450


def test_Traces_test_send_success_and_failure(collector):
    st = traces.clean({"endpoint": collector.url, "headers": {"X-Api-Key": "k"}})
    assert traces.test_send(st) == "", "works before traces are turned on"
    assert collector.posts[0][1]["X-Api-Key"] == "k"
    assert collector.spans()[0]["name"] == "argos test span"
    collector.status = 401
    err = traces.test_send(st)
    assert "HTTP 401" in err and "boom" in err
    err = traces.test_send(traces.clean({"endpoint": f"http://127.0.0.1:{free_port()}"}))
    assert err.startswith("Cannot reach http://127.0.0.1:")
    assert traces.test_send(traces.clean({})) == "Set the endpoint URL first"
