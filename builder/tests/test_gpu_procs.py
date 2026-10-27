import pytest

from src import gpu_procs

ROCM = """
======================= ROCm System Management Interface =======================
=================================== KFD Processes ==============================
KFD process information:
PID     PROCESS NAME    GPU(s)  VRAM USED       SDMA USED       CU OCCUPANCY
4242    llama-server    1       2561241088      0               0
77      python3         2       1073741824      0               0
================================================================================
"""


def test_rocm_showpids_is_parsed(monkeypatch):
    monkeypatch.setattr(gpu_procs.gputools, "tool", lambda name: "rocm-smi" if name == "rocm-smi" else "")
    monkeypatch.setattr(gpu_procs, "_run", lambda argv, timeout=8.0: ROCM)
    monkeypatch.setattr(gpu_procs, "_commands", lambda pids: {4242: "llama-server -m /m/x.gguf --port 5801"})
    out = gpu_procs.listing([{"model": "qwen-embed", "cmd": "llama-server -m /m/x.gguf --port 5801"}], [])
    assert out["tool"] == "rocm-smi"
    a, b = out["processes"]
    assert (a["pid"], a["name"], a["gpu"], a["vram_gib"], a["owner"]) == (4242, "llama-server", "1", 2.39, "model qwen-embed")
    assert b["pid"] == 77 and b["owner"] is None  # not started by Argos: the one to look at


def test_kill_refuses_anything_not_on_a_gpu_and_pid_one():
    with pytest.raises(PermissionError):
        gpu_procs.kill(123, {456})
    with pytest.raises(PermissionError):
        gpu_procs.kill(1, {1})


def _fake_kfd(root, procs, gpus=("0", "4521", "8840")):
    import os
    for i, gid in enumerate(gpus):
        os.makedirs(os.path.join(root, "topology", "nodes", str(i)))
        with open(os.path.join(root, "topology", "nodes", str(i), "gpu_id"), "w") as f:
            f.write(gid)
    for pid, files in procs.items():
        d = os.path.join(root, "proc", str(pid))
        os.makedirs(os.path.join(d, "stats_4521"))
        for name, value in files.items():
            with open(os.path.join(d, name), "w") as f:
                f.write(str(value))


def test_kfd_shows_gpu_memory_of_processes_argos_cannot_see(tmp_path, monkeypatch):
    root = str(tmp_path / "kfd")
    _fake_kfd(root, {550406: {"vram_4521": 3482193920, "vram_8840": 0, "sdma_4521": 0, "stats_4521/cu_occupancy": 0}})
    monkeypatch.setattr(gpu_procs, "_host_pids", lambda: {})
    (p,) = gpu_procs.kfd_processes(root, sample_s=0)
    # card 0 is the first GPU node (node 0 is the CPU), the numbering rocm-smi and HIP use
    assert (p["host_pid"], p["gpu"], p["vram_gib"], p["visible"], p["busy"]) == (550406, "0", 3.24, False, False)
    assert gpu_procs.kfd_processes(str(tmp_path / "none"), sample_s=0) is None


def test_kfd_names_the_cards_by_memory_when_the_topology_hides_the_gpus(tmp_path, monkeypatch):
    G = 2 ** 30
    root = str(tmp_path / "kfd")
    # a container without the GPU devices: KFD lists the processes, but its topology only the CPU node
    _fake_kfd(root, {3036952: {"vram_14886": 20 * G, "vram_30964": 1 * G}, 77: {"vram_30964": 18 * G}}, gpus=("0",))
    monkeypatch.setattr(gpu_procs, "_host_pids", lambda: {})
    monkeypatch.setattr(gpu_procs, "_drm_used", lambda root=None: [int(19.3 * G), 21 * G])
    procs = {p["host_pid"]: p for p in gpu_procs.kfd_processes(root, sample_s=0)}
    assert procs[3036952]["gpu"] == "0,1" and procs[77]["gpu"] == "0"
    assert procs[3036952]["vram_gib"] == 21.0
    # cards that do not add up to the ids: left as the kernel names them
    monkeypatch.setattr(gpu_procs, "_drm_used", lambda root=None: [1])
    assert {p["gpu"] for p in gpu_procs.kfd_processes(root, sample_s=0)} == {"14886,30964", "30964"}


def test_a_process_outside_the_container_is_never_signalled(app, client, monkeypatch):
    monkeypatch.setattr(gpu_procs, "listing", lambda running, launches, pool=None: {"tool": "kernel (KFD)", "note": "", "processes": [
        {"pid": 550406, "host_pid": 550406, "visible": False, "name": "not visible from Argos", "vram_gib": 3.2, "owner": None}]})
    killed = []
    monkeypatch.setattr(gpu_procs, "kill", lambda *a, **k: killed.append(a))
    r = client.post("/api/gpu/processes/550406/kill", json={"hard": True})
    assert r.status_code == 409 and "kill 550406" in r.get_json()["error"] and killed == []


def test_a_dry_run_says_what_would_happen_and_signals_nothing(app, client, monkeypatch):
    row = {"pid": 4242, "visible": True, "name": "llama-server", "vram_gib": 3.3, "owner": None, "cmd": "llama-server --port 8081"}
    monkeypatch.setattr(gpu_procs, "listing", lambda running, launches, pool=None: {"tool": "x", "note": "", "processes": [row]})
    killed = []
    monkeypatch.setattr(gpu_procs, "kill", lambda *a, **k: killed.append(a))
    monkeypatch.setattr(gpu_procs.os, "kill", lambda *a: killed.append(a))
    for hard in (False, True):
        out = client.post("/api/gpu/processes/4242/kill", json={"dry_run": True, "hard": hard}).get_json()
        assert out["dry_run"] and out["allowed"] and "PID 4242" in out["would"] and "3.3 GiB" in out["would"]
    assert killed == []


def _hidden(monkeypatch, root, procs):
    """KFD sees these processes; none of them is in this container (a llama.cpp sidecar's, over SSH)."""
    _fake_kfd(root, procs)
    monkeypatch.setattr(gpu_procs, "KFD", root)
    monkeypatch.setattr(gpu_procs, "_nvidia", lambda: None)
    monkeypatch.setattr(gpu_procs, "_host_pids", lambda: {})
    real = gpu_procs.kfd_processes
    monkeypatch.setattr(gpu_procs, "kfd_processes", lambda: real(root, sample_s=0))


GIB_B = 1 << 30


def test_a_model_started_over_ssh_is_named_by_its_cards_and_memory(tmp_path, monkeypatch):
    _hidden(monkeypatch, str(tmp_path / "kfd"), {
        2420766: {"vram_4521": 30 * GIB_B, "vram_8840": int(29.3 * GIB_B), "sdma_4521": 0, "stats_4521/cu_occupancy": 0},
        777: {"vram_4521": 2 * GIB_B, "sdma_4521": 0, "stats_4521/cu_occupancy": 0},
    })
    running = [{"model": "qwen-coder--256", "cmd": "/app/bin/remote-llama rocm --port 5891 -m /app/models/q.gguf"},
               {"model": "embed", "cmd": "llama-server --port 5802"}]
    pool = [{"id": "qwen-coder--256", "size_gib": 58.0, "cards": ["0", "1"]}, {"id": "embed", "size_gib": 0.6, "cards": ["0"]}]
    out = gpu_procs.listing(running, [], pool)
    big = next(p for p in out["processes"] if p["host_pid"] == 2420766)
    assert big["owner"] == "model qwen-coder--256" and big["matched"] == "by cards and memory" and big["argos"]
    assert big["cmd"].startswith("/app/bin/remote-llama"), "the engine's command for it, since its own is unreadable"
    small = next(p for p in out["processes"] if p["host_pid"] == 777)
    assert small["owner"] is None, "2 GiB is not a 0.6 GiB model: it stays unknown"
    assert "1 process(es) are outside" in out["note"], "only the unknown one is called out"


def test_a_hidden_process_on_other_cards_is_not_taken_for_a_model(tmp_path, monkeypatch):
    _hidden(monkeypatch, str(tmp_path / "kfd"), {9: {"vram_8840": 8 * GIB_B, "sdma_4521": 0, "stats_4521/cu_occupancy": 0}})
    out = gpu_procs.listing([{"model": "m", "cmd": "remote-llama rocm --port 1"}], [], [{"id": "m", "size_gib": 8, "cards": ["0"]}])
    assert out["processes"][0]["owner"] is None, "the model is on card 0, the process on card 1"


def test_an_engine_model_in_another_container_is_unloaded_not_killed(app, client, monkeypatch):
    monkeypatch.setattr(gpu_procs, "listing", lambda running, launches, pool=None: {"tool": "kernel (KFD)", "note": "", "processes": [
        {"pid": 2420766, "host_pid": 2420766, "visible": False, "name": "not visible from Argos", "vram_gib": 59.3,
         "owner": "model qwen-coder--256", "matched": "by cards and memory"}]})
    r = client.post("/api/gpu/processes/2420766/kill", json={"hard": True})
    assert r.status_code == 409 and "unload it from Schedule" in r.get_json()["error"]
