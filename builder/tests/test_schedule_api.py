"""The Schedule HTTP API: rows, plan, applying it to the engine, simulator, measuring and the live view."""
import os
import time

import yaml

from .conftest import fake_command
from .fakes import make_engine, serve
from .ggufs import llama_kvs, write_gguf

GIB = 2 ** 30


def make_models(app, **files):
    """Sparse GGUFs in the app's models folder: name -> size in GiB. Returns name -> path."""
    root = app.config["MODELS_DIR"]
    os.makedirs(root, exist_ok=True)
    paths = {}
    for name, gib in files.items():
        path = os.path.join(root, f"{name}.gguf")
        write_gguf(path, llama_kvs(layers=32, kv_heads=8, emb=4096, train_ctx=32768), size=int(gib * GIB))
        paths[name] = path
    return paths


def rows_by_id(client):
    return {r["id"]: r for r in client.get("/api/schedule").get_json()["rows"]}


def save_profile(client, name, path, extra=""):
    cmd = f'llama-server --model "{path}" --host 0.0.0.0 --port 8080 -ngl 99 {extra}'
    r = client.post("/api/profiles", json={"name": name, "base_model": os.path.basename(path), "launch_command": cmd})
    assert r.status_code == 200, r.get_json()
    return r.get_json()["profile"]


# ----- rows -------------------------------------------------------------------------
def test_rows_list_models_and_profile_variants_with_estimates(app, client):
    paths = make_models(app, big=10)
    save_profile(client, "Long ctx", paths["big"], "-c 65536 --cache-type-k q8_0 --cache-type-v q8_0")
    save_profile(client, "Short", paths["big"], "-c 4096")

    rows = rows_by_id(client)
    assert {"big", "big--long-ctx", "big--short"} <= set(rows)
    long, short, base = rows["big--long-ctx"], rows["big--short"], rows["big"]

    assert long["kind"] == "profile" and long["base_id"] == "big" and long["profile_id"]
    assert base["kind"] == "model" and base["file_gib"] == 10.0
    # llama arch above: 32 layers * 8 KV heads * (128 + 128) values; q8_0 is 34/32 bytes per value
    assert long["estimate"]["kv_gib"] == round(32 * 8 * 256 * (34 / 32) * 65536 / GIB, 2)
    assert short["estimate"]["kv_gib"] == round(32 * 8 * 256 * 2 * 4096 / GIB, 2)
    assert long["effective_gib"] > short["effective_gib"] > 10 and long["source"] == "estimate"
    assert long["ctx"] == 65536 and long["cache_k"] == "q8_0"


def test_rows_use_the_models_own_context_when_the_command_has_none(app, client):
    paths = make_models(app, m=1)
    save_profile(client, "Default ctx", paths["m"])
    row = rows_by_id(client)["m--default-ctx"]
    assert row["ctx"] == 32768, "llama-server defaults -c to the model's own context"


def test_missing_model_file_has_no_size(app, client):
    save_profile(client, "Elsewhere", "/nowhere/ghost.gguf")
    row = rows_by_id(client)["ghost--elsewhere"]
    assert row["effective_gib"] == 0 and row["estimate"] is None and row["source"] == ""


# ----- plan -------------------------------------------------------------------------
def test_plan_defaults_validation_and_precedence(app, client):
    paths = make_models(app, big=10)
    j = client.get("/api/schedule").get_json()
    assert j["plan"]["enabled"] is False and j["plan"]["cards"] == []

    assert client.put("/api/schedule", json={"cards": [32, 0]}).status_code == 400
    assert client.put("/api/schedule", json={"bogus": 1}).status_code == 400
    r = client.put("/api/schedule", json={"cards": "32, 32", "persistent": ["big"], "sizes": {"big": 14.5}})
    assert r.status_code == 200
    row = {x["id"]: x for x in r.get_json()["rows"]}["big"]
    assert row["effective_gib"] == 14.5 and row["source"] == "plan" and row["persistent"] is True
    assert row["estimate"]["gib"] != 14.5, "the estimate stays visible beside a size set by hand"
    assert client.get("/api/schedule").get_json()["plan"]["cards"] == [32.0, 32.0]


def test_setup_can_seed_the_plan_and_the_schedule_view_wins(client, monkeypatch):
    monkeypatch.setenv("ARGOS_SCHEDULE_POOL", "1")
    monkeypatch.setenv("ARGOS_SCHEDULE_CARDS", "32, 32")
    plan = client.get("/api/schedule").get_json()["plan"]
    assert plan["enabled"] is True and plan["cards"] == [32.0, 32.0]
    client.put("/api/schedule", json={"enabled": False, "cards": [24]})
    plan = client.get("/api/schedule").get_json()["plan"]
    assert plan["enabled"] is False and plan["cards"] == [24.0], "what you save in the Schedule view wins"
    monkeypatch.setenv("ARGOS_SCHEDULE_CARDS", "lots")
    assert client.get("/api/schedule").status_code == 200, "a bad value is ignored"


# ----- applying the plan to the engine ---------------------------------------------
def test_saving_applies_the_routing_block_and_follows_profile_changes(app, client, tmp_path, monkeypatch):
    paths = make_models(app, big=10)
    client.put("/api/settings/integrations", json={"engine_url": ""})
    out = tmp_path / "config.d" / "scheduler.generated.yaml"
    monkeypatch.setenv("ARGOS_SCHEDULE_FILE", str(out))
    j = client.put("/api/schedule", json={"enabled": True, "cards": [32, 32]}).get_json()
    assert j["applied"]["connected"] and j["applied"]["at"] and not j["applied"]["error"]

    doc = yaml.safe_load(out.read_text())
    pool = doc["routing"]["router"]["settings"]["pool"]
    assert doc["routing"]["router"]["use"] == "pool" and pool["cards"] == [32.0, 32.0]
    assert "big" in pool["sizes"], "library models get an estimated size"

    save_profile(client, "Fast", paths["big"], "-c 8192")
    pool = yaml.safe_load(out.read_text())["routing"]["router"]["settings"]["pool"]
    assert "big--fast" in pool["sizes"], "saving a profile adds it to the plan without another click"

    client.put("/api/schedule", json={"enabled": False})
    assert yaml.safe_load(out.read_text()) is None, "turning scheduling off empties the file"


def test_without_an_engine_nothing_is_written_and_unknown_models_are_skipped(app, client, tmp_path, monkeypatch):
    paths = make_models(app, big=10)
    monkeypatch.delenv("ARGOS_SCHEDULE_FILE", raising=False)
    assert client.put("/api/schedule", json={"enabled": True}).get_json()["applied"]["connected"] is False
    assert client.post("/api/schedule/export").status_code in (404, 405), "there is no export: saving applies the plan"
    save_profile(client, "Fast", paths["big"])
    out = tmp_path / "s.yaml"
    monkeypatch.setenv("ARGOS_SCHEDULE_FILE", str(out))

    with serve(make_engine([], [], model_ids=["big"])) as url:  # the engine has not made the variant yet
        client.put("/api/settings/integrations", json={"engine_url": url})
        j = client.put("/api/schedule", json={"enabled": True, "persistent": ["big", "ghost"]}).get_json()
    pool = yaml.safe_load(out.read_text())["routing"]["router"]["settings"]["pool"]
    assert pool["persistent"] == ["big"] and pool["sizes"].keys() == {"big"}
    assert "big--fast" in j["skipped"] and "ghost" in j["skipped"], "Argos would reject ids it does not have"
    assert j["known_from"] == "live"
    assert app.extensions["pb"].schedule_state["_retry"] is True, "re-checked soon, so the new profile gets in"


def test_without_the_live_list_nothing_is_filtered(app, client, tmp_path, monkeypatch):
    make_models(app, big=10)
    client.put("/api/settings/integrations", json={"engine_url": ""})
    out = tmp_path / "s.yaml"
    monkeypatch.setenv("ARGOS_SCHEDULE_FILE", str(out))
    j = client.put("/api/schedule", json={"enabled": True, "persistent": ["whatever"]}).get_json()
    pool = yaml.safe_load(out.read_text())["routing"]["router"]["settings"]["pool"]
    assert "big" in pool["sizes"] and pool["persistent"] == ["whatever"]
    assert j["skipped"] == [] and j["known_from"] == ""


def test_a_plan_the_engine_cannot_get_is_reported(app, client, tmp_path, monkeypatch):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    monkeypatch.setenv("ARGOS_SCHEDULE_FILE", str(blocker / "sub" / "s.yaml"))
    r = client.put("/api/schedule", json={"enabled": True}).get_json()
    assert r["applied"]["error"]
    assert any(e["kind"] == "schedule" and e["level"] == "error" for e in client.get("/api/events").get_json()["events"])


# ----- simulator --------------------------------------------------------------------
def test_simulate_runs_steps_with_plan_sizes(client):
    client.put("/api/schedule", json={"cards": [31, 31], "reserve_gib": 0, "sizes": {"a": 26, "b": 20, "s": 6}})
    steps = [{"op": "request", "model": m} for m in ("a", "b", "s")]
    j = client.post("/api/schedule/simulate", json={"steps": steps}).get_json()
    assert j["ok"] and j["placement"] is True
    assert [r["cards"] for r in j["results"]] == [["0"], ["1"], ["1"]]
    assert j["results"][2]["state"]["cards"][1]["used_gib"] == 26.0


def test_simulate_overrides_plan_and_sizes_without_saving(client):
    client.put("/api/schedule", json={"cards": [31, 31], "reserve_gib": 0, "sizes": {"a": 10}})
    j = client.post("/api/schedule/simulate", json={
        "steps": [{"op": "request", "model": "a"}], "plan": {"cards": [8, 8]}, "sizes": {"a": 12}}).get_json()
    assert j["results"][0]["cards"] == ["0", "1"], "12 GiB does not fit one 8 GiB card"
    assert client.get("/api/schedule").get_json()["plan"]["cards"] == [31.0, 31.0]


def test_simulate_input_errors(client):
    for bad in ({}, {"steps": []}, {"steps": ["x"]}, {"steps": [{"op": "request"}] * 201}):
        assert client.post("/api/schedule/simulate", json=bad).status_code == 400
    assert client.post("/api/schedule/simulate", json={"steps": [{"op": "wait", "minutes": 1}], "plan": {"cards": [-1]}}
                       ).status_code == 400
    r = client.post("/api/schedule/simulate", json={"steps": [{"op": "wait", "minutes": 1}]})
    assert r.status_code == 400 and "no cards" in r.get_json()["error"]


# ----- measuring --------------------------------------------------------------------
def wait_job(client, job_id, timeout=30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = client.get(f"/api/schedule/measure/{job_id}").get_json()["job"]
        if job["state"] in ("done", "error", "cancelled"):
            return job
        time.sleep(0.1)
    raise AssertionError(f"measurement never finished: {job}")


def fake_gpu(app, used_before_mb, used_after_mb):
    """Make GPU telemetry answer 'before', then 'after', whatever the launch did."""
    readings = iter([used_before_mb, used_after_mb, used_after_mb, used_after_mb])
    last = [used_after_mb]

    def sample():
        try:
            last[0] = next(readings)
        except StopIteration:
            pass
        return [{"gpu": i, "name": f"GPU {i}", "mem_used_mb": mb, "mem_total_mb": 32768} for i, mb in enumerate(last[0])]

    app.extensions["pb"].collector.sample = sample


def test_measure_records_the_vram_that_appeared(app, client):
    paths = make_models(app, big=1)
    prof = save_profile(client, "Fast", paths["big"])
    # the fake server stands in for llama-server; point the profile at it
    client.post("/api/profiles", json={"profile_id": prof["profile_id"], "name": "Fast", "base_model": "big",
                                       "launch_command": fake_command("--model", paths["big"], port="8080")})
    fake_gpu(app, [1000, 500], [1000 + 11000, 500 + 5000])

    j = client.post("/api/schedule/measure", json={"id": "big--fast", "settle_s": 0}).get_json()
    assert j["ok"]
    job = wait_job(client, j["job"]["job_id"])
    assert job["state"] == "done", job
    assert job["result"]["gpu_gib"] == round((11000 + 5000) / 1024, 2)
    assert job["result"]["per_card"] == [round(11000 / 1024, 3), round(5000 / 1024, 3)]

    plan = client.get("/api/schedule").get_json()
    row = {r["id"]: r for r in plan["rows"]}["big--fast"]
    assert row["measured"]["gib"] == round(16000 / 1024, 2) and row["source"] == "measured"
    assert row["effective_gib"] == row["measured"]["gib"]

    used = client.post("/api/schedule/measured/big--fast/use").get_json()
    assert used["plan"]["sizes"]["big--fast"] == row["measured"]["gib"]
    assert client.post("/api/schedule/measured/nothing/use").status_code == 404


def test_measure_falls_back_to_the_log_when_gpu_readings_do_not_move(app, client):
    paths = make_models(app, big=1)
    prof = save_profile(client, "Fast", paths["big"])
    client.post("/api/profiles", json={"profile_id": prof["profile_id"], "name": "Fast", "base_model": "big",
                                       "launch_command": fake_command("--model", paths["big"], port="8080")})
    fake_gpu(app, [2000], [2000])  # telemetry that never changes

    job = wait_job(client, client.post("/api/schedule/measure", json={"id": "big--fast", "settle_s": 0})
                   .get_json()["job"]["job_id"])
    # the fake server logs 4403.49 + 576 + 296 MiB of buffers on one device, plus 0.4 GiB of runtime context
    assert job["state"] == "done" and job["result"]["source"] == "log"
    assert job["result"]["gib"] == round((4403.49 + 576 + 296) / 1024 + 0.4, 2)


def test_measure_reports_failures_and_refuses_overlap(app, client):
    paths = make_models(app, big=1)
    assert client.post("/api/schedule/measure", json={"id": "nope"}).status_code == 404

    prof = save_profile(client, "Broken", paths["big"])
    client.post("/api/profiles", json={"profile_id": prof["profile_id"], "name": "Broken", "base_model": "big",
                                       "launch_command": f"definitely-not-a-real-binary-xyz --model {paths['big']} --port 8080"})
    fake_gpu(app, [0], [0])
    job = wait_job(client, client.post("/api/schedule/measure", json={"id": "big--broken", "settle_s": 0})
                   .get_json()["job"]["job_id"])
    assert job["state"] == "error" and "could not start" in job["message"]
    assert client.get("/api/schedule/measure/run_nope").status_code == 404


def test_measure_can_be_cancelled(app, client):
    paths = make_models(app, big=1)
    prof = save_profile(client, "Slow", paths["big"])
    client.post("/api/profiles", json={"profile_id": prof["profile_id"], "name": "Slow", "base_model": "big",
                                       "launch_command": fake_command("--model", paths["big"], "--load-delay", "30", port="8080")})
    fake_gpu(app, [0], [0])
    j = client.post("/api/schedule/measure", json={"id": "big--slow", "settle_s": 0}).get_json()["job"]
    assert client.post("/api/schedule/measure", json={"id": "big--slow"}).status_code == 409, "one at a time"
    time.sleep(0.5)
    assert client.post(f"/api/schedule/measure/{j['job_id']}/cancel").get_json()["cancelled"] is True
    assert wait_job(client, j["job_id"])["state"] == "cancelled"


# ----- live + watcher ------------------------------------------------------------------
def hook(app, name):
    """A background hook by function name, so tests do not depend on the order they were registered in."""
    return next(h for h in app.extensions["pb"].collector.hooks if h.__name__ == name)


def pool_state(**models):
    return {"router": "pool", "enabled": True, "placement": True, "budget_gib": 62, "protect_seconds": 600,
            "cards": [], "swaps": [], "queue": [],
            "models": [{"id": m, "state": st, "cards": cards, "size_gib": 10, "size_known": True, "in_flight": 0,
                        "persistent": False, "protected": False} for m, (st, cards) in models.items()]}


def test_live_passes_the_pool_through_and_reports_problems(app, client):
    j = client.get("/api/schedule/live").get_json()
    assert j["connected"] is False and "engine is not running" in j["error"]

    state = pool_state(a=("ready", ["0"]))
    with serve(make_engine([], [], pool=state)) as url:
        client.put("/api/settings/integrations", json={"engine_url": url})
        j = client.get("/api/schedule/live").get_json()
        assert j["connected"] is True and j["pool"]["models"][0]["id"] == "a"
    j = client.get("/api/schedule/live").get_json()
    assert j["connected"] is False and j["pool"] is None and j["error"]


def test_watcher_logs_loads_moves_unloads_and_long_waits(app, client):
    watch = hook(app, "watch_pool")
    state = pool_state(a=("ready", ["0"]), b=("stopped", []))
    with serve(make_engine([], [], pool=state)) as url:
        client.put("/api/settings/integrations", json={"engine_url": url})
        watch()  # the first look only records what is there
        assert not [e for e in client.get("/api/events?kind=schedule").get_json()["events"]]

        state.update(pool_state(a=("ready", ["1"]), b=("starting", ["0"])))
        state["queue"] = [{"model": "c", "position": 1, "waited_seconds": 45}]
        watch()
        state.update(pool_state(a=("stopped", []), b=("ready", ["0"])))
        watch()
        watch()  # nothing changed: nothing logged

    msgs = [e["message"] for e in client.get("/api/events?kind=schedule").get_json()["events"]]
    # a load says why it happened ("fit beside the others", or what it pushed out); an unload alone says it stopped
    loaded = [m for m in msgs if m.startswith("b loaded on GPU 0; ")]
    unloaded = [m for m in msgs if m.startswith("a unloaded")]
    assert len(loaded) == 1 and len(unloaded) == 1
    assert sorted(m for m in msgs if m not in loaded + unloaded) == ["a moved on GPU 1", "a request for c has waited 45s"]


def test_watcher_ignores_a_engine_without_the_pool(app, client):
    watch = hook(app, "watch_pool")
    with serve(make_engine([], [])) as url:  # answers {"enabled": false}
        client.put("/api/settings/integrations", json={"engine_url": url})
        watch()
        watch()
    assert client.get("/api/events?kind=schedule").get_json()["events"] == []


# ----- the Live view's row menu ------------------------------------------------------
def test_live_row_actions(app, client, monkeypatch, tmp_path):
    paths = make_models(app, big=1)
    save_profile(client, "Fast", paths["big"], "-c 8192")
    monkeypatch.setenv("ARGOS_SCHEDULE_FILE", str(tmp_path / "s.yaml"))
    running, calls = [{"model": "big--fast", "state": "ready", "cmd": "llama-server -m big.gguf"}], []
    with serve(make_engine([], [], model_ids=["big", "big--fast"], running=running, calls=calls)) as url:
        client.put("/api/settings/integrations", json={"engine_url": url})
        assert client.post("/api/schedule/live/big--fast/keep", json={"on": True}).get_json()["persistent"] is True
        assert "big--fast" in client.get("/api/schedule").get_json()["plan"]["persistent"]
        client.post("/api/schedule/live/big--fast/keep", json={"on": False})
        assert "big--fast" not in client.get("/api/schedule").get_json()["plan"]["persistent"]

        assert client.post("/api/schedule/live/big--fast/unload").get_json()["ok"]
        assert ("POST", "/api/models/unload/big--fast") in calls and running == []
        assert client.post("/api/schedule/live/big/load").get_json()["started"] is True

        log = client.get("/api/schedule/live/big--fast/log").get_json()["log"]
        assert "log of big--fast" in log

        cmd = client.get("/api/schedule/live/big--fast/command").get_json()
        assert cmd["source"] == "profile" and cmd["name"] == "Fast" and "-c 8192" in cmd["command"]
        assert client.get("/api/schedule/live/big/command").get_json()["source"] == "prefab"
        assert client.get("/api/schedule/live/ghost/command").status_code == 404
        assert client.post("/api/schedule/live/big/explode").status_code == 404


def test_profile_variants_the_pool_lists_count_as_known(app, client):
    """The engine leaves profile variants out of /v1/models; the pool still plans them, so they must get sizes."""
    from src.routes import schedule as sched
    state = pool_state(**{"base--fast": ("stopped", [])})
    with serve(make_engine([], [], pool=state, model_ids=["base"])) as url:
        client.put("/api/settings/integrations", json={"engine_url": url})
        with app.app_context():
            app.extensions["pb"].schedule_state.pop("_known", None)
            known, src = sched.known_ids()
    assert src == "live" and {"base", "base--fast"} <= known


def test_answer_first_reaches_the_engine_and_toggles_from_live(app, client, tmp_path, monkeypatch):
    """A model marked answer first goes into the pool block with its two numbers; ids the engine lacks stay out."""
    make_models(app, big=10)
    client.put("/api/settings/integrations", json={"engine_url": ""})
    out = tmp_path / "s.yaml"
    monkeypatch.setenv("ARGOS_SCHEDULE_FILE", str(out))
    client.put("/api/schedule", json={"enabled": True, "answer_first": ["big"], "answer_first_min_tokens": 8000,
                                      "answer_first_max_wait_s": "120"})
    pool = yaml.safe_load(out.read_text())["routing"]["router"]["settings"]["pool"]
    assert pool["answerFirst"] == {"models": ["big"], "minPromptTokens": 8000, "maxWaitSeconds": 120}
    client.put("/api/schedule", json={"answer_first": []})
    assert "answerFirst" not in yaml.safe_load(out.read_text())["routing"]["router"]["settings"]["pool"]

    with serve(make_engine([], [], model_ids=["big"])) as url:
        client.put("/api/settings/integrations", json={"engine_url": url})
        assert client.post("/api/schedule/live/big/answer_first", json={"on": True}).get_json()["answer_first"] is True
        assert client.get("/api/schedule").get_json()["plan"]["answer_first"] == ["big"]
        client.post("/api/schedule/live/big/answer_first", json={"on": False})
        assert client.get("/api/schedule").get_json()["plan"]["answer_first"] == []
