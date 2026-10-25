"""The Builder picks card numbers; the server decides at launch which variable the GPU runtime reads."""
import pytest

from src import gpu_pick
from src.command import parse_command
from src.gpu_pick import resolve, resolve_command


@pytest.fixture
def machine(monkeypatch):
    def has(*backends):
        monkeypatch.setattr(gpu_pick, "_local", tuple(backends) or ("rocm", "cuda"))
    return has


@pytest.mark.parametrize("found, expect", [
    (("rocm",), "HIP_VISIBLE_DEVICES=0,1 llama-server -m /m/a.gguf"),
    (("cuda",), "CUDA_VISIBLE_DEVICES=0,1 llama-server -m /m/a.gguf"),
    (("rocm", "cuda"), "HIP_VISIBLE_DEVICES=0,1 CUDA_VISIBLE_DEVICES=0,1 llama-server -m /m/a.gguf"),
])
def test_GpuPick_this_machine_sets_what_it_has(machine, found, expect):
    machine(*found)
    assert resolve_command("ARGOS_GPUS=0,1 llama-server -m /m/a.gguf") == expect


def test_GpuPick_a_remote_launcher_names_its_backend(machine):
    machine("rocm", "cuda")
    assert resolve_command("ARGOS_GPUS=1 /app/bin/remote-llama cuda --model /m/a.gguf") == \
        "CUDA_VISIBLE_DEVICES=1 /app/bin/remote-llama cuda --model /m/a.gguf"
    assert resolve_command("ARGOS_GPUS=0 /app/bin/remote-llama rocm --model /m/a.gguf").startswith("HIP_VISIBLE_DEVICES=0 ")


def test_GpuPick_a_variable_set_by_hand_wins_and_the_pick_never_reaches_the_process(machine):
    machine("rocm")
    cmd = resolve(parse_command("ARGOS_GPUS=0,1 HIP_VISIBLE_DEVICES=1 llama-server -m /m/a.gguf"))
    assert cmd.env == {"HIP_VISIBLE_DEVICES": "1"}
    assert resolve_command("ARGOS_GPUS= llama-server -m /m/a.gguf") == "llama-server -m /m/a.gguf"
    assert resolve_command("llama-server -m /m/a.gguf") == "llama-server -m /m/a.gguf"


def test_GpuPick_the_builder_is_told_the_variables_and_the_engine_gets_them(app, client, machine):
    machine("rocm")
    j = client.post("/api/command/parse", json={"command": "ARGOS_GPUS=1 llama-server -m /m/a.gguf", "format": "plain"}).get_json()
    assert j["gpu_vars"] == ["HIP_VISIBLE_DEVICES"] and j["env"] == {"ARGOS_GPUS": "1"}, "saved as the pick"
    client.post("/api/profiles", json={"name": "p", "base_model": "a", "launch_command": "ARGOS_GPUS=1 llama-server --model /m/a.gguf --port 8080"})
    ours = [x for x in client.get("/api/cookbook/state").get_json()["presets"] if (x.get("fields") or {}).get("source") == "argos"]
    assert "HIP_VISIBLE_DEVICES=1" in ours[0]["cmd"] and "ARGOS_GPUS" not in ours[0]["cmd"], "the engine starts it on card 1"


def test_GpuPick_a_launch_here_gets_the_runtime_variable(app, client, machine):
    from .conftest import fake_command
    machine("cuda")
    r = client.post("/api/launches", json={"command": "ARGOS_GPUS=0 " + fake_command()}).get_json()
    launched = r["launch"]["command"]
    client.post(f"/api/launches/{r['launch']['launch_id']}/stop")
    assert launched.startswith("CUDA_VISIBLE_DEVICES=0 ") and "ARGOS_GPUS" not in launched


@pytest.mark.parametrize("names, expect", [
    (["AMD Instinct MI50", "AMD Instinct MI50"], ("rocm",)),
    (["NVIDIA GeForce RTX 3060"], ("cuda",)),
    (["AMD Instinct MI50", "NVIDIA GeForce RTX 3060"], ("rocm", "cuda")),
    ([], ("rocm", "cuda")),
])
def test_GpuPick_with_no_device_files_the_card_names_decide(monkeypatch, names, expect):
    monkeypatch.setattr(gpu_pick.os.path, "exists", lambda p: False)
    monkeypatch.setattr(gpu_pick.shutil, "which", lambda p: None)
    monkeypatch.setattr(gpu_pick, "_local", None)
    monkeypatch.setattr(gpu_pick, "_names", ())
    gpu_pick.note_cards(names)
    assert gpu_pick.local_backends() == expect


def test_GpuPick_telemetry_teaches_it_the_cards(app, client, monkeypatch):
    monkeypatch.setattr(gpu_pick.os.path, "exists", lambda p: False)
    monkeypatch.setattr(gpu_pick.shutil, "which", lambda p: None)
    monkeypatch.setattr(gpu_pick, "_local", None)
    monkeypatch.setattr(gpu_pick, "_names", ())
    store = app.extensions["pb"].store
    store.add_gpu_samples([{"ts": 1.0, "gpu": 0, "name": "AMD Instinct MI50", "util": 0, "mem_used_mb": 0, "mem_total_mb": 32768,
                            "temp_c": 30, "power_w": 20, "source": "test"}])
    client.get("/api/gpu/latest")
    j = client.post("/api/command/parse", json={"command": "ARGOS_GPUS=0,1 llama-server -m /m/a.gguf", "format": "plain"}).get_json()
    assert j["gpu_vars"] == ["HIP_VISIBLE_DEVICES"]
