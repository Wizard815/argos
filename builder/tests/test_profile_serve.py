"""A client asking for a model's plain name (Hermes, say) gets the profile picked to serve it."""
from src.routes import core

CMD = 'llama-server --model "/m/Test-8B-Q4_K_M.gguf" --port ${PORT} -ngl 99 -c 126500 --split-mode tensor'


def test_ProfileServe_lists_names_and_switches_the_engine(app, client, monkeypatch):
    client.put("/api/settings/integrations", json={"engine_url": "http://engine.test"})
    engine = {"chosen": "", "labels": ["long"], "sets": []}
    monkeypatch.setattr(core.engine_api, "model_profile", lambda url, m, timeout=3.0: {"labels": engine["labels"], "chosen": engine["chosen"]})

    def put(url, m, label, timeout=10.0):
        engine["sets"].append((m, label))
        engine["chosen"] = label or ""
    monkeypatch.setattr(core.engine_api, "set_model_profile", put)
    pid = client.post("/api/profiles", json={"name": "long", "base_model": "Test-8B", "launch_command": CMD}).get_json()["profile"]["profile_id"]

    served = client.get("/api/profiles/served").get_json()["profiles"][pid]
    assert served == {"variant": "test-8b-q4_k_m--long", "model": "test-8b-q4_k_m", "serving": False}
    assert client.put(f"/api/profiles/{pid}/serve", json={"on": True}).get_json()["serving"] is True
    assert client.get("/api/profiles/served").get_json()["profiles"][pid]["serving"] is True
    client.put(f"/api/profiles/{pid}/serve", json={"on": False})
    assert engine["sets"] == [("test-8b-q4_k_m", "long"), ("test-8b-q4_k_m", None)]


def test_ProfileServe_turning_off_leaves_another_profiles_choice_alone(app, client, monkeypatch):
    client.put("/api/settings/integrations", json={"engine_url": "http://engine.test"})
    sets = []
    monkeypatch.setattr(core.engine_api, "model_profile", lambda url, m, timeout=3.0: {"labels": ["long", "other"], "chosen": "other"})
    monkeypatch.setattr(core.engine_api, "set_model_profile", lambda url, m, label, timeout=10.0: sets.append(label))
    pid = client.post("/api/profiles", json={"name": "long", "base_model": "Test-8B", "launch_command": CMD}).get_json()["profile"]["profile_id"]
    client.put(f"/api/profiles/{pid}/serve", json={"on": False})
    assert sets == [], "this profile was not the one serving"


def test_ProfileServe_needs_the_engine(app, client):
    pid = client.post("/api/profiles", json={"name": "long", "base_model": "Test-8B", "launch_command": CMD}).get_json()["profile"]["profile_id"]
    r = client.put(f"/api/profiles/{pid}/serve", json={"on": True})
    assert r.status_code == 400 and "engine" in r.get_json()["error"]
