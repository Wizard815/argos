"""Schedule -> Live and the Usage GPU toggle show the engine's newest requests, as llama-swap's Activity page did."""
from src import telemetry
from src.routes import schedule

ROWS = {"data": [
    {"id": 7, "timestamp": "2026-10-26T10:00:02Z", "model": "embed", "req_path": "/v1/embeddings", "src": "ip:127.0.0.1",
     "resp_status_code": 200, "duration_ms": 41, "tokens": {"input_tokens": 37, "output_tokens": 0, "prompt_per_second": -1, "tokens_per_second": -1}},
    {"id": 6, "timestamp": "2026-10-26T10:00:01Z", "model": "chat", "req_path": "/completion", "src": "ip:127.0.0.1",
     "resp_status_code": 200, "duration_ms": 2000, "tokens": {"input_tokens": 812, "output_tokens": 64, "prompt_per_second": 2030, "tokens_per_second": 40}},
]}


def fake_engine(monkeypatch, pool=None):
    seen = []

    def get_json(url, *a, **k):
        seen.append(url)
        return ROWS
    monkeypatch.setattr(telemetry, "_get_json", get_json)
    monkeypatch.setattr(schedule.engine_api, "pool_state", lambda url, timeout=3.0: pool or {
        "enabled": True, "models": [{"id": "chat", "in_flight": 2}, {"id": "embed", "in_flight": 0}], "cards": [], "queue": [], "swaps": []})
    return seen


def test_RequestsFeed_live_carries_the_newest_requests(app, client, monkeypatch):
    client.put("/api/settings/integrations", json={"engine_url": "http://engine.test"})
    seen = fake_engine(monkeypatch)
    j = client.get("/api/schedule/live").get_json()
    assert [(r["model"], r["path"], r["input_tokens"], r["output_tokens"]) for r in j["requests"]] == \
        [("embed", "/v1/embeddings", 37, 0), ("chat", "/completion", 812, 64)], "embeddings and benchmarks too, newest first"
    assert j["requests"][0]["tokens_per_second"] is None, "-1 means unknown, not a speed"
    assert any("order=desc" in u for u in seen), "read straight from the engine, newest first"


def test_RequestsFeed_recent_endpoint_says_what_is_running(app, client, monkeypatch):
    client.put("/api/settings/integrations", json={"engine_url": "http://engine.test"})
    fake_engine(monkeypatch)
    j = client.get("/api/requests/recent?limit=5").get_json()
    assert j["source"] == "engine" and len(j["requests"]) == 2 and j["in_flight"] == {"chat": 2}


def test_RequestsFeed_without_the_engine_shows_what_was_synced(app, client):
    app.extensions["pb"].store.add_requests([{"source_id": 1, "ts": 1.0, "model": "m", "path": "/v1/chat/completions",
                                              "input_tokens": 5, "output_tokens": 2}])
    j = client.get("/api/requests/recent").get_json()
    assert j["source"] == "stored" and j["requests"][0]["model"] == "m"
