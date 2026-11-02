"""When the memory embeds, how it searches, and each assistant model's fallback when the GPUs are full."""
import time

from src.model_procs import ModelProcs


def _records(mem, sources=("event", "speed", "note")):
    for i, src in enumerate(sources):
        mem.store._x("INSERT INTO memory_records (source, key, ts, text, hash) VALUES (?, ?, ?, ?, ?)",
                     (src, str(i), time.time() - 100 + i, f"{src} {i}", f"h{i}"))


def _fake_embedder(mem, monkeypatch):
    asked = []
    monkeypatch.setattr(mem, "_embed_request", lambda texts: asked.extend(texts) or [[0.6, 0.8] for _ in texts])
    monkeypatch.setattr(mem, "vectors_on", lambda: True)
    monkeypatch.setattr(mem, "vector_key", lambda: "emb")
    return asked


def test_Memory_a_record_whose_numbers_move_keeps_its_vector(app, monkeypatch):
    mem = app.extensions["pb"].memory
    _records(mem)
    asked = _fake_embedder(mem, monkeypatch)
    assert mem.burst() == 3
    asked.clear()
    rows = [("speed", "1", time.time(), "speed 1: now 41 t/s"), ("event", "0", 0, "event 0 edited"), ("note", "2", 2, "note 2")]
    monkeypatch.setattr(mem, "collect", lambda: rows)
    mem.sync(budget=0, embed=False)
    got = {r["source"]: r for r in mem.store._q("SELECT source, vector, text FROM memory_records")}
    assert got["speed"]["vector"] is not None and got["speed"]["text"] == "speed 1: now 41 t/s", "kept, text updated"
    assert got["event"]["vector"] is None, "a record that should never change is embedded again"
    mem.search("speed", 3)  # a search embeds what is new, not the speed record again
    assert "speed 1: now 41 t/s" not in asked and "event 0 edited" in asked


def test_Memory_quiet_mode_search_embeds_only_its_question(app, client, monkeypatch):
    mem = app.extensions["pb"].memory
    client.put("/api/settings/memory", json={"embed_when": "quiet"})
    _records(mem)
    asked = _fake_embedder(mem, monkeypatch)
    mem.search("what was slow", 3)
    assert asked == ["what was slow"], "records wait for downtime"
    assert mem.status()["embed_when"] == "quiet" and mem.status()["activity"]["last"]["why"] == "the question of a search"
    asked.clear()
    assert mem.burst() == 3 and len(asked) == 3


def test_Memory_embed_when_follows_the_older_switch_until_it_is_set(app, client):
    mem = app.extensions["pb"].memory
    assert mem.embed_when() == "auto"
    client.put("/api/settings/memory", json={"background": False})
    assert mem.embed_when() == "search"
    client.put("/api/settings/memory", json={"embed_when": "quiet"})
    assert mem.embed_when() == "quiet"


def test_Memory_search_by_words_runs_no_model_and_no_links(app, client):
    mem = app.extensions["pb"].memory
    client.put("/api/settings/memory", json={"enabled": True, "model": "emb", "endpoint": "http://x/v1", "search_by": "words"})
    assert not mem.vectors_on() and not mem.links_on()
    client.put("/api/settings/memory", json={"search_by": "links"})
    assert not mem.vectors_on() and mem.links_on()
    client.put("/api/settings/memory", json={"search_by": "embed"})
    assert mem.vectors_on()


def test_Assistant_fallback_per_model(app, client):
    a = app.extensions["pb"].assistant
    client.put("/api/settings/assistant", json={"mode": "engine", "model": "chat", "escalate_model": "big", "model_cpu": "chat-cpu"})
    client.put("/api/settings/memory", json={"enabled": True, "model": "emb"})
    assert (a.fallback("chat"), a.fallback("escalate"), a.fallback("embed")) == ("model", "off", "same"), "the older settings"
    client.put("/api/settings/assistant", json={"fallback": "off", "escalate_fallback": "same"})
    client.put("/api/settings/memory", json={"fallback": "off"})
    assert a.role_models("chat") == ("chat", ""), "off: its CPU model is not used even though one is set"
    from src.routes.schedule import cpu_fallback_models
    with app.test_request_context():
        assert cpu_fallback_models() == ["big"], "only the escalation model may run on the CPU now"
    client.put("/api/settings/assistant", json={"fallback": "same"})
    client.put("/api/settings/memory", json={"fallback": "same"})
    with app.test_request_context():
        assert cpu_fallback_models() == ["chat", "big", "emb"]


def test_Assistant_escalation_falls_back_to_the_cpu_when_set(app, client, monkeypatch):
    a = app.extensions["pb"].assistant
    client.put("/api/settings/integrations", json={"engine_url": "http://engine"})
    client.put("/api/settings/assistant", json={"mode": "engine", "model": "chat", "escalate_model": "big", "escalate_to_loaded": False})
    monkeypatch.setattr(a, "_pool", lambda: {"models": [{"id": "big", "state": "stopped", "size_gib": 40}], "cards": [{"free_gib": 4}]})
    monkeypatch.setattr(a, "_cpu_only", lambda model: False)
    assert a.bigger_model()[0] == "", "off: no room, so the small model answers"
    client.put("/api/settings/assistant", json={"escalate_fallback": "same"})
    model, why = a.bigger_model()
    assert model == "big" and "on the CPU" in why


def test_ModelProcs_runs_ps_with_the_launch_env_and_says_why_it_failed():
    seen = []
    procs = ModelProcs()
    procs._ssh_ps = None
    procs._run = lambda argv, env=None: seen.append((argv, env)) or ""
    running = [{"model": "emb", "cmd": "/app/bin/remote-llama rocm_srv3 --port 5801 -m x.gguf",
                "env": ["ROCM_SRV3_SSH_HOST=llama-rocm", "REMOTE_SSH_KEY=/data/ssh/server-3"]}]
    assert procs.sample(running, max_age=0) == {}
    assert seen[0][0] == ["/app/bin/remote-llama", "rocm_srv3", "--ps"]
    assert seen[0][1]["ROCM_SRV3_SSH_HOST"] == "llama-rocm", "the Servers host rides in the launch's env"
    assert "listed no llama.cpp process" in procs.errors["emb"]


def test_Benchmarks_live_lists_speeds_from_real_requests(app, client):
    st = app.extensions["pb"].store
    st.add_requests([{"source_id": 1, "ts": time.time() - 60, "model": "coder--chat", "tokens_per_second": 39.0,
                      "prompt_per_second": 1000.0, "output_tokens": 500, "input_tokens": 8000},
                     {"source_id": 2, "ts": time.time() - 30, "model": "emb", "input_tokens": 70}])
    rows = client.get("/api/benchmarks/live").get_json()["rows"]
    assert [r["model"] for r in rows] == ["coder--chat"], "embeddings have no speeds to show"
    assert rows[0]["tg_avg"] == 39.0


def test_Memory_a_burst_leaves_an_unloaded_embedder_alone_for_a_few_records(app, monkeypatch):
    mem = app.extensions["pb"].memory
    _records(mem)  # 3 new records
    asked = _fake_embedder(mem, monkeypatch)
    assert mem.burst(loaded=False) == 0 and asked == [], "3 records are not worth loading it: its idle timer runs out"
    _records(mem, sources=tuple(f"event{i}" for i in range(30)))
    assert mem.burst(loaded=False) > 0, "a real batch is"
    asked.clear()
    mem.store._x("UPDATE memory_records SET indexed_at = 0, ts = 1")  # every vector is stale now
    assert mem.burst(loaded=False) == 0 and asked == [], "moved numbers never start it"
    assert mem.burst(loaded=True) > 0, "but are refreshed while it is loaded anyway"
