"""Per-model speeds by launch config, and concurrency."""
import time

from src.model_perf import UNTRACKED, concurrency, labels, model_perf, signature

A = "HIP_VISIBLE_DEVICES=0,1 llama-server --model /app/models/m-Q8_0.gguf --host 0.0.0.0 --port ${PORT} -c 65536 -np 2 --split-mode layer"
B = "HIP_VISIBLE_DEVICES=0,1 llama-server -m /models/m-Q8_0.gguf --port 9001 -c 65536 -np 2 --split-mode row"


def test_signature_ignores_what_does_not_change_speed():
    a, sa = signature(A)
    a2, _ = signature(A.replace("${PORT}", "5800").replace("/app/models/", "/elsewhere/") + " --api-key x --jinja")
    assert a == a2, "port, host, API key, jinja and the folder do not make a new config"
    assert sa["model"] == "m-Q8_0.gguf" and sa["parallel"] == "2" and sa["HIP_VISIBLE_DEVICES"] == "0,1"
    assert signature(B)[0] != a, "a different split mode is a different config"
    assert signature("llama-server 'broken")[0].startswith("raw-")


def test_labels_show_only_what_differs():
    cfgs = {signature(A)[0]: signature(A)[1], signature(B)[0]: signature(B)[1]}
    got = labels(cfgs)
    assert got[signature(A)[0]] == "sm layer" and got[signature(B)[0]] == "sm row"
    alone = labels({signature(A)[0]: signature(A)[1]})
    assert alone[signature(A)[0]] == "ctx 65536 · np 2 · sm layer"
    assert labels({UNTRACKED: {}})[UNTRACKED].startswith("untracked")


def test_labels_name_what_sets_each_config_apart():
    base = "llama-server -m /m/x.gguf -c 131072 -np 2 -ctk q8_0 -ctv q8_0 --split-mode layer"
    cmds = [base.replace("131072", "262144").replace("-np 2", "-np 4"),
            base + " -b 4096 -ub 2048",
            base.replace("layer", "row"),
            base.replace("q8_0", "turbov3") + " -b 4096 -ub 2048"]
    cfgs = {signature(c)[0]: signature(c)[1] for c in cmds}
    got = [labels(cfgs)[signature(c)[0]] for c in cmds]
    assert got == ["ctx 262144 · np 4 · kv q8_0 · sm layer",
                   "ctx 131072 · np 2 · kv q8_0 · sm layer · b 4096 · ub 2048",
                   "ctx 131072 · np 2 · kv q8_0 · sm row",
                   "ctx 131072 · np 2 · kv turbov3 · sm layer · b 4096 · ub 2048"]
    mixed = labels({"a": {"cache-type-k": "q8_0", "cache-type-v": "f16"}, "b": {}})
    assert mixed["a"] == "kv q8_0/f16" and mixed["b"] == "default kv"


def test_concurrency_counts_overlapping_requests():
    #  0-10 overlaps 5-15; 20-30 alone; 25-26 inside 20-30
    got = concurrency([(0, 10), (5, 15), (20, 30), (25, 26), (40, 41)])
    assert got == [2, 2, 2, 2, 1]
    assert concurrency([(0, 10), (10, 20)]) == [1, 1], "back to back is not at the same time"


def add_request(store, i, ts, dur_s, prefill, decode, n_in=500, n_out=200, model="m"):
    store.add_requests([{"source_id": i, "ts": ts, "model": model, "duration_ms": int(dur_s * 1000),
                         "input_tokens": n_in, "output_tokens": n_out, "prompt_per_second": prefill,
                         "tokens_per_second": decode}])


def test_requests_are_split_by_the_config_they_ran_under(app):
    store = app.extensions["pb"].store
    now = time.time()
    s1 = store.open_session({"model_id": "m", "started_at": now - 7200, "last_seen": now, "command": A, "gpus": ["0", "1"], "size_gib": 40})
    store.update_session(s1, ended_at=now - 3600, open=0)
    store.open_session({"model_id": "m", "started_at": now - 3500, "last_seen": now, "command": B, "gpus": ["0", "1"], "measured_gib": 41})
    # config A: one alone, then two at the same time (slower)
    add_request(store, 1, now - 7000, 10, 900, 40)
    add_request(store, 2, now - 6000, 10, 800, 30)
    add_request(store, 3, now - 5995, 10, 700, 28)
    # config B: faster decode
    add_request(store, 4, now - 3000, 10, 950, 55)
    add_request(store, 5, now - 2000, 10, 990, 60)
    # too short to count for prefill or decode, but still a request
    add_request(store, 6, now - 1000, 1, 5000, 999, n_in=10, n_out=2)
    # before any recorded load
    add_request(store, 7, now - 86400, 10, 600, 20)

    perf = model_perf(store, "m", now=now)
    cfg = {c["label"]: c for c in perf["configs"]}
    a, b = cfg["sm layer"], cfg["sm row"]
    assert a["requests"] == 3 and a["decode"]["median"] == 30 and a["concurrency_max"] == 2
    assert a["decode_by_concurrency"] == {"1": {"n": 1, "median": 40}, "2": {"n": 2, "median": 29.0}}
    assert a["parallel_setting"] == "2" and a["vram_gib"] == 40 and a["gpus"] == ["0", "1"] and a["sessions"] == 1
    assert b["requests"] == 3 and b["decode"]["median"] == 57.5 and b["prefill"]["n"] == 2, "the tiny request is left out of the speeds"
    assert b["vram_gib"] == 41
    untracked = [c for c in perf["configs"] if c["id"] == UNTRACKED][0]
    assert untracked["requests"] == 1 and untracked["slot"] == len(perf["configs"]) - 1, "untracked is listed last"
    assert [c["slot"] for c in perf["configs"]] == list(range(len(perf["configs"]))), "colors follow first use"
    assert perf["summary"]["requests"] == 7 and perf["summary"]["configs"] == 2 and perf["summary"]["concurrency_max"] == 2


def test_benchmarks_join_the_config_with_the_same_settings(app):
    store = app.extensions["pb"].store
    now = time.time()
    store.open_session({"model_id": "m", "started_at": now - 600, "last_seen": now, "command": A})
    store.add_run({"run_id": "r1", "created_at": now - 300, "base_model": "m", "model_id": "m",
                   "launch_command": A.replace("${PORT}", "8080"), "prompt_per_second": 1000.0, "tokens_per_second": 45.0})
    perf = model_perf(store, "m", now=now)
    assert len(perf["configs"]) == 1
    c = perf["configs"][0]
    assert c["benchmarks"] == 1 and c["requests"] == 0 and c["decode"]["median"] == 45.0
    assert [p["source"] for p in perf["points"]] == ["bench"]


def test_variant_page_includes_base_model_benchmarks(app):
    store = app.extensions["pb"].store
    store.add_run({"run_id": "r1", "created_at": time.time() - 10, "base_model": "m", "model_id": "m",
                   "launch_command": B, "prompt_per_second": 900.0, "tokens_per_second": 50.0})
    assert model_perf(store, "m--fast")["summary"]["benchmarks"] == 1


def test_model_page_route(app, client):
    store = app.extensions["pb"].store
    assert client.get("/api/inventory/model/nope").status_code == 404
    store.open_session({"model_id": "org/model--x", "started_at": time.time() - 60, "last_seen": time.time(), "command": A})
    app.extensions["pb"].inventory.invalidate()  # the session was written behind the inventory's back
    j = client.get("/api/inventory/model/org/model--x?days=7").get_json()
    assert j["ok"] and j["model_id"] == "org/model--x" and j["days"] == 7 and len(j["configs"]) == 1
    assert j["model"]["model_id"] == "org/model--x"
