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
    assert "30 requests of 19 tokens in / 24 out" in loop_guard.describe(loops[0])


def test_LoopGuard_catches_a_loop_whose_prompt_moves_beside_another_chat():
    """The second loop seen: 70-83 tokens in (a counter in the tool result moves) and exactly 61 out every ~6.6 s,
    while the same client's other chat writes long answers on the model's other slot."""
    ins = [78, 80, 77, 72, 71, 74, 70, 77, 76, 81, 75, 78, 77, 70, 81, 74, 77, 83, 74, 77, 81, 72, 70, 78, 80, 83, 76]
    rows = [{"src": "ip:192.168.20.7", "model": "coder", "path": "/v1/chat/completions", "input_tokens": n, "output_tokens": 61,
             "ts": 1000.0 + i * 6.6} for i, n in enumerate(ins)]
    rows += [{"src": "ip:192.168.20.7", "model": "coder", "path": "/v1/chat/completions", "input_tokens": 9000 + i, "output_tokens": 1600 + 37 * i,
              "ts": 1003.0 + i * 60} for i in range(3)]  # the real task on slot 0, finishing now and then
    (lp,) = loop_guard.find_loops(rows, repeats=25)
    assert lp["out"] == 61 and lp["count"] == len(ins) and (lp["in_low"], lp["in_high"]) == (70, 83)
    assert "70-83 tokens in / 61 out" in loop_guard.describe(lp)


def test_LoopGuard_an_agent_doing_real_work_is_not_a_loop():
    """Tool calls with small results look alike in what they read; what they write changes, so they are not."""
    rows = [{"src": "ip:a", "model": "m", "input_tokens": 70 + (i * 7) % 15, "output_tokens": 40 + (i * 53) % 300, "ts": float(i)}
            for i in range(60)]
    assert loop_guard.find_loops(rows) == []


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
    monkeypatch.setattr(engine_api, "guard_block", lambda url, src, model, reason, timeout=5.0, client="": calls.append(("block", src, model, reason, client)) or {})
    monkeypatch.setattr(engine_api, "guard_lift", lambda url, src, model, timeout=5.0, client="": calls.append(("lift", src, model)))
    monkeypatch.setattr(engine_api, "guard_blocks", lambda url, timeout=3.0: [{"src": c[1], "model": c[2], "client": c[4]} for c in calls if c[0] == "block"][-1:] if calls[-1:] and calls[-1][0] == "block" else [])
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
    assert sum(1 for c in calls if c[0] == "block") == 1 and state["ip:192.168.20.7||coder"]["status"] == "flagged", "warn only"


def test_LoopGuard_two_apps_on_one_address_are_told_apart(app, client, monkeypatch):
    """Hermes (OpenAI/Python) and Codex (node) on one machine: only the one repeating is flagged and stopped."""
    pb = app.extensions["pb"]
    now = time.time()
    rows = [{**r, "client": "node"} for r in _rows(30, tin=77, tout=61, start=now - 200)]
    rows += [{"src": "ip:192.168.20.7", "client": "OpenAI/Python 2.24.0", "model": "coder", "path": "/v1/chat/completions",
              "input_tokens": 61, "output_tokens": 61, "ts": now - 190 + i * 6.4} for i in range(30)]  # same sizes by chance
    rows[-30:] = [{**r, "output_tokens": 30 + 47 * i} for i, r in enumerate(rows[-30:])]  # but Hermes's answers vary
    loops = loop_guard.find_loops(rows)
    assert [(lp["client"], lp["out"]) for lp in loops] == [("node", 61)]
    calls = []
    monkeypatch.setattr(engine_api, "guard_block", lambda url, src, model, reason, timeout=5.0, client="": calls.append(client) or {})
    monkeypatch.setattr(pb.store, "list_requests", lambda since=None, limit=0, model=None: rows)
    loop_guard.check(pb.store, pb.settings, "http://engine", {}, pb.event, now)
    assert calls == ["node"], "the block names the app, so Hermes keeps running"
