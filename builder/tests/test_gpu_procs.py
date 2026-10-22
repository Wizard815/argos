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


def test_a_process_outside_the_container_is_never_signalled(app, client, monkeypatch):
    monkeypatch.setattr(gpu_procs, "listing", lambda running, launches: {"tool": "kernel (KFD)", "note": "", "processes": [
        {"pid": 550406, "host_pid": 550406, "visible": False, "name": "not visible from Argos", "vram_gib": 3.2, "owner": None}]})
    killed = []
    monkeypatch.setattr(gpu_procs, "kill", lambda *a, **k: killed.append(a))
    r = client.post("/api/gpu/processes/550406/kill", json={"hard": True})
    assert r.status_code == 409 and "kill 550406" in r.get_json()["error"] and killed == []
