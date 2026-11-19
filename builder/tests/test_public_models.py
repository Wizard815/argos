"""What apps see: classifiers found as models, and a base model shown to apps by hand (Models -> ⋯)."""
import os

from src import library
from src.integrations import engine_api
from src.library import scan_models

from .ggufs import kv_str, kv_u32, llama_kvs, write_gguf


def test_PublicModels_a_decision_model_is_a_classifier(tmp_path):
    os.makedirs(tmp_path / "jev")
    write_gguf(str(tmp_path / "jev" / "OpenJev-Q8_0.gguf"),
               [kv_str("general.architecture", "qwen35"), kv_u32("qwen35.block_count", 64), kv_str("qwen35.decision.type", "openjev")]
               + llama_kvs()[1:], size=10_000_000)
    write_gguf(str(tmp_path / "jev" / "Qwen3-8B-Q4_K_M.gguf"), llama_kvs(), size=10_000_000)
    library.forget_scans()
    models = {m["name"]: m for m in scan_models(str(tmp_path))}
    assert (models["OpenJev-Q8_0"]["kind"], models["OpenJev-Q8_0"]["decision"]) == ("classifier", "openjev")
    assert (models["Qwen3-8B-Q4_K_M"]["kind"], models["Qwen3-8B-Q4_K_M"]["decision"]) == ("chat", "")


def test_PublicModels_show_a_base_model_to_apps(client, monkeypatch):
    listed, calls = set(), []
    monkeypatch.setattr(engine_api, "listed_models", lambda url, timeout=3.0: sorted(listed))

    def put(url, mid, on, timeout=10.0):
        calls.append((mid, on))
        (listed.add if on else listed.discard)(mid)
    monkeypatch.setattr(engine_api, "set_model_listed", put)
    client.put("/api/settings/integrations", json={"engine_url": "http://127.0.0.1:9"})

    assert client.get("/api/library/listed").get_json()["listed"] == []
    r = client.put("/api/library/listed/openjev-q8_0", json={"listed": True}).get_json()
    assert r["listed"] == ["openjev-q8_0"] and calls == [("openjev-q8_0", True)]
    r = client.put("/api/library/listed/openjev-q8_0", json={"listed": False}).get_json()
    assert r["listed"] == []
