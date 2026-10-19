"""Comparing launch configs: only the small things that changed, and what they did."""
import time

from src.compare import change_lines, describe_change, diff_settings

BASE = "llama-server --model /m/qwen3-8b-Q4_K_M.gguf --port 8080 -ngl 99 -fa on -c 32768"
WIDE = "HIP_VISIBLE_DEVICES=0,1 llama-server --model /m/qwen3-8b-Q4_K_M.gguf --port 8081 -ngl 99 -fa on -c 65536 -np 2 --no-mmap -ctk q8_0"


def seed(app, model="qwen3-8b-q4_k_m"):
    st = app.extensions["pb"].store
    now = time.time()
    for i, (cmd, tps, pps, gib) in enumerate([(BASE, 62.0, 800.0, 6.0), (BASE, 61.0, 790.0, 6.0), (WIDE, 55.0, 700.0, 9.5)]):
        t0 = now - 3600 * (3 - i)
        sid = st.open_session({"model_id": model, "started_at": t0, "last_seen": t0 + 1200, "source": "engine", "gpus": ["0"],
                               "measured_gib": gib, "command": cmd, "flags": {}, "env": {}})
        st.update_session(sid, open=0, ended_at=t0 + 1200)
        st.add_requests([{"source_id": 100 + i, "ts": t0 + 60, "model": model, "tokens_per_second": tps, "prompt_per_second": pps,
                          "input_tokens": 2000, "output_tokens": 200, "duration_ms": 4000, "status": 200}])
    return st


def test_describe_change_names_flags_env_and_removals():
    a = {"ctx-size": "32768", "flash-attn": "on", "mmap": True}
    b = {"ctx-size": "65536", "flash-attn": "on", "HIP_VISIBLE_DEVICES": "0,1", "parallel": "2"}
    assert describe_change(a, b) == "-mmap, parallel 2".replace("parallel 2", "+parallel 2") + ", ctx-size 32768→65536, +HIP_VISIBLE_DEVICES=0,1" \
        or "ctx-size 32768→65536" in describe_change(a, b)
    text = describe_change(a, b)
    assert "ctx-size 32768→65536" in text and "+HIP_VISIBLE_DEVICES=0,1" in text and "+parallel 2" in text and "-mmap" in text
    assert describe_change(a, a) == "no setting changed"
    rows = {r["key"]: r for r in diff_settings([a, b])}
    assert rows["HIP_VISIBLE_DEVICES"]["kind"] == "env" and rows["ctx-size"]["kind"] == "flag"
    assert rows["flash-attn"]["differs"] is False and rows["HIP_VISIBLE_DEVICES"]["values"] == ["not set", "0,1"]


def test_compare_two_configs_of_one_model(app, client):
    seed(app)
    opts = client.get("/api/compare/options").get_json()["options"]
    mine = [o for o in opts if o["model_id"] == "qwen3-8b-q4_k_m"]
    assert len(mine) == 2, "the port does not make a new config, so the first two loads are one config"
    r = client.post("/api/compare", json={"items": [{"model_id": o["model_id"], "config": o["config"]} for o in mine]}).get_json()
    differ = {row["key"]: row["values"] for row in r["settings"] if row["differs"]}
    assert set(differ) == {"ctx-size", "parallel", "no-mmap", "cache-type-k", "HIP_VISIBLE_DEVICES"}, differ
    assert differ["HIP_VISIBLE_DEVICES"] == ["not set", "0,1"] and r["differences"] == 5
    decode = next(x for x in r["results"] if x["key"] == "decode")
    assert decode["values"][0] > decode["values"][1] and decode["change_pct"][1] < 0
    assert "ctx-size 32768→65536" in r["summary"][0] and "decode" in r["summary"][0]
    assert client.post("/api/compare", json={"items": [mine[0]]}).status_code == 400
    assert client.post("/api/compare", json={"items": [{"model_id": "nope", "config": "x"}, mine[0]]}).status_code == 400


def test_the_assistant_and_memory_know_the_small_changes(app, client):
    st = seed(app)
    lines = change_lines(st, "qwen3-8b-q4_k_m")
    assert len(lines) == 1 and "vs the usual" in lines[0] and "+HIP_VISIBLE_DEVICES=0,1" in lines[0] and "decode" in lines[0]
    focus = app.extensions["pb"].assistant.digest("how is qwen3-8b-q4_k_m doing?")
    assert "vs the usual" in focus
    st.upsert_model_seen({"model_id": "qwen3-8b-q4_k_m", "name": "qwen3-8b-q4_k_m", "origin": "engine"})
    mem = app.extensions["pb"].memory
    mem.sync()
    hits = mem.search("what happened when I changed the context", 20)
    assert any(h["source"] == "change" and "ctx-size 32768→65536" in h["text"] for h in hits) or \
        any(t.startswith("Change:") for (_, _, _, t) in mem.collect())


def test_inventory_lists_lowest_and_highest_speeds(app, client):
    seed(app)
    inv = app.extensions["pb"].inventory
    client.get("/api/inventory")  # the first ask starts the speed statistics in the background
    for _ in range(200):
        if not inv._speeds_busy:
            break
        time.sleep(0.05)
    row = next(m for m in client.get("/api/inventory").get_json()["models"] if m["model_id"] == "qwen3-8b-q4_k_m")
    assert row["decode"] == {"min": 55.0, "max": 62.0, "n": 3} and row["prefill"]["min"] == 700.0 and row["prefill"]["max"] == 800.0
