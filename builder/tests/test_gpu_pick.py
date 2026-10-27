"""The Builder picks cards; the command gets the variable the picked cards' runtime reads."""
import pytest

from src import gpu_pick
from src.command import parse_command
from src.gpu_pick import apply_pick, resolve, resolve_command

MI50 = "AMD Instinct MI50"
RTX = "NVIDIA GeForce RTX 3060"


@pytest.fixture
def machine(monkeypatch):
    """No device files; the cards are what the telemetry saw: machine(MI50, MI50) numbers them 0, 1."""
    monkeypatch.setattr(gpu_pick.os.path, "exists", lambda p: False)
    monkeypatch.setattr(gpu_pick.shutil, "which", lambda p: None)
    monkeypatch.setattr(gpu_pick, "_local", None)
    monkeypatch.setattr(gpu_pick, "_cards", {})

    def cards(*names):
        gpu_pick.note_cards([{"gpu": i, "name": n} for i, n in enumerate(names)])
    return cards


def pin(command, cards):
    return apply_pick(parse_command(command), cards).to_string()


@pytest.mark.parametrize("names, pick, expect", [
    ((MI50, MI50), ["0", "1"], "HIP_VISIBLE_DEVICES=0,1 llama-server -m /m/a.gguf"),
    ((RTX,), ["0"], "CUDA_VISIBLE_DEVICES=0 llama-server -m /m/a.gguf"),
    ((MI50, RTX), ["1"], "CUDA_VISIBLE_DEVICES=1 llama-server -m /m/a.gguf"),
    ((MI50, RTX), ["0"], "HIP_VISIBLE_DEVICES=0 llama-server -m /m/a.gguf"),
    ((), ["0"], "HIP_VISIBLE_DEVICES=0 CUDA_VISIBLE_DEVICES=0 llama-server -m /m/a.gguf"),
])
def test_GpuPick_the_picked_cards_decide_the_variable(machine, names, pick, expect):
    machine(*names)
    assert pin("llama-server -m /m/a.gguf", pick) == expect


def test_GpuPick_a_new_pick_replaces_the_old_one_and_none_removes_it(machine):
    machine(MI50, MI50)
    assert pin("CUDA_VISIBLE_DEVICES=0 ARGOS_GPUS=1 llama-server -m /m/a.gguf", ["1"]) == "HIP_VISIBLE_DEVICES=1 llama-server -m /m/a.gguf"
    assert pin("HIP_VISIBLE_DEVICES=1 llama-server -m /m/a.gguf", []) == "llama-server -m /m/a.gguf"


def test_GpuPick_a_remote_launcher_names_its_backend(machine):
    machine(MI50, MI50)
    assert pin("/app/bin/remote-llama cuda --model /m/a.gguf", ["1"]) == "CUDA_VISIBLE_DEVICES=1 /app/bin/remote-llama cuda --model /m/a.gguf"


def test_GpuPick_with_device_files_this_machine_decides_for_unknown_cards(monkeypatch):
    monkeypatch.setattr(gpu_pick, "_cards", {})
    monkeypatch.setattr(gpu_pick, "_local", ("rocm",))
    assert pin("llama-server -m /m/a.gguf", ["0"]) == "HIP_VISIBLE_DEVICES=0 llama-server -m /m/a.gguf"


def test_GpuPick_an_old_argos_gpus_becomes_the_real_variable(machine):
    machine(MI50, MI50)
    assert resolve_command("ARGOS_GPUS=0,1 llama-server -m /m/a.gguf") == "HIP_VISIBLE_DEVICES=0,1 llama-server -m /m/a.gguf"
    cmd = resolve(parse_command("ARGOS_GPUS=0,1 HIP_VISIBLE_DEVICES=1 llama-server -m /m/a.gguf"))
    assert cmd.env == {"HIP_VISIBLE_DEVICES": "1"}, "a variable set by hand wins"
    assert resolve_command("llama-server -m /m/a.gguf") == "llama-server -m /m/a.gguf"


def test_GpuPick_the_builder_writes_the_real_variable(app, client, machine):
    machine(MI50, MI50)
    j = client.post("/api/command/build", json={"command": "llama-server -m /m/a.gguf", "format": "plain", "gpus": ["0", "1"]}).get_json()
    assert j["command"] == "HIP_VISIBLE_DEVICES=0,1 llama-server -m /m/a.gguf" and j["gpu_vars"] == ["HIP_VISIBLE_DEVICES"]
    j = client.post("/api/command/build", json={"command": j["command"], "format": "plain", "gpus": []}).get_json()
    assert j["command"] == "llama-server -m /m/a.gguf"
    j = client.post("/api/command/build", json={"command": "HIP_VISIBLE_DEVICES=1 llama-server -m /m/a.gguf", "format": "plain",
                                                "edits": {"ctx-size": 8192}}).get_json()
    assert j["env"] == {"HIP_VISIBLE_DEVICES": "1"}, "an edit without gpus leaves the pin alone"


def test_GpuPick_an_old_profile_opens_with_the_real_variable(app, client, machine):
    machine(MI50, MI50)
    j = client.post("/api/command/parse", json={"command": "ARGOS_GPUS=1 llama-server -m /m/a.gguf", "format": "plain"}).get_json()
    assert j["env"] == {"HIP_VISIBLE_DEVICES": "1"} and j["gpu_vars"] == ["HIP_VISIBLE_DEVICES"]


def test_GpuPick_old_profiles_still_launch_and_export_right(app, client, machine):
    from .conftest import fake_command
    machine(RTX)
    client.post("/api/profiles", json={"name": "p", "base_model": "a", "launch_command": "ARGOS_GPUS=0 llama-server --model /m/a.gguf --port 8080"})
    ours = [x for x in client.get("/api/cookbook/state").get_json()["presets"] if (x.get("fields") or {}).get("source") == "argos"]
    assert "CUDA_VISIBLE_DEVICES=0" in ours[0]["cmd"] and "ARGOS_GPUS" not in ours[0]["cmd"]
    r = client.post("/api/launches", json={"command": "ARGOS_GPUS=0 " + fake_command()}).get_json()
    launched = r["launch"]["command"]
    client.post(f"/api/launches/{r['launch']['launch_id']}/stop")
    assert launched.startswith("CUDA_VISIBLE_DEVICES=0 ") and "ARGOS_GPUS" not in launched


def test_GpuPick_telemetry_teaches_it_the_cards(app, client, machine):
    store = app.extensions["pb"].store
    store.add_gpu_samples([{"ts": 1.0, "gpu": g, "name": MI50, "util": 0, "mem_used_mb": 0, "mem_total_mb": 32768,
                            "temp_c": 30, "power_w": 20, "source": "test"} for g in (0, 1)])
    client.get("/api/gpu/latest")
    j = client.post("/api/command/build", json={"command": "llama-server -m /m/a.gguf", "format": "plain", "gpus": ["1"]}).get_json()
    assert j["command"].startswith("HIP_VISIBLE_DEVICES=1 ")


def test_GpuPick_cpu_only_hides_every_gpu_and_offloads_nothing(app, client, machine):
    machine(MI50, MI50)
    j = client.post("/api/command/build", json={"command": "llama-server -m /m/a.gguf -ngl 99", "format": "plain",
                                                "gpus": ["-1"], "edits": {"n-gpu-layers": "0"}}).get_json()
    assert j["command"] == "HIP_VISIBLE_DEVICES=-1 llama-server -m /m/a.gguf -ngl 0"
    j = client.post("/api/command/build", json={"command": j["command"], "format": "plain", "gpus": ["1"],
                                                "edits": {"n-gpu-layers": "99"}}).get_json()
    assert j["command"] == "HIP_VISIBLE_DEVICES=1 llama-server -m /m/a.gguf -ngl 99", "a card again: back on the GPU"
