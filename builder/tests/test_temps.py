"""Profiles -> Temperature: sampling per model applied to requests, and temperature sweeps (temps.py)."""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from src import temps as T

SEEN = []


def test_Temps_clean_and_bounds():
    assert T.clean({"mode": "force", "temperature": "0.2", "top_k": "40"}) == {"mode": "force", "temperature": 0.2, "top_k": 40}
    assert T.clean({}) == {"mode": "default"}
    for bad in ({"mode": "sometimes"}, {"temperature": 3}, {"temperature": "nan"}, {"top_p": 1.5}, {"temperature": "1e400"},
                {"top_k": -1}, {"min_p": "x"}):
        with pytest.raises(ValueError):
            T.clean(bad)


def test_Temps_apply_default_and_force():
    rule = {"mode": "default", "temperature": 0.2, "min_p": 0.05}
    b = {"model": "m", "messages": []}
    assert T.apply("/v1/chat/completions", b, rule) == ["temperature", "min_p"] and b["temperature"] == 0.2
    b = {"temperature": 1.0}
    assert T.apply("/v1/chat/completions", b, rule) == ["min_p"] and b["temperature"] == 1.0, "the app's own is kept"
    b = {"temperature": 1.0}
    assert T.apply("/v1/chat/completions", b, {**rule, "mode": "force"}) == ["temperature", "min_p"] and b["temperature"] == 0.2
    b = {}
    assert T.apply("/v1/messages", b, rule) == ["temperature"] and "min_p" not in b, "Anthropic's API has no min_p"
    assert T.apply("/v1/embeddings", {}, rule) == [] and T.apply("/v1/chat/completions", {}, None) == []
    assert T.apply("/v1/responses", {}, rule) == ["temperature", "min_p"]


def test_Temps_rule_for_label_or_full_id():
    rules = {"coder": {"mode": "force", "temperature": 0.2}}
    assert T.rule_for("coder", rules) and T.rule_for("qwen3-coder-30b--coder", rules)
    assert T.rule_for("other", rules) is None and T.rule_for("", rules) is None


@pytest.mark.parametrize("answer,right,ok", [
    ("12 * 2/3 ... ANSWER: 12", "12", True),
    ("Answer: $12.00", "12", True),
    ("ANSWER: 12.0", "12", True),
    ("ANSWER: 120", "12", False),
    ("so\n**ANSWER:** Saturday.", "saturday", True),
    ("<think>maybe 391</think>\nANSWER: 391", "391", True),
    ("The answer is 1-4-7", "1-4-7", True),
    ("ANSWER: 38 cm", "38", True),
    ("", "1", False),
    ("ANSWER: desserts", "desserts", True),
    ("answer: 4\nanswer: 5", "5", True),
])
def test_Temps_grading(answer, right, ok):
    assert T.graded(answer, right) is ok


def test_Temps_variety():
    same = ["the keeper found a bottle"] * 4
    assert T.variety(same) == 0.0
    diff = ["the keeper found a bottle on the rocks", "a whale sang to her one night", "storms bring ghosts ashore", "x"]
    assert T.variety(diff) > 0.9
    assert T.variety(["one"]) == 0.0 and T.variety([]) == 0.0


def test_Temps_best_prefers_right_then_varied():
    pts = [{"temp": 0.0, "correct": 15, "total": 16, "variety": 0.1}, {"temp": 0.4, "correct": 15, "total": 16, "variety": 0.5},
           {"temp": 0.6, "correct": 14, "total": 16, "variety": 0.7}, {"temp": 1.0, "correct": 9, "total": 16, "variety": 0.9}]
    assert T.best(pts) == 0.4, "near the top with room before the drop at 1.0 (0.6 is next to it): the more varied"
    hy4 = [{"temp": t, "correct": c, "total": 16} for t, c in ((0, 16), (0.4, 16), (0.8, 16), (1.2, 16), (1.6, 8), (2.0, 0))]
    assert T.best(hy4) == 0.8, "1.2 is right before the cliff"
    assert T.best([{"temp": 0.5, "correct": 3, "total": 4}]) == 0.5, "one point: that one"
    assert T.best([]) is None
    assert T.best([{"temp": 0.2, "correct": 87, "total": 92}, {"temp": 0.6, "correct": 85, "total": 92}, {"temp": 1.0, "correct": 52, "total": 92}]) == 0.2


def test_Temps_manual_results():
    r = T.clean_manual({"model": "gemma", "label": "code", "points": [{"temp": 1.0, "passed": 52, "total": 92, "broken": 2},
                                                                      {"temp": 0.2, "passed": 87, "total": 92}]})
    assert [p["temp"] for p in r["points"]] == [0.2, 1.0] and r["best"] == 0.2 and r["points"][1]["broken"] == 2
    for bad in ({"model": "", "points": [{"temp": 0, "passed": 1, "total": 1}]}, {"model": "m", "points": []},
                {"model": "m", "points": [{"temp": 3, "passed": 1, "total": 1}]}, {"model": "m", "points": [{"temp": 0, "passed": 5, "total": 4}]},
                {"model": "m", "points": [{"temp": 0}]}):
        with pytest.raises(ValueError):
            T.clean_manual(bad)


class Store:
    def __init__(self):
        self.s = {}

    def get_settings(self):
        return self.s

    def set_setting(self, k, v):
        self.s[k] = v


def _fake_ask(engine, model, prompt, temp, max_tokens, thinking):
    """Right below 0.7, then wrong more often the hotter; stories differ more the hotter."""
    if "ANSWER" in prompt:
        right = dict(T.QUESTIONS)[prompt[len(T.ASK):]]
        wrong = temp >= 0.7 and hash((prompt, temp)) % 10 < int(temp * 6)
        return f"ANSWER: {'nope' if wrong else right}", 5
    if temp < 0.3:
        return "the keeper found a bottle with a map", 20
    import random
    return " ".join(random.choice(["sea", "light", "ghost", "map", "storm", "bird", "door", "song"]) for _ in range(12)), 20


def test_Temps_a_sweep_measures_each_temperature():
    st = Store()
    s = T.Sweep(st, "http://x", "m", [0.0, 0.6, 1.2], ask=_fake_ask)
    s.run()
    v = s.view()
    assert v["state"] == "done" and v["done"] == v["total"] == 3 * (16 + 4)
    acc = {p["temp"]: p["accuracy"] for p in v["points"]}
    var = {p["temp"]: p["variety"] for p in v["points"]}
    assert acc[0.0] == 1.0 and acc[1.2] < 1.0 and var[0.0] == 0.0 and var[1.2] > 0.5
    assert T.results(st)[0]["points"] == v["points"] and v["best"] in (0.0, 0.6)


def test_Temps_a_sweep_of_a_model_that_is_not_there_fails_with_why():
    def down(*a):
        raise RuntimeError("404: no router for requested model")
    s = T.Sweep(Store(), "http://x", "ghost", [0.0, 0.5], ask=down)
    s.run()
    assert s.state == "failed" and "no router" in s.error and len(s.points) == 1


def test_Temps_a_sweep_can_be_stopped():
    def slow(*a):
        time.sleep(0.05)
        return "ANSWER: 1", 1
    st = Store()
    s = T.Sweep(st, "http://x", "m", [0.0, 0.5, 1.0], ask=slow)
    th = threading.Thread(target=s.run)
    th.start()
    time.sleep(0.3)
    s.stop.set()
    th.join(3)
    assert s.state == "stopped" and s.done_n < s.total_n


# ----- through the builder ---------------------------------------------------------------------------------------
class Engine(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        self._send({"running": [{"model": "fast", "state": "ready"}]} if self.path.startswith("/running") else {"data": []})

    def _send(self, obj):
        d = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(d)))
        self.end_headers()
        self.wfile.write(d)

    def do_POST(self):
        b = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        SEEN.append(b)
        prompt = (b.get("messages") or [{}])[-1].get("content") or ""
        text = "ANSWER: " + dict(T.QUESTIONS).get(prompt[len(T.ASK):], "") if prompt.startswith(T.ASK) else f"story {b.get('temperature')}"
        self._send({"choices": [{"message": {"role": "assistant", "content": text}}], "usage": {"completion_tokens": 3}})


@pytest.fixture
def gw(tmp_path):
    from src.app import create_app
    eng = ThreadingHTTPServer(("127.0.0.1", 0), Engine)
    threading.Thread(target=eng.serve_forever, daemon=True).start()
    app = create_app({"DB": str(tmp_path / "t.db"), "RUNS_DIR": str(tmp_path / "runs"), "MODELS_DIR": str(tmp_path / "m"),
                      "ENGINE_URL": f"http://127.0.0.1:{eng.server_address[1]}", "HF_ENDPOINT": "http://127.0.0.1:9",
                      "TELEMETRY": False, "BACKGROUND": False, "UI_PASSWORD": ""})
    app.extensions["pb"].store.set_setting("integrations", {**app.extensions["pb"].settings.get("integrations"),
                                                            "engine_url": f"http://127.0.0.1:{eng.server_address[1]}"})
    SEEN.clear()
    yield app
    app.extensions["pb"].store.close()
    eng.shutdown()


def test_Temps_sampling_is_applied_to_requests_without_a_reload(gw):
    c = gw.test_client()
    assert c.put("/api/temps/sampling", json={"model": "fast", "mode": "force", "temperature": 0.2}).status_code == 200
    r = c.post("/v1/chat/completions", json={"model": "fast", "temperature": 1.0, "messages": []})
    r.get_data()
    assert SEEN[-1]["temperature"] == 0.2 and r.headers.get("X-Argos-Sampling") == "temperature=0.2"
    c.put("/api/temps/sampling", json={"model": "fast", "mode": "default", "temperature": 0.3})
    c.post("/v1/chat/completions", json={"model": "fast", "temperature": 0.9, "messages": []}).get_data()
    assert SEEN[-1]["temperature"] == 0.9, "default: the app's own wins"
    c.post("/v1/chat/completions", json={"model": "fast", "messages": []}).get_data()
    assert SEEN[-1]["temperature"] == 0.3
    c.put("/api/temps/sampling", json={"model": "fast", "clear": True})
    c.post("/v1/chat/completions", json={"model": "fast", "messages": []}).get_data()
    assert "temperature" not in SEEN[-1]
    assert c.put("/api/temps/sampling", json={"model": "fast", "temperature": 9}).status_code == 400
    assert c.put("/api/temps/sampling", json={"temperature": 0.2}).status_code == 400


def test_Temps_sweep_through_the_routes(gw):
    c = gw.test_client()
    assert c.post("/api/temps/sweep", json={"model": "fast", "temps": [5]}).status_code == 400
    assert c.post("/api/temps/sweep", json={"model": "fast", "temps": [0, 0.5], "stories": 2}).status_code == 200
    assert c.post("/api/temps/sweep", json={"model": "fast"}).status_code in (200, 409)
    for _ in range(100):
        sw = c.get("/api/temps").get_json()["sweeps"]
        if sw and sw[0]["state"] != "running":
            break
        time.sleep(0.05)
    assert sw[0]["state"] == "done" and [p["accuracy"] for p in sw[0]["points"]] == [1.0, 1.0], sw[0]
    assert {b["temperature"] for b in SEEN} == {0.0, 0.5}, "each question asked at the sweep's own temperatures"
    assert all(b["chat_template_kwargs"] == {"enable_thinking": False} for b in SEEN)
    assert c.post("/api/temps/results", json={"model": "fast", "label": "code", "points": [{"temp": 0.2, "passed": 87, "total": 92}]}).status_code == 200
    res = T.results(gw.extensions["pb"].store, "fast")
    assert [r["kind"] for r in res] == ["sweep", "manual"]
    assert c.delete(f"/api/temps/results/{res[1]['id']}").status_code == 200
    assert len(T.results(gw.extensions["pb"].store, "fast")) == 1


def test_Temps_answers_cut_off_while_thinking_are_counted_apart():
    def thinker(engine, model, prompt, temp, max_tokens, thinking):
        if temp > 1 or "story" in prompt:
            raise T.TokensRanOut("thinking")
        return "ANSWER: " + dict(T.QUESTIONS)[prompt[len(T.ASK):]], 10
    s = T.Sweep(Store(), "http://x", "m", [0.5, 1.5], ask=thinker)
    s.run()
    a, b = s.points
    assert s.state == "done" and a["correct"] == 16 and a["cut"] == 4 and a["variety"] is None, "no stories: not measured"
    assert b["correct"] == 0 and b["cut"] == 20 and b["errors"] == 0
