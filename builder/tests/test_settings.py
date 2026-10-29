import pytest

from src.db import Store
from src.settings import Settings, env_defaults


def test_defaults_overrides_and_coercion(tmp_path):
    s = Settings(Store(str(tmp_path / "s.db")), env_defaults({"MODELS_DIR": "/app/models", "HF_TOKEN": "hf_x"}))
    assert s.get("paths")["models_dir"] == "/app/models"
    saved = s.update("telemetry", {"interval_s": "30", "enabled": "false"})
    assert saved["interval_s"] == 30 and saved["enabled"] is False and saved["keep_minutes"] == 120
    assert s.update("assistant", {"temperature": "0.2"})["temperature"] == 0.2
    with pytest.raises(KeyError):
        s.update("telemetry", {"nope": 1})
    with pytest.raises(KeyError):
        s.update("nope", {})
    assert s.public()["downloads"]["hf_token"] == "••••••••"
    s.update("downloads", {"hf_token": "••••••••", "vram_gb": 48})  # masked value comes back unchanged
    assert s.get("downloads")["hf_token"] == "hf_x" and s.get("downloads")["vram_gb"] == 48


def test_settings_routes(client):
    j = client.get("/api/settings").get_json()["settings"]
    assert set(j) == {"paths", "integrations", "always", "odysseus", "memory", "builder", "prefab", "downloads", "telemetry", "assistant", "kv", "gpu", "playground"}
    assert client.put("/api/settings/builder", json={"command_format": "plain"}).get_json()["ok"]
    assert client.post("/api/command/parse", json={"command": "llama-server -m a.gguf"}).get_json()["command"] == \
        "llama-server -m a.gguf"
    assert client.put("/api/settings/builder", json={"x": 1}).status_code == 400
    assert client.put("/api/settings/nope", json={}).status_code == 400
