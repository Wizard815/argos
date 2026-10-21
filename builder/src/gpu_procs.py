"""Which processes hold GPU memory, so memory nobody can account for can be found and freed.

The Live view adds up the models the engine loaded. The card's own reading is higher whenever anything
else is on it: a llama-server another container started, a model left over from a crashed run, a
desktop. This asks the driver tools who is really there. It reads whatever the machine has:
nvidia-smi, rocm-smi, or on Linux the processes holding /dev/kfd. It sees other containers' processes
only when the builder shares the host's PID namespace (``pid: host``); otherwise it sees its own.
"""
from __future__ import annotations

import os
import re
import shutil

from . import gputools
import signal
import subprocess
import sys
from typing import Optional

GIB = 2 ** 30
PORT = re.compile(r"--port[= ](\d+)")


def _run(argv: list[str], timeout: float = 8.0) -> str:
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError):
        return ""
    return r.stdout or ""


def _nvidia() -> Optional[list[dict]]:
    exe = gputools.tool("nvidia-smi")
    if not exe:
        return None
    index = {}
    for line in _run([exe, "--query-gpu=index,uuid", "--format=csv,noheader"]).splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) == 2:
            index[parts[1]] = parts[0]
    out = []
    for line in _run([exe, "--query-compute-apps=pid,process_name,used_gpu_memory,gpu_uuid", "--format=csv,noheader,nounits"]).splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 4 or not parts[0].isdigit():
            continue
        used = float(parts[2]) / 1024 if re.fullmatch(r"\d+(\.\d+)?", parts[2]) else None  # MiB -> GiB; N/A on Windows
        out.append({"pid": int(parts[0]), "name": os.path.basename(parts[1]), "gpu": index.get(parts[3], parts[3]),
                    "vram_gib": round(used, 2) if used is not None else None})
    return out


def _rocm() -> Optional[list[dict]]:
    exe = gputools.tool("rocm-smi")
    if not exe:
        return None
    out = []
    for line in _run([exe, "--showpids"]).splitlines():
        t = line.split()
        # PID  NAME  GPU(s)  VRAM USED (bytes)  SDMA USED  CU OCCUPANCY
        if len(t) >= 6 and t[0].isdigit() and all(x.isdigit() for x in t[-3:]):
            out.append({"pid": int(t[0]), "name": " ".join(t[1:-4]) or t[1], "gpu": t[-4],
                        "vram_gib": round(int(t[-3]) / GIB, 2)})
    return out


def _kfd() -> list[dict]:
    """Linux without a usable rocm-smi: every process with the compute device open."""
    out = []
    try:
        pids = [p for p in os.listdir("/proc") if p.isdigit()]
    except OSError:
        return out
    for pid in pids:
        try:
            for fd in os.listdir(f"/proc/{pid}/fd"):
                try:
                    if os.readlink(f"/proc/{pid}/fd/{fd}") == "/dev/kfd":
                        with open(f"/proc/{pid}/comm", encoding="utf-8", errors="replace") as f:
                            out.append({"pid": int(pid), "name": f.read().strip(), "gpu": "?", "vram_gib": None})
                        break
                except OSError:
                    continue
        except OSError:
            continue
    return out


def _commands(pids: list[int]) -> dict[int, str]:
    """The command line of each process (best effort: Linux /proc, or PowerShell on Windows)."""
    out: dict[int, str] = {}
    if sys.platform == "win32":
        if not pids:
            return out
        ps = ("Get-CimInstance Win32_Process | Where-Object { @(" + ",".join(map(str, pids)) +
              ") -contains $_.ProcessId } | ForEach-Object { \"$($_.ProcessId)`t$($_.CommandLine)\" }")
        for line in _run(["powershell", "-NoProfile", "-Command", ps], 15).splitlines():
            pid, _, cmd = line.partition("\t")
            if pid.strip().isdigit():
                out[int(pid)] = cmd.strip()
        return out
    for pid in pids:
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                out[pid] = f.read().replace(b"\0", b" ").decode("utf-8", "replace").strip()
        except OSError:
            pass
    return out


def listing(engine_running: list[dict], launches: list[dict]) -> dict:
    """Processes on the GPUs, each tagged with who started it when that can be told.

    ``engine_running``: the engine's /running rows (model, cmd); ``launches``: this app's own launches
    (profile_name or base_model, port). A llama-server is matched to them by its --port.
    """
    tool = "nvidia-smi"
    procs = _nvidia()
    if procs is None:
        tool, procs = "rocm-smi", _rocm()
    if procs is None or (not procs and sys.platform.startswith("linux")):
        found = _kfd()
        if found or procs is None:
            tool, procs = "/dev/kfd", found
    if procs is None:
        return {"tool": None, "processes": [], "note": "No GPU tool found here (nvidia-smi or rocm-smi)."}
    cmds = _commands([p["pid"] for p in procs])
    by_port: dict[str, str] = {}
    for r in engine_running:
        m = PORT.search(r.get("cmd") or "")
        if m:
            by_port[m.group(1)] = f"model {r['model']}"
    for l in launches:
        if l.get("port") and l.get("running"):
            by_port[str(l["port"])] = f"launched here: {l.get('profile_name') or l.get('base_model') or l.get('launch_id')}"
    for p in procs:
        p["cmd"] = cmds.get(p["pid"], "")
        m = PORT.search(p["cmd"])
        p["owner"] = by_port.get(m.group(1)) if m else None
        p["self"] = p["pid"] == os.getpid()
    note = ""
    if not procs:
        note = ("No processes reported. If the card still shows used memory, the processes are in another container: "
                "run the builder with the host's PID namespace (pid: host) so they are visible here.")
    return {"tool": tool, "processes": sorted(procs, key=lambda p: -(p["vram_gib"] or 0)), "note": note}


def kill(pid: int, allowed: set[int], force: bool = False) -> str:
    """Stop one process the listing showed. Refuses anything else, this process and PID 1."""
    if pid not in allowed:
        raise PermissionError("that process is not using a GPU as far as this app can see")
    if pid in (os.getpid(), 1):
        raise PermissionError("refusing to stop this app or PID 1")
    sig = signal.SIGKILL if force and hasattr(signal, "SIGKILL") else signal.SIGTERM
    try:
        os.kill(pid, sig)
    except ProcessLookupError:
        return "already gone"
    return "killed" if force else "asked to stop"
