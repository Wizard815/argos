"""The memory's link graph: records joined by what they name, searched with no model."""
from src.memory_links import entities, query_entities, rank


def test_a_record_is_linked_to_what_it_names():
    text = ("Load 2026-11-01 until 2026-11-01: qwen3-coder-next-q5_k_s--qwencoder-512 on GPU 0,1, kv q8_0, "
            "HSA_ENABLE_SDMA=0 llama-server --port 5801 -sm tensor --flash-attn on: out of memory, GGML_ASSERT")
    e = entities(text, 1762000000.0)
    assert {"model:qwen3-coder-next-q5_k_s", "profile:qwen3-coder-next-q5_k_s--qwencoder-512", "gpu:0", "gpu:1",
            "env:HSA_ENABLE_SDMA", "flag:sm", "flag:flash-attn", "trouble:oom", "trouble:crash"} <= e
    assert "flag:port" not in e, "a port says nothing about the setup"
    assert any(x.startswith("day:") for x in e)


def test_a_question_names_a_model_by_the_words_of_its_name():
    q = query_entities("why did the qwen coder run out of memory?", ["qwen3-coder-next-q5_k_s", "gemma-4-e2b"])
    assert "model:qwen3-coder-next-q5_k_s" in q and "trouble:oom" in q and "model:gemma-4-e2b" not in q


def test_rarer_shared_things_count_more():
    links = [(1, "trouble:crash"), (1, "gpu:0"), (2, "gpu:0"), (3, "gpu:0"), (2, "env:X_Y_Z")]
    df = {"trouble:crash": 1, "gpu:0": 3, "env:X_Y_Z": 1}
    ranked = rank({"trouble:crash", "gpu:0", "env:X_Y_Z"}, links, df, total=10)
    assert [r[0] for r in ranked][:2] in ([1, 2], [2, 1]) and ranked[-1][0] == 3, "the record sharing only GPU 0 is last"


def test_the_memory_links_its_records_and_finds_similar_ones_with_no_model(app, client):
    st = app.extensions["pb"].store
    mem = app.extensions["pb"].memory
    st.add_event("launch", "qwen3-coder-next--qwencoder exited while starting: out of memory on GPU 0", level="error")
    st.add_event("launch", "qwen3-coder-next--qwencoder crashed: GGML_ASSERT failed, out of memory", level="error")
    st.add_event("bench", "benchmark gemma-4-e2b--fast: 62.0 t/s gen on GPU 1")
    mem.sync()
    status = client.get("/api/assistant/memory").get_json()
    assert status["links_on"] and status["links"] > 0

    r = client.get("/api/assistant/memory/similar?q=why does the qwen3 coder run out of memory").get_json()
    assert "model:qwen3-coder-next" in r["entities"] and "trouble:oom" in r["entities"]
    assert [x["text"] for x in r["results"][:2]] and all("qwen3-coder-next" in x["text"] for x in r["results"][:2])
    assert "trouble:oom" in r["results"][0]["shared"]

    one = r["results"][0]
    like = client.get(f"/api/assistant/memory/similar?source={one['source']}&key={one['key']}").get_json()["results"]
    assert like and like[0]["id"] != one["id"] and "qwen3-coder-next" in like[0]["text"], "like one record: the other crash"
    assert client.get("/api/assistant/memory/similar").status_code == 400

    hits = client.post("/api/assistant/memory/search", json={"q": "qwen coder out of memory"}).get_json()["results"]
    assert "links" in hits[0]["matched_by"], "the links join the words (and meaning) in the memory's search"

    st.add_event("launch", "gemma-4-e2b--fast crashed: out of memory")
    mem.sync()  # a new record is linked as it comes in
    top = client.get("/api/assistant/memory/similar?q=did gemma crash").get_json()["results"][0]
    assert "gemma-4-e2b--fast crashed" in top["text"]


def test_the_cpu_embedder_runs_in_bursts_never_while_a_model_works():
    from src.memory import EmbedBursts
    b = EmbedBursts(quiet_s=60)
    assert b.due(0, busy=True) is False, "a model is working: wait"
    assert b.due(10, busy=False) is True, "the burst at start, once things are quiet"
    assert b.due(20, busy=False) is False and b.due(500, busy=False) is False, "one burst per quiet spell"
    assert b.due(600, busy=True) is False
    assert b.due(620, busy=False) is False and b.due(670, busy=False) is False, "quiet for 50 s only"
    assert b.due(681, busy=False) is True, "a minute after the models stopped"
    assert b.due(700, busy=False) is False


def test_links_only_runs_no_embedding_model(app, client):
    mem = app.extensions["pb"].memory
    client.put("/api/settings/integrations", json={"engine_url": "http://engine"})
    client.put("/api/settings/memory", json={"enabled": True, "model": "qembed", "search_by": "embed"})
    assert mem.vectors_on() and mem.links_on()
    client.put("/api/settings/memory", json={"search_by": "links", "links": False})
    assert not mem.vectors_on() and mem.links_on(), "the link graph instead: on even with the switch off"
    mem.embed = lambda texts: (_ for _ in ()).throw(AssertionError("no embedding in links mode"))
    app.extensions["pb"].store.add_event("launch", "coder crashed: out of memory", level="error")
    mem.sync()
    assert mem.burst() == 0
    hits = client.post("/api/assistant/memory/search", json={"q": "coder out of memory"}).get_json()["results"]
    assert hits and "meaning" not in hits[0]["matched_by"]
