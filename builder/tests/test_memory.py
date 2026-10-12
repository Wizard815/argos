"""The assistant's memory: records from every table, embeddings, and search by meaning and by words."""
import json
import math
import time
from http.server import BaseHTTPRequestHandler

from .fakes import serve

# a tiny "embedding model": words in the same group point the same way, so related words are close
GROUPS = [{"crash", "crashed", "failed", "oom", "exited"},
          {"fast", "speed", "quick", "throughput", "t/s"},
          {"note", "idea", "remember", "todo"},
          {"cache", "kv", "turbov3", "q8_0"}]


def fake_embed(text):
    words = set(text.lower().replace(":", " ").replace(",", " ").split())
    v = [1.0 if words & g else 0.0 for g in GROUPS] + [0.05]
    n = math.sqrt(sum(x * x for x in v))
    return [x / n for x in v]


def make_embedder(calls, fail=False):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            calls.append(body)
            if fail:
                self.send_response(500)
                self.end_headers()
                self.wfile.write(b"boom")
                return
            out = json.dumps({"data": [{"index": i, "embedding": fake_embed(t)} for i, t in enumerate(body["input"])]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)
    return H


def seed(app):
    st = app.extensions["pb"].store
    st.add_event("launch", "tiel-coder exited with code 1: out of memory on ROCm1", level="error")
    st.add_event("bench", "benchmark qwen3: 62.0 t/s gen")
    st.add_note("builder", "Cache idea", "turbov3 kv cache with -b 4096 might be quicker")
    return st


def test_records_come_from_every_table_and_follow_changes(app, client):
    st = seed(app)
    mem = app.extensions["pb"].memory
    r = mem.sync()
    assert r["added"] >= 3 and r["embedded"] == 0, "no embedding model set: words only"
    sources = client.get("/api/assistant/memory").get_json()["by_source"]
    assert sources["event"] >= 2 and sources["note"] == 1

    note = st.list_notes()[0]
    st.update_note(note["id"], body="changed text")
    assert mem.sync()["changed"] == 1
    st.delete_note(note["id"])
    assert mem.sync()["removed"] == 1
    assert mem.sync() == {"added": 0, "changed": 0, "removed": 0, "embedded": 0}, "nothing new, nothing done"


def test_word_search_without_an_embedding_model(app, client):
    seed(app)
    app.extensions["pb"].memory.sync()
    hits = client.post("/api/assistant/memory/search", json={"q": "turbov3"}).get_json()["results"]
    assert hits[0]["source"] == "note" and hits[0]["matched_by"] == ["words"] and hits[0]["similarity"] is None
    assert client.post("/api/assistant/memory/search", json={"q": ""}).status_code == 400


def test_meaning_finds_what_words_miss(app, client):
    seed(app)
    calls = []
    with serve(make_embedder(calls)) as url:
        client.put("/api/settings/memory", json={"endpoint": url + "/v1", "model": "nomic-embed"})
        res = client.post("/api/assistant/memory/sync", json={}).get_json()
        assert res["result"]["embedded"] == res["records"] == res["embedded"]
        assert calls[0]["model"] == "nomic-embed"
        # "crashed" appears nowhere; the embedding links it to "exited ... out of memory"
        hits = client.post("/api/assistant/memory/search", json={"q": "why it crashed", "k": 3}).get_json()["results"]
    assert "out of memory" in hits[0]["text"] and "meaning" in hits[0]["matched_by"] and hits[0]["similarity"] > 0.5


def test_changing_the_model_reembeds_and_failures_are_reported(app, client):
    seed(app)
    calls = []
    with serve(make_embedder(calls)) as url:
        client.put("/api/settings/memory", json={"endpoint": url + "/v1", "model": "a"})
        client.post("/api/assistant/memory/sync", json={})
        n = len(calls)
        client.put("/api/settings/memory", json={"model": "b"})
        assert client.post("/api/assistant/memory/sync", json={}).get_json()["embedded"] >= 3
        assert len(calls) > n, "records embedded by another model are embedded again"
    with serve(make_embedder([], fail=True)) as url:
        client.put("/api/settings/memory", json={"endpoint": url + "/v1"})
        client.post("/api/assistant/memory/rebuild")
        st = client.post("/api/assistant/memory/sync", json={}).get_json()
        assert st["embedded"] == 0 and "HTTP 500" in st["error"]
        # search still answers by words while the embedding server is down
        assert client.post("/api/assistant/memory/search", json={"q": "turbov3"}).get_json()["results"]


def test_the_assistant_sees_related_records(app, client):
    seed(app)
    app.extensions["pb"].memory.sync()
    digest = app.extensions["pb"].assistant.digest("what about turbov3?")
    assert "RELATED RECORDS" in digest and "turbov3 kv cache" in digest
    client.put("/api/settings/memory", json={"enabled": False})
    assert "RELATED RECORDS" not in app.extensions["pb"].assistant.digest("what about turbov3?")


def test_the_background_hook_keeps_it_current(app):
    seed(app)
    hook = next(h for h in app.extensions["pb"].collector.hooks if h.__name__ == "watch_memory")
    hook()
    assert app.extensions["pb"].memory.status()["records"] >= 3


def test_outside_sources_join_and_cannot_break_it(app):
    mem = app.extensions["pb"].memory
    mem.extra_sources.append(lambda: [("odysseus-brain", "m1", time.time(), "Brain: the user prefers row split for two chats")])

    def broken():
        raise RuntimeError("odysseus is down")
    mem.extra_sources.append(broken)
    mem.sync()
    assert mem.search("row split")[0]["source"] == "odysseus-brain"
    assert "odysseus is down" in mem.status()["error"]


def test_models_are_remembered_and_have_similar_ones(app, client):
    st = seed(app)
    for mid, arch, q in (("qwen3-8b-q4", "qwen3", "Q4_K_M"), ("qwen3-14b-q4", "qwen3", "Q4_K_M"), ("whisper-small", "whisper", "F16")):
        st.upsert_model_seen({"model_id": mid, "name": mid, "size_bytes": 5 * 2**30, "quant": q, "arch": arch, "kind": "llm", "origin": "library"})
    mem = app.extensions["pb"].memory
    mem.sync()
    assert client.get("/api/assistant/memory").get_json()["by_source"]["model"] == 3
    r = client.get("/api/inventory/similar/qwen3-8b-q4").get_json()
    assert r["results"][0]["model_id"] == "qwen3-14b-q4", "same arch and quant ranks first (by words without embeddings)"


def test_log_insight_pairs_requests_and_keeps_load_readings():
    from src.log_analyzer import insight
    log = ("load_tensors: ROCm0 model buffer size = 4000.00 MiB\n"
           "prompt eval time =   100.00 ms /   50 tokens (    2.00 ms per token,   500.00 tokens per second)\n"
           "       eval time =  1000.00 ms /   64 tokens (   15.62 ms per token,    64.00 tokens per second)\n"
           "prompt eval time =   200.00 ms /  100 tokens (    2.00 ms per token,   500.00 tokens per second)\n"
           "       eval time =  2000.00 ms /  80 tokens (   25.00 ms per token,    40.00 tokens per second)\n")
    r = insight(log)
    assert [(x["prefill"], x["decode"]) for x in r["requests"]] == [(500.0, 64.0), (500.0, 40.0)]
    assert any("model buffer" in line for line in r["readings"])
    assert r["parsed"]["gpu_by_device"] == {"ROCm0": 4000.0}
