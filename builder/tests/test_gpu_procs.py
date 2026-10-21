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
