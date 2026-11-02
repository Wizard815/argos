"""The loop guard: the same request from one client, over and over."""
import time

from src import loop_guard
from src.integrations import engine_api


def _rows(n, src="ip:192.168.20.7", model="coder", tin=19, tout=24, start=1000.0, step=6.4, path="/v1/chat/completions"):
    return [{"src": src, "model": model, "path": path, "input_tokens": tin, "output_tokens": tout, "ts": start + i * step}
            for i in range(n)]


def test_LoopGuard_finds_a_client_repeating_the_same_request():
    work = [{"src": "ip:192.168.20.7", "model": "coder", "path": "/v1/chat/completions", "input_tokens": 400 + i * 97,
             "output_tokens": 50 + (i * 37) % 400, "ts": 900.0 + i} for i in range(40)]
    loops = loop_guard.find_loops(work + _rows(30), repeats=25)
    assert [(lp["src"], lp["in"], lp["out"], lp["count"]) for lp in loops] == [("ip:192.168.20.7", 19, 24, 30)]
    assert "30 requests in a row of 19 tokens in / 24 out" in loop_guard.describe(loops[0])


def test_LoopGuard_leaves_real_work_embeddings_and_old_runs_alone():
    work = [{"src": "ip:a", "model": "m", "input_tokens": 100 * i, "output_tokens": 30 + i, "ts": float(i)} for i in range(60)]
    assert loop_guard.find_loops(work) == [], "sizes change: real work"
    assert loop_guard.find_loops(_rows(60, path="/v1/embeddings", tout=0)) == [], "the same text embedded again is normal"
    assert loop_guard.find_loops(_rows(24)) == [], "fewer than the repeats"
    assert loop_guard.find_loops(_rows(30), now=1000 + 30 * 6.4 + 600) == [], "it stopped ten minutes ago"
    tiny = _rows(30, tin=67, tout=22)
    for i, r in enumerate(tiny):
        r["input_tokens"] += i % 3  # 67-69: within the tolerance
    assert loop_guard.find_loops(tiny)[0]["count"] == 30


def test_LoopGuard_stops_the_client_then_lets_it_run(app, client, monkeypatch):
    pb = app.extensions["pb"]
    client.put("/api/settings/integrations", json={"engine_url": "http://engine"})
    calls = []
    monkeypatch.setattr(engine_api, "guard_block", lambda url, src, model, reason, timeout=5.0: calls.append(("block", src, model, reason)) or {})
    monkeypatch.setattr(engine_api, "guard_lift", lambda url, src, model, timeout=5.0: calls.append(("lift", src, model)))
    monkeypatch.setattr(engine_api, "guard_blocks", lambda url, timeout=3.0: [{"src": c[1], "model": c[2]} for c in calls if c[0] == "block"][-1:] if calls[-1:] and calls[-1][0] == "block" else [])
    rows = _rows(30, start=time.time() - 200)
    pb.store.add_requests([{**r, "source_id": i + 1} for i, r in enumerate(rows)])
    state = pb.schedule_state.setdefault("loops", {})
    now = rows[-1]["ts"] + 5
    monkeypatch.setattr(pb.store, "list_requests", lambda since=None, limit=0, model=None: rows)
    loop_guard.check(pb.store, pb.settings, "http://engine", state, pb.event, now)
    assert calls[0][:3] == ("block", "192.168.20.7", "coder") and "19 tokens in / 24 out" in calls[0][3]
    g = client.get("/api/guard").get_json()
    assert g["loops"][0]["status"] == "stopped" and g["settings"]["action"] == "stop"

    assert client.post("/api/guard/allow", json={"src": "ip:192.168.20.7", "model": "coder"}).status_code == 200
    assert calls[-1] == ("lift", "192.168.20.7", "coder")
    loop_guard.check(pb.store, pb.settings, "http://engine", state, pb.event, now + 6)
    assert sum(1 for c in calls if c[0] == "block") == 1, "let run: the same loop is not stopped again"

    client.put("/api/settings/guard", json={"action": "warn"})
    state.clear()
    loop_guard.check(pb.store, pb.settings, "http://engine", state, pb.event, now + 12)
    assert sum(1 for c in calls if c[0] == "block") == 1 and state["ip:192.168.20.7|coder"]["status"] == "flagged", "warn only"
