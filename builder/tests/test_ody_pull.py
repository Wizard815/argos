"""Pulling Odysseus's Cookbook presets into Argos profiles."""
import pytest

from src.command import parse_command


def _presets(monkeypatch, presets):
    from src.routes import odysseus_hub
    monkeypatch.setattr(odysseus_hub, "cookbook_presets", lambda: {"presets": presets, "error": ""})


GOOD = {"label": "qwen3 fast", "cmd": 'llama-server -m /m/Qwen3-8B-Q4_K_M.gguf --host 0.0.0.0 --port 8080 -ngl 99 -c 8192'}


def test_a_preset_becomes_a_profile(client, app, monkeypatch):
    _presets(monkeypatch, [GOOD])
    out = client.post("/api/ody/pull").get_json()
    assert out["ok"] and len(out["imported"]) == 1, out
    assert out["imported"][0]["label"] == "qwen3 fast"

    profiles = [p for p in app.extensions["pb"].store.list_profiles() if p["name"] == "qwen3 fast"]
    assert len(profiles) == 1
    p = profiles[0]
    assert p["source"] == "odysseus" and p["model_id"] == "qwen3-8b-q4_k_m"
    assert "Qwen3-8B-Q4_K_M.gguf" in p["launch_command"]
    # and it is a command the builder can actually use
    assert parse_command(p["launch_command"]).summary()["model"].endswith(".gguf")


def test_pulling_twice_changes_nothing(client, app, monkeypatch):
    _presets(monkeypatch, [GOOD])
    client.post("/api/ody/pull")
    out = client.post("/api/ody/pull").get_json()
    assert out["imported"] == [] and out["updated"] == []
    assert out["skipped"] and out["skipped"][0]["reason"] == "already here, unchanged"
    assert len([p for p in app.extensions["pb"].store.list_profiles() if p["name"] == "qwen3 fast"]) == 1


def test_an_edit_in_odysseus_wins(client, app, monkeypatch):
    _presets(monkeypatch, [GOOD])
    client.post("/api/ody/pull")
    changed = {**GOOD, "cmd": GOOD["cmd"].replace("-c 8192", "-c 16384")}
    _presets(monkeypatch, [changed])
    out = client.post("/api/ody/pull").get_json()
    assert len(out["updated"]) == 1 and out["imported"] == []
    p = [x for x in app.extensions["pb"].store.list_profiles() if x["name"] == "qwen3 fast"][0]
    assert "-c 16384" in p["launch_command"]


def test_a_preset_argos_cannot_run_is_reported_with_its_reason(client, app, monkeypatch):
    """A silent zero is what made this look like it did nothing at all."""
    _presets(monkeypatch, [
        {"label": "shell one", "cmd": "llama-server -m $(find /m -name '*.gguf' | head -1)"},
        {"label": "no file", "cmd": "llama-server --host 0.0.0.0 -ngl 99"},
        {"label": "empty", "cmd": ""},
    ])
    out = client.post("/api/ody/pull").get_json()
    assert out["imported"] == [] and out["updated"] == []
    assert [f["label"] for f in out["failed"]] == ["shell one", "no file", "empty"], out["failed"]
    assert all(f["reason"] for f in out["failed"])


def test_an_engine_that_cannot_be_reached_says_so(client, app, monkeypatch):
    from src.routes import odysseus_hub
    monkeypatch.setattr(odysseus_hub, "cookbook_presets", lambda: {"presets": [], "error": "connection refused"})
    r = client.post("/api/ody/pull")
    assert r.status_code == 502 and "connection refused" in r.get_json()["error"]


def test_nothing_to_pull_says_that_too(client, app, monkeypatch):
    _presets(monkeypatch, [])
    out = client.post("/api/ody/pull").get_json()
    assert out["ok"] and "no Cookbook presets" in out["note"]
