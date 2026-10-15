"""Launching when llama.cpp is not in this container: the engine takes it, or the
default server names the launcher."""
from types import SimpleNamespace

from src.library import prefab_command, prefab_with_launcher
from src.servers import launcher_for
from src.settings import env_defaults

PREFAB = env_defaults({})["prefab"]
CHAT = {"path": "/m/x.gguf", "kind": "chat", "mmproj": []}


def test_a_remote_server_names_its_backend_word():
    assert launcher_for({"kind": "ssh", "host": "mx-llamacpp-ssh", "gpu": "rocm"}) == "/app/bin/remote-llama rocm"
    assert launcher_for({"kind": "ssh", "host": "box", "gpu": "cuda"}) == "/app/bin/remote-llama cuda"


def test_the_local_machine_and_unknown_backends_need_no_launcher():
    assert launcher_for({"kind": "local", "host": "", "gpu": "auto"}) == ""
    assert launcher_for({"kind": "ssh", "host": "box", "gpu": "none"}) == ""
    assert launcher_for({}) == ""


def test_an_unset_prefab_program_takes_the_default_servers_launcher():
    eff = prefab_with_launcher(PREFAB, "/app/bin/remote-llama rocm")
    assert not (PREFAB.get("program") or "").strip(), "the default prefab ships no program"

    cmd = prefab_command(CHAT, eff)
    assert cmd.program == "/app/bin/remote-llama"
    text = cmd.to_string(style="odysseus")
    assert text.startswith("/app/bin/remote-llama rocm --model ")
    assert "--port ${PORT}" in text, "the runner still owns the port"


def test_a_program_set_by_hand_wins_over_the_launcher():
    eff = prefab_with_launcher({**PREFAB, "program": "llama-server"}, "/app/bin/remote-llama rocm")
    assert prefab_command(CHAT, eff).program == "llama-server"


def test_the_engine_takes_a_model_it_already_serves(monkeypatch):
    from src.routes import core
    from src.integrations import engine_api

    monkeypatch.setattr(engine_api, "model_ids", lambda url, timeout=3.0: {"qwen3-0.6b-q8_0"})
    ctx = SimpleNamespace(settings={"integrations": {"engine_url": "http://engine"}})
    assert core._engine_model(ctx, "llama-server -m /m/qwen3-0.6b-q8_0.gguf") == "qwen3-0.6b-q8_0"


def test_a_model_the_engine_does_not_have_is_left_to_the_local_runner(monkeypatch):
    from src.routes import core
    from src.integrations import engine_api

    monkeypatch.setattr(engine_api, "model_ids", lambda url, timeout=3.0: {"something-else"})
    ctx = SimpleNamespace(settings={"integrations": {"engine_url": "http://engine"}})
    assert core._engine_model(ctx, "llama-server -m /m/qwen3-0.6b-q8_0.gguf") == ""


def test_no_engine_and_no_model_path_mean_no_routing(monkeypatch):
    from src.routes import core
    from src.integrations import engine_api

    monkeypatch.setattr(engine_api, "model_ids", lambda url, timeout=3.0: {"qwen3-0.6b-q8_0"})
    assert core._engine_model(SimpleNamespace(settings={"integrations": {"engine_url": ""}}),
                              "llama-server -m /m/qwen3-0.6b-q8_0.gguf") == ""
    ctx = SimpleNamespace(settings={"integrations": {"engine_url": "http://engine"}})
    assert core._engine_model(ctx, "llama-bench -p 512") == ""
