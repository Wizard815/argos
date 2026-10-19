"""The inventory: model sessions, the registry of everything ever seen, refresh, and the assistant's clean-up."""
import json
import os
import sqlite3
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from src.db import Store
from src.inventory import Inventory, extract, is_known, norm_key

from .fakes import make_engine, serve
from .ggufs import kv_str, kv_u32, kv_str_array, llama_kvs, write_gguf

EXOTIC = ("HIP_VISIBLE_DEVICES=1 LLAMA_ENABLE_MTP_OPT=1 llama-server -m /m/Tiel-Q8_K_XL.gguf -c 262144 "
          "-ctk turbov3 -ctv turbov3 --split-mode row --brand-new-flag 7 --another -lm dio")


def hook(app, name):
    return next(h for h in app.extensions["pb"].collector.hooks if h.__name__ == name)


def traits(client, **params):
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    return client.get(f"/api/inventory/traits?{qs}").get_json()["traits"]


def by_value(client, kind):
    return {t["value"]: t for t in traits(client, kind=kind)}


# ----- reading commands ---------------------------------------------------------------------
def test_extract_takes_in_settings_it_has_never_seen():
    f = extract(EXOTIC, arch={"arch": "qwen35moe", "ftype": 7})
    assert (f["quant"], f["arch"], f["ctx"]) == ("Q8_K_XL", "qwen35moe", 262144)
    assert (f["cache_k"], f["cache_v"], f["split_mode"], f["load_mode"]) == ("turbov3", "turbov3", "row", "dio")
    assert f["spec_type"] == "draft-mtp", "the MTP env var counts even without --spec-type"
    assert f["flags"]["brand-new-flag"] == "7" and f["flags"]["another"] is True, "unknown flags are kept, not dropped"
    assert f["env"] == {"HIP_VISIBLE_DEVICES": "1", "LLAMA_ENABLE_MTP_OPT": "1"}
    assert ("cache_type", "turbov3") in f["traits"] and ("flag", "brand-new-flag") in f["traits"]


def test_extract_never_raises():
    assert extract("llama-server 'unterminated")["unparsed"] is True
    for empty in ("", None):
        got = extract(empty)  # type: ignore[arg-type]
        assert got["traits"] == [] and got["flags"] == {}
    assert extract("llama-server -m x.gguf -ctk")["unparsed"] is False


def test_quant_falls_back_to_the_file_type_and_unknown_ones_get_a_name():
    assert extract("llama-server -m /m/plain.gguf", arch={"ftype": 15})["quant"] == "Q4_K_M"
    assert extract("llama-server -m /m/plain.gguf", arch={"ftype": 99})["quant"] == "ftype-99"


def test_what_counts_as_familiar():
    assert is_known("quant", "UD-Q8_K_XL") and is_known("quant", "IQ4_NL") and not is_known("quant", "Q9_Z")
    assert not is_known("quant", "Q5_K_XXL") and is_known("quant", "ud-q4_k_xl"), "looking like a quant is not enough"
    assert is_known("cache_type", "q8_0") and not is_known("cache_type", "turbov3")
    assert is_known("split_mode", "row") and is_known("spec_type", "draft-mtp")
    assert is_known("flag", "split-mode") and is_known("flag", "temp") and not is_known("flag", "brand-new-flag")
    assert is_known("env", "HIP_VISIBLE_DEVICES") and not is_known("env", "MY_NEW_TOGGLE")
    assert norm_key("Turbo_V3") == norm_key("turbo-v3") == "turbov3"


# ----- the registry ---------------------------------------------------------------------------
def test_new_settings_are_logged_not_rejected(app, client):
    inv = app.extensions["pb"].inventory
    for value in ("turbov3", "", "   ", "x" * 5000, "ünïcode ✓", "a\nb"):
        inv.note("cache_type", value)  # must not raise
    inv.note("cache_type", "turbov3")
    t = by_value(client, "cache_type")["turbov3"]
    assert t["status"] == "new" and t["count"] == 2
    assert "" not in by_value(client, "cache_type")
    assert max(len(v) for v in by_value(client, "cache_type")) <= 200

    events = client.get("/api/events?kind=inventory").get_json()["events"]
    assert [e["message"] for e in events if "turbov3" in e["message"]] == ["new cache type seen: turbov3"], "logged once, on first sight"
    assert events[-1]["level"] in ("warn", "info")


def test_a_familiar_first_use_is_logged_but_not_flagged(app, client):
    app.extensions["pb"].inventory.note("split_mode", "row", "qwen35")
    t = by_value(client, "split_mode")["row"]
    assert t["status"] == "known"
    msgs = [e for e in client.get("/api/events?kind=inventory").get_json()["events"] if "row" in e["message"]]
    assert msgs[0]["message"] == "first use of split mode row (qwen35)" and msgs[0]["level"] == "info"


def test_saving_a_profile_registers_its_settings_before_it_ever_runs(client):
    client.post("/api/profiles", json={"name": "Turbo row", "base_model": "x", "launch_command": EXOTIC})
    assert by_value(client, "cache_type")["turbov3"]["status"] == "new"
    assert by_value(client, "split_mode")["row"]["status"] == "known"
    assert by_value(client, "spec_type")["draft-mtp"]["status"] == "known"
    assert by_value(client, "flag")["brand-new-flag"]["status"] == "new"
    assert by_value(client, "env")["LLAMA_ENABLE_MTP_OPT"]["status"] == "known"
    assert client.get("/api/inventory").get_json()["pending"] >= 3


def test_an_old_database_gains_the_inventory_tables(tmp_path):
    path = str(tmp_path / "old.db")
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    conn.execute("INSERT INTO settings VALUES ('x', '1')")
    conn.commit()
    conn.close()
    store = Store(path)
    assert store.list_traits() == [] and store.list_sessions() == [] and store.list_models_seen() == []
    assert store.get_settings() == {"x": 1}


# ----- sessions -------------------------------------------------------------------------------
def running_entry(model, cmd="llama-server -m /m/a-Q4_K_M.gguf -c 8192 -ctk q8_0 -ctv q8_0 --port ${PORT}", state="ready"):
    return {"model": model, "state": state, "cmd": cmd, "proxy": "http://x", "ttl": 0}


def pool_for(**models):
    return {"router": "pool", "enabled": True, "placement": True, "budget_gib": 62, "protect_seconds": 600,
            "cards": [], "swaps": [], "queue": [],
            "models": [{"id": m, "state": "ready", "cards": cards, "size_gib": gib, "size_known": True, "in_flight": 0,
                        "persistent": False, "protected": False, "load_seconds": 12.5}
                       for m, (cards, gib) in models.items()]}


def sessions(client, **params):
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    return client.get(f"/api/inventory/sessions?{qs}").get_json()["sessions"]


def test_sessions_follow_models_loading_moving_and_stopping(app, client):
    state_running = [running_entry("a")]
    pool = pool_for(a=(["0"], 12.0))
    with serve(make_engine([], [], pool=pool, running=state_running)) as url:
        client.put("/api/settings/integrations", json={"engine_url": url})
        sync = hook(app, "watch_inventory")

        sync()  # first look: a was already running
        s = sessions(client)
        assert len(s) == 1 and s[0]["model_id"] == "a" and s[0]["observed_running"] == 1 and s[0]["open"] == 1
        assert s[0]["gpus"] == ["0"] and s[0]["size_gib"] == 12.0 and s[0]["load_seconds"] == 12.5
        assert (s[0]["quant"], s[0]["ctx"], s[0]["cache_k"], s[0]["cache_v"]) == ("Q4_K_M", 8192, "q8_0", "q8_0")

        state_running.append(running_entry("b", "llama-server -m /m/b.gguf --split-mode row --spec-type draft-mtp --new-thing 1"))
        pool["models"] += pool_for(b=(["1"], 7.0))["models"]
        pool["models"][0]["cards"] = ["1"]
        sync()
        by = {x["model_id"]: x for x in sessions(client)}
        assert by["b"]["observed_running"] == 0, "b was seen starting, so its start time is real"
        assert (by["b"]["split_mode"], by["b"]["spec_type"]) == ("row", "draft-mtp")
        assert by["b"]["flags"]["new-thing"] == "1", "a flag nothing knows about is kept in the session"
        assert by["a"]["gpus"] == ["1"], "a moved to card 1"

        state_running[:] = [running_entry("b")]
        sync()
        by = {x["model_id"]: x for x in sessions(client)}
        assert by["a"]["open"] == 0 and by["a"]["ended_at"] and by["b"]["open"] == 1

    msgs = [e["message"] for e in client.get("/api/events?kind=inventory").get_json()["events"]]
    assert any("a was already running on GPU 0" in m for m in msgs) and any("b loaded on GPU 1" in m for m in msgs)
    assert any("a moved to GPU 1" in m for m in msgs)


def test_an_unreachable_engine_does_not_close_sessions(app, client):
    with serve(make_engine([], [], running=[running_entry("a")])) as url:
        client.put("/api/settings/integrations", json={"engine_url": url})
        hook(app, "watch_inventory")()
    hook(app, "watch_inventory")()  # Argos is gone now
    assert [s["open"] for s in sessions(client)] == [1]


def test_without_the_pool_router_the_command_names_the_gpu(app, client):
    cmd = "HIP_VISIBLE_DEVICES=1 llama-server -m /m/a.gguf"
    with serve(make_engine([], [], running=[running_entry("a", cmd)])) as url:  # /api/pool says enabled: false
        client.put("/api/settings/integrations", json={"engine_url": url})
        hook(app, "watch_inventory")()
    assert sessions(client)[0]["gpus"] == ["1"]


def test_session_stats_join_gpu_history_and_requests(app, client):
    c = app.extensions["pb"]
    now = time.time()
    sid = c.store.open_session({"model_id": "a", "started_at": now - 600, "last_seen": now, "gpus": ["0", "1"]})
    c.store.update_session(sid, ended_at=now - 60, open=0)
    c.store.add_gpu_samples([{"ts": now - 300, "gpu": 0, "name": "g0", "util": 80, "mem_used_mb": 20000, "mem_total_mb": 32768, "temp_c": 60, "power_w": 150, "source": "t"},
                             {"ts": now - 200, "gpu": 0, "name": "g0", "util": 40, "mem_used_mb": 24000, "mem_total_mb": 32768, "temp_c": 62, "power_w": 130, "source": "t"},
                             {"ts": now - 30, "gpu": 0, "name": "g0", "util": 99, "mem_used_mb": 1, "mem_total_mb": 32768, "temp_c": 90, "power_w": 1, "source": "t"}])
    c.store.add_requests([{"source_id": 1, "ts": now - 250, "model": "a", "tokens_per_second": 50.0, "prompt_per_second": 900.0, "output_tokens": 10}])
    s = sessions(client, model="a")[0]
    assert s["duration_s"] == 540
    g = s["gpu_stats"][0]
    assert g["util_avg"] == 60 and g["mem_max_mb"] == 24000, "the sample after the session ended is left out"
    assert s["requests"]["requests"] == 1 and s["requests"]["tg_avg"] == 50.0


def test_sessions_csv(app, client):
    c = app.extensions["pb"]
    c.store.open_session({"model_id": "a", "started_at": time.time() - 100, "last_seen": time.time(), "gpus": ["0"],
                          "quant": "Q8_0", "cache_k": "turbov3", "command": "llama-server -m a.gguf"})
    r = client.get("/api/inventory/sessions.csv")
    assert r.mimetype == "text/csv"
    lines = r.get_data(as_text=True).strip().splitlines()
    assert lines[0].startswith("started,ended,duration_s,model,gpus") and "turbov3" in lines[1] and ",a," in lines[1]


# ----- refresh --------------------------------------------------------------------------------
def make_gguf(app, name, ftype=None, arch="llama", size=1000):
    path = os.path.join(app.config["MODELS_DIR"], f"{name}.gguf")
    os.makedirs(app.config["MODELS_DIR"], exist_ok=True)
    kvs = [kv_str("general.architecture", arch)] + ([kv_u32("general.file_type", ftype)] if ftype is not None else []) + [
        kv_u32(f"{arch}.block_count", 4), kv_u32(f"{arch}.attention.head_count", 8), kv_u32(f"{arch}.embedding_length", 512),
        kv_str_array("tokenizer.ggml.tokens", ["a"])]
    write_gguf(path, kvs, size=size)
    return path


def refresh(client):
    return client.post("/api/inventory/refresh").get_json()["result"]


def test_refresh_records_new_models_and_quants_it_does_not_recognise(app, client):
    make_gguf(app, "plain-Q4_K_M", ftype=15)
    make_gguf(app, "mystery", ftype=99, arch="newarch")  # a quant llama.cpp has not told us about yet
    r = refresh(client)
    assert sorted(r["models_new"]) == ["mystery", "plain-q4_k_m"] and r["problems"] == []

    quants = by_value(client, "quant")
    assert quants["ftype-99"]["status"] == "new", "an unknown quant id is recorded under a made-up name, flagged new"
    assert quants["Q4_K_M"]["status"] == "known"
    assert "newarch" in by_value(client, "arch")
    inv = client.get("/api/inventory").get_json()
    assert {m["model_id"] for m in inv["models"]} >= {"mystery", "plain-q4_k_m"}

    again = refresh(client)
    assert again["models_new"] == [] and again["traits_new"] == [], "a second refresh finds nothing new"


def test_refresh_notices_a_model_that_went_away(app, client):
    path = make_gguf(app, "temp-Q8_0", ftype=7)
    refresh(client)
    os.remove(path)
    assert refresh(client)["models_gone"] == ["temp-q8_0"]
    assert [m["present"] for m in client.get("/api/inventory").get_json()["models"] if m["model_id"] == "temp-q8_0"] == [False]


def test_refresh_survives_garbage_files_and_a_dead_engine(app, client):
    os.makedirs(app.config["MODELS_DIR"], exist_ok=True)
    with open(os.path.join(app.config["MODELS_DIR"], "broken.gguf"), "wb") as f:
        f.write(b"not a gguf at all")
    client.put("/api/settings/integrations", json={"engine_url": "http://127.0.0.1:9"})
    r = refresh(client)
    assert "broken" in r["models_new"], "an unreadable file is still a model, just without header facts"
    assert any("engine" in p for p in r["problems"]) and r["host_changed"] is True, "local host facts are kept anyway"


def test_refresh_reads_engine_and_keeps_host_changes(app, client):
    hw = {"captured_at": "t1", "accelerators": [{"index": 0, "model": "Radeon Pro VII", "architecture": "gfx906"}]}
    with serve(make_engine([], [], model_ids=["only-in-config"], hardware=hw, version={"version": "v1"})) as url:
        client.put("/api/settings/integrations", json={"engine_url": url})
        assert "only-in-config" in refresh(client)["models_new"]
        host = client.get("/api/inventory").get_json()["host"]
        assert host["engine"]["data"]["hardware"]["accelerators"][0]["architecture"] == "gfx906"
        assert "captured_at" not in host["engine"]["data"]["hardware"], "a timestamp is not a hardware change"
        assert host["app"]["data"]["python"]

        hw["captured_at"] = "t2"
        assert refresh(client)["host_changed"] is False
        hw["accelerators"].append({"index": 1, "model": "Radeon Pro VII", "architecture": "gfx906"})
        assert refresh(client)["host_changed"] is True
        assert client.get("/api/inventory").get_json()["host_changes"]["engine"] == 2


# ----- review and clean-up ---------------------------------------------------------------------
def test_traits_can_be_marked_ignored_or_merged(app, client):
    inv = app.extensions["pb"].inventory
    for _ in range(3):
        inv.note("cache_type", "turbov3")
    inv.note("cache_type", "turbo_v3")
    t = by_value(client, "cache_type")

    assert client.put(f"/api/inventory/traits/{t['turbo_v3']['id']}", json={"status": "bogus"}).status_code == 400
    assert client.put(f"/api/inventory/traits/{t['turbo_v3']['id']}", json={"status": "alias", "canonical": "nope"}).status_code == 400
    assert client.put(f"/api/inventory/traits/{t['turbo_v3']['id']}", json={"status": "alias", "canonical": "turbo_v3"}).status_code == 400
    assert client.put("/api/inventory/traits/99999", json={"status": "known"}).status_code == 404

    r = client.put(f"/api/inventory/traits/{t['turbo_v3']['id']}", json={"status": "alias", "canonical": "turbov3", "note": "same"}).get_json()
    assert r["trait"]["status"] == "alias" and r["trait"]["canonical"] == "turbov3" and r["pending"] == 1
    r = client.put(f"/api/inventory/traits/{t['turbov3']['id']}", json={"status": "known"}).get_json()
    assert r["pending"] == 0 and r["trait"]["canonical"] == ""


def test_aliases_merge_spellings_in_the_model_summary(app, client):
    c = app.extensions["pb"]
    c.inventory.note("cache_type", "turbov3")
    t = c.inventory.note("cache_type", "turbo_v3")
    for ck in ("turbov3", "turbo_v3"):
        sid = c.store.open_session({"model_id": "m", "started_at": time.time() - 50, "last_seen": time.time(), "cache_k": ck, "cache_v": ck})
        c.store.update_session(sid, ended_at=time.time(), open=0)
    client.put(f"/api/inventory/traits/{t['id']}", json={"status": "alias", "canonical": "turbov3"})
    row = [m for m in client.get("/api/inventory").get_json()["models"] if m["model_id"] == "m"][0]
    assert row["cache"] == ["turbov3/turbov3"] and row["sessions"] == 2


def test_rule_suggestions_merge_lookalikes_and_accept_busy_ones(app):
    inv = app.extensions["pb"].inventory
    for _ in range(4):
        inv.note("cache_type", "turbov3")
    inv.note("cache_type", "Turbo-V3")
    for _ in range(3):
        inv.note("flag", "fancy-flag")
    inv.note("flag", "once")
    got = {s["value"]: s for s in inv.suggestions()}
    assert got["Turbo-V3"]["action"] == "alias" and got["Turbo-V3"]["canonical"] == "turbov3"
    assert got["fancy-flag"]["action"] == "known" and "3 times" in got["fancy-flag"]["reason"]
    assert got["turbov3"]["action"] == "known", "the spelling the others fold into is worth accepting too"
    assert "once" not in got, "a one-off is left for a person to look at"


def fake_openai(replies):
    """An OpenAI-style server that answers each chat call with the next reply."""
    calls = []

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            calls.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            body = json.dumps({"choices": [{"message": {"content": replies.pop(0)}}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"data": [{"id": "m"}]}')

    return H, calls


def point_assistant_at(client, url):
    r = client.put("/api/settings/assistant", json={"mode": "endpoint", "endpoint": url + "/v1", "model": "m"})
    assert r.status_code == 200, r.get_json()


def test_the_assistant_suggests_and_bad_answers_are_filtered_out(app, client):
    inv = app.extensions["pb"].inventory
    for _ in range(2):
        inv.note("cache_type", "turbov3")
    t_new = inv.note("cache_type", "turbo_v3")
    t_flag = inv.note("flag", "wat")
    inv.note("split_mode", "row")  # known: nothing to review
    reply = ("Sure! ```json\n" + json.dumps([
        {"id": t_new["id"], "action": "alias", "canonical": "turbov3", "reason": "same cache type"},
        {"id": t_flag["id"], "action": "delete everything", "reason": "bad action"},
        {"id": 9999, "action": "known"},
        {"id": t_new["id"], "action": "known"},
        {"id": "x", "action": "ignore"},
        {"id": by_value(client, "cache_type")["turbov3"]["id"], "action": "alias", "canonical": "does-not-exist"},
        "junk"]) + "\n```")
    handler, calls = fake_openai([reply])
    with serve(handler) as url:
        point_assistant_at(client, url)
        r = client.post("/api/inventory/assist").get_json()

    assert r["assistant"] == "ok"
    got = {s["trait_id"]: s for s in r["suggestions"]}
    assert got[t_new["id"]]["action"] == "alias" and got[t_new["id"]]["source"] == "assistant" and got[t_new["id"]]["canonical"] == "turbov3"
    assert got[t_new["id"]]["reason"] == "same cache type"
    assert t_flag["id"] not in got or got[t_flag["id"]]["source"] == "rules", "an invented action never gets through"
    assert 9999 not in got
    prompt = calls[0]["messages"][-1]["content"]
    assert "turbo_v3" in prompt and "kind=flag value=wat" in prompt and "split_mode" not in prompt.split("EXISTING VALUES")[0]
    # nothing was applied: a person clicks Apply
    assert by_value(client, "cache_type")["turbo_v3"]["status"] == "new"


def test_assist_falls_back_to_rules_when_the_assistant_is_down(app, client):
    inv = app.extensions["pb"].inventory
    for _ in range(3):
        inv.note("flag", "fancy-flag")
    client.put("/api/settings/assistant", json={"mode": "endpoint", "endpoint": "http://127.0.0.1:9/v1", "model": "m"})
    r = client.post("/api/inventory/assist").get_json()
    assert r["assistant"] and r["assistant"] != "ok"
    assert [s["action"] for s in r["suggestions"]] == ["known"] and r["suggestions"][0]["source"] == "rules"


def test_assist_with_nothing_to_review(client):
    r = client.post("/api/inventory/assist").get_json()
    assert r["suggestions"] == [] and r["assistant"] == "nothing to review"


def test_assistant_garbage_reply_is_not_an_error(app, client):
    app.extensions["pb"].inventory.note("flag", "wat")
    handler, _ = fake_openai(["I am sorry, I cannot do that."])
    with serve(handler) as url:
        point_assistant_at(client, url)
        r = client.post("/api/inventory/assist").get_json()
    assert r["ok"] and r["suggestions"] == [] and "no usable" in r["assistant"]


def test_the_assistant_digest_carries_the_inventory(app, client):
    c = app.extensions["pb"]
    c.inventory.note("cache_type", "turbov3")
    sid = c.store.open_session({"model_id": "qwen-big", "started_at": time.time() - 7200, "last_seen": time.time(),
                                "gpus": ["0", "1"], "quant": "Q8_K_XL", "cache_k": "q8_0", "cache_v": "q8_0", "split_mode": "row"})
    c.store.update_session(sid, ended_at=time.time(), open=0)
    c.store.upsert_model_seen({"model_id": "qwen-big", "name": "qwen-big", "origin": "library"})
    digest = client.get("/api/assistant/digest").get_json()["digest"]
    assert "INVENTORY: 1 models seen" in digest and "1 loads recorded" in digest
    assert "qwen-big 2.0 h on GPU 0,1 (Q8_K_XL, kv q8_0/q8_0, row)" in digest
    assert "New to review: cache_type turbov3 (x1)" in digest


def test_the_digest_is_unchanged_when_there_is_no_inventory(client):
    assert "INVENTORY" not in client.get("/api/assistant/digest").get_json()["digest"]


def test_the_assistant_can_run_on_a_model_the_engine_lists(app, client):
    from .fakes import make_engine, serve

    with serve(make_engine([], [], model_ids=["qwen3-0.6b-q8_0", "nomic-embed-text-v1.5"])) as url:
        client.put("/api/settings/integrations", json={"engine_url": url})
        j = client.get("/api/models/available").get_json()
        assert j["engine"] == ["nomic-embed-text-v1.5", "qwen3-0.6b-q8_0"] and j["embeddings"] == ["nomic-embed-text-v1.5"]
        assert j["engine_connected"] is True
        client.put("/api/settings/assistant", json={"mode": "engine", "model": "qwen3-0.6b-q8_0"})
        a = app.extensions["pb"].assistant
        assert a.endpoint() == (url + "/v1", "qwen3-0.6b-q8_0")
        assert a.server_state() == {"mode": "engine", "model": "qwen3-0.6b-q8_0"}
        client.put("/api/settings/assistant", json={"model": ""})
        with pytest.raises(Exception, match="Pick a model"):
            a.endpoint()
    client.put("/api/settings/integrations", json={"engine_url": ""})
    off = client.get("/api/models/available").get_json()
    assert off["engine"] == [] and off["engine_connected"] is False


def test_params_from_name_reads_the_size_out_of_a_file_name():
    from src.inventory import params_from_name
    assert params_from_name("Qwythos-9B-Claude-Mythos-5-1M-Q6_K") == "9B"
    assert params_from_name("Qwen3-0.6B-Q8_0") == "0.6B"
    assert params_from_name("foo-Q4_K_M") == ""


def test_request_speeds_of_minus_one_are_not_speeds(tmp_path):
    from src.db import Store
    store = Store(str(tmp_path / "t.db"))
    base = {"ts": 1000.0, "model": "embed", "status": 200}
    store.add_requests([{**base, "source_id": "a", "prompt_per_second": -1, "tokens_per_second": -1},
                        {**base, "source_id": "b", "prompt_per_second": 500.0, "tokens_per_second": 80.0}])
    (row,) = store.request_stats(0)
    assert row["requests"] == 2 and row["tg_avg"] == 80.0 and row["pp_avg"] == 500.0 and row["tg_max"] == 80.0


# ----- the inventory page on a big history -------------------------------------------------
def _seed_speeds(store, now):
    rows = [  # model, input, output, prefill/s, decode/s
        ("a", 500, 100, 900.0, 40.0), ("a", 600, 100, 1100.0, 55.0), ("a", 10, 100, 5000.0, 10.0),
        ("a", 500, 4, 100.0, 999.0), ("a", 500, 100, 0.0, 0.0), ("b", 200, 50, 300.0, 20.0)]
    for i, (m, tin, tout, pp, tg) in enumerate(rows):
        store._conn.execute("INSERT INTO requests (source_id, ts, model, input_tokens, output_tokens, prompt_per_second, "
                            "tokens_per_second) VALUES (?,?,?,?,?,?,?)", (i, now - 60 - i, m, tin, tout, pp, tg))
    store._conn.execute("INSERT INTO requests (source_id, ts, model, input_tokens, output_tokens, prompt_per_second, "
                        "tokens_per_second) VALUES (99, ?, 'a', 500, 100, 1.0, 1.0)", (now - 200 * 86400,))
    store._conn.commit()


def test_Store_speed_summary_ignores_short_requests_and_old_history(tmp_path):
    store, now = Store(str(tmp_path / "d.db")), time.time()
    _seed_speeds(store, now)
    r = store.model_speed_summary(now - 90 * 86400, 128, 16)
    assert r["a"]["prefill"] == {"min": 100.0, "max": 1100.0, "n": 3}  # the 10-token prompt and the zero are out
    assert r["a"]["decode"] == {"min": 10.0, "max": 55.0, "n": 3}  # the 4-token reply and the zero are out
    assert r["b"]["prefill"] == {"min": 300.0, "max": 300.0, "n": 1}


def _speeds_done(inv):
    """The speed statistics are computed in the background after the first ask; wait for that pass."""
    for _ in range(200):
        if not inv._speeds_busy:
            return
        time.sleep(0.05)
    raise AssertionError("the speed statistics never finished")


def test_Inventory_models_summary_is_cached_until_sessions_change(tmp_path):
    store = Store(str(tmp_path / "d.db"))
    inv = Inventory(store, lambda *a, **k: None)
    store.upsert_model_seen({"model_id": "m1", "name": "m1", "origin": "library"})
    assert [r["model_id"] for r in inv.models_summary()] == ["m1"]
    _speeds_done(inv)
    inv.models_summary()  # the finished speed pass dropped the list; this one is built and cached again
    store.upsert_model_seen({"model_id": "m2", "name": "m2", "origin": "library"})
    assert [r["model_id"] for r in inv.models_summary()] == ["m1"]  # still inside the TTL
    inv.invalidate()
    assert [r["model_id"] for r in inv.models_summary()] == ["m1", "m2"]
    inv.models_summary()[0]["name"] = "mutated"  # callers get copies, not the cache
    assert inv.models_summary()[0]["name"] == "m1"


def test_Inventory_models_summary_stays_fast_on_a_big_history(tmp_path):
    store, now = Store(str(tmp_path / "d.db")), time.time()
    store._conn.executemany(
        "INSERT INTO requests (source_id, ts, model, input_tokens, output_tokens, prompt_per_second, tokens_per_second) "
        "VALUES (?,?,?,?,?,?,?)", ((i, now - i, f"m{i % 40}", 500, 100, 800.0, 30.0) for i in range(100000)))
    store._conn.commit()
    inv = Inventory(store, lambda *a, **k: None)
    t = time.time()
    rows = inv.models_summary(now)
    assert time.time() - t < 3
    assert rows == [] or all("decode" in r for r in rows)
    assert store.model_speed_summary(now - 90 * 86400, 128, 16)["m3"]["decode"]["n"] == 2500


def test_Inventory_models_summary_serves_a_stale_list_while_it_rebuilds(tmp_path):
    store = Store(str(tmp_path / "d.db"))
    inv = Inventory(store, lambda *a, **k: None)
    store.upsert_model_seen({"model_id": "m1", "name": "m1", "origin": "library"})
    inv.models_summary()
    _speeds_done(inv)
    inv.models_summary()
    store.upsert_model_seen({"model_id": "m2", "name": "m2", "origin": "library"})
    inv._summary = (time.time() - 3600, inv._summary[1])  # long past its TTL
    assert [r["model_id"] for r in inv.models_summary()] == ["m1"]  # answered at once, from the old list
    for _ in range(100):
        if not inv._rebuilding:
            break
        time.sleep(0.05)
    assert [r["model_id"] for r in inv.models_summary()] == ["m1", "m2"]  # and the rebuild landed


def test_Inventory_a_rebuild_that_races_an_invalidate_is_dropped(tmp_path):
    store = Store(str(tmp_path / "d.db"))
    inv = Inventory(store, lambda *a, **k: None)
    inv.models_summary()
    inv.invalidate()
    inv._rebuilding = True
    inv._rebuild_summary()
    assert inv._summary[0] == 0.0 and not inv._rebuilding


def test_Store_logs_a_slow_query_with_where_the_time_went(tmp_path, caplog, monkeypatch):
    import src.db as dbmod
    monkeypatch.setattr(dbmod, "SLOW_DB_S", 0.0)
    store = Store(str(tmp_path / "d.db"))
    with caplog.at_level("WARNING", logger="argos.db"):
        store.list_runs()
    assert any("slow query" in r.message and "waited" in r.message and "runs" in r.message for r in caplog.records)


def test_a_slow_request_is_logged(app, client, caplog, monkeypatch):
    import src.app as appmod
    monkeypatch.setattr(appmod, "SLOW_REQUEST_S", 0.0)
    with caplog.at_level("WARNING"):
        assert client.get("/api/inventory").status_code == 200
    assert any("slow request" in r.message and "/api/inventory" in r.message for r in caplog.records)


def test_Inventory_the_page_gets_speeds_once_the_background_pass_has_run(tmp_path):
    store, now = Store(str(tmp_path / "d.db")), time.time()
    _seed_speeds(store, now)
    store.upsert_model_seen({"model_id": "a", "name": "a", "origin": "library"})
    inv = Inventory(store, lambda *a, **k: None)
    first = inv.models_summary()[0]
    assert first["decode"] is None  # a page never waits for the 90-day scan
    _speeds_done(inv)
    later = inv.models_summary()[0]
    assert later["decode"] == {"min": 10.0, "max": 55.0, "n": 3} and later["requests"] == 5
