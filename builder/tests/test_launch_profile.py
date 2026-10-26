"""A Builder launch of a saved profile runs that profile's command in the engine, not the base model's own."""
import time

import pytest

from src.routes import core

CMD = 'HIP_VISIBLE_DEVICES=0,1 llama-server --model "/m/Test-8B-Q4_K_M.gguf" --port ${PORT} -ngl 99 -c 126500 --split-mode tensor'


@pytest.fixture
def engine(app, client, monkeypatch):
    """A fake engine: knows the base model, and whichever variants are listed in ``variants``."""
    client.put("/api/settings/integrations", json={"engine_url": "http://engine.test"})
    state = {"variants": set(), "loaded": [], "refreshed": 0}
    monkeypatch.setattr(core.engine_api, "model_ids", lambda url, timeout=3.0: {"test-8b-q4_k_m"})
    monkeypatch.setattr(core.engine_api, "pool_state", lambda url, timeout=3.0: {
        "enabled": True, "models": [{"id": "test-8b-q4_k_m"}, *({"id": v} for v in state["variants"])]})

    def refresh(url, timeout=30.0):
        state["refreshed"] += 1
    monkeypatch.setattr(core.engine_api, "odysseus_refresh", refresh)
    monkeypatch.setattr(core.engine_api, "load_model", lambda url, mid, timeout=900.0: state["loaded"].append(mid))
    monkeypatch.setattr(core, "PROFILE_WAIT_S", 0.2)
    return state


def save(client, name, command=CMD):
    return client.post("/api/profiles", json={"name": name, "base_model": "Test-8B", "launch_command": command}).get_json()["profile"]["profile_id"]


def launch(client, command, name="", profile_id=""):
    return client.post("/api/launches", json={"command": command, "base_model": "Test-8B", "profile_name": name, "profile_id": profile_id})


def loaded(engine, n=1):
    for _ in range(50):
        if len(engine["loaded"]) >= n:
            break
        time.sleep(0.02)
    return engine["loaded"]


def test_Launch_a_saved_profile_loads_its_variant(client, engine):
    pid = save(client, "ornith tensor")
    engine["variants"].add("test-8b-q4_k_m--ornith-tensor")
    j = launch(client, CMD, "ornith tensor", pid).get_json()
    assert j["ok"] and j["model"] == "test-8b-q4_k_m--ornith-tensor" and "warning" not in j
    assert loaded(engine) == ["test-8b-q4_k_m--ornith-tensor"], "not the base model with its own -c and card"


def test_Launch_a_profile_saved_twice_uses_the_variant_the_engine_made(client, engine):
    save(client, "first")
    pid = save(client, "copy")
    engine["variants"].add("test-8b-q4_k_m--first")  # one variant per command: the first label names it
    assert launch(client, CMD, "copy", pid).get_json()["model"] == "test-8b-q4_k_m--first"


def test_Launch_with_unsaved_changes_is_refused(client, engine):
    pid = save(client, "ornith tensor")
    r = launch(client, CMD.replace("126500", "65536"), "ornith tensor", pid)
    assert r.status_code == 409 and "unsaved" in r.get_json()["error"]
    assert engine["loaded"] == []


def test_Launch_waits_for_a_just_saved_profile_then_says_why_it_cannot(client, engine):
    pid = save(client, "ornith tensor")
    r = launch(client, CMD, "ornith tensor", pid)
    assert r.status_code == 409 and "odysseus.baseURL" in r.get_json()["error"]
    assert engine["refreshed"] == 1 and engine["loaded"] == []


def test_Launch_without_a_profile_warns_that_the_engine_used_its_own_command(client, engine):
    j = launch(client, CMD).get_json()
    assert j["model"] == "test-8b-q4_k_m" and "its own command" in j["warning"]
