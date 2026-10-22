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
import signal
import subprocess
import sys
import time
from typing import Optional

from . import gputools

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


KFD = "/sys/class/kfd/kfd"


def _read(path: str) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read().strip()
    except OSError:
        return ""


def _kfd_gpus(root: str = KFD) -> dict[str, str]:
    """KFD gpu_id -> card index (0, 1, ...), in topology order: the numbering rocm-smi and HIP use."""
    nodes = os.path.join(root, "topology", "nodes")
    try:
        names = sorted(os.listdir(nodes), key=lambda n: int(n) if n.isdigit() else 1 << 30)
    except OSError:
        return {}
    out: dict[str, str] = {}
    for n in names:
        gid = _read(os.path.join(nodes, n, "gpu_id"))
        if gid and gid != "0":
            out[gid] = str(len(out))
    return out


def _kfd_sample(root: str) -> dict[int, dict]:
    """host pid -> {"vram": {card: bytes}, "sdma": total bytes, "cu": occupancy} for every process using KFD."""
    gpus = _kfd_gpus(root)
    out: dict[int, dict] = {}
    try:
        pids = [d for d in os.listdir(os.path.join(root, "proc")) if d.isdigit()]
    except OSError:
        return out
    for pid in pids:
        base = os.path.join(root, "proc", pid)
        rec = {"vram": {}, "sdma": 0, "cu": 0}
        try:
            entries = os.listdir(base)
        except OSError:
            continue
        for e in entries:
            if e.startswith("vram_"):
                v = _read(os.path.join(base, e))
                if v.isdigit() and int(v) > 0:
                    rec["vram"][gpus.get(e[5:], e[5:])] = int(v)
            elif e.startswith("sdma_"):
                v = _read(os.path.join(base, e))
                rec["sdma"] += int(v) if v.isdigit() else 0
            elif e.startswith("stats_"):
                v = _read(os.path.join(base, e, "cu_occupancy"))
                rec["cu"] += int(v) if v.isdigit() else 0
        out[int(pid)] = rec
    return out


def _host_pids() -> dict[int, int]:
    """host pid -> the pid this container sees, for every process visible here (from NSpid in /proc/*/status).
    KFD names processes by their host pid; inside a container they have other numbers."""
    out: dict[int, int] = {}
    try:
        pids = [p for p in os.listdir("/proc") if p.isdigit()]
    except OSError:
        return out
    for pid in pids:
        for line in _read(f"/proc/{pid}/status").splitlines():
            if line.startswith("NSpid:"):
                ns = [int(x) for x in line.split()[1:] if x.isdigit()]
                if ns:
                    out[ns[0]] = int(pid)
                break
    return out


def kfd_processes(root: str = KFD, sample_s: float = 0.4) -> Optional[list[dict]]:
    """Every process using an AMD GPU, read from the kernel (no rocm-smi needed), with its VRAM and whether it is
    working. Sees processes in other containers and on the host, by their host pid; their names only when this
    container shares the host's PID namespace. None when this machine has no KFD."""
    if not os.path.isdir(os.path.join(root, "proc")):
        return None
    first = _kfd_sample(root)
    if sample_s:
        time.sleep(sample_s)
    second = _kfd_sample(root) if sample_s else first
    local = _host_pids()
    out = []
    for hpid, rec in second.items():
        lp = local.get(hpid)
        name = _read(f"/proc/{lp}/comm") if lp else ""
        moved = rec["sdma"] - (first.get(hpid) or {}).get("sdma", rec["sdma"])
        out.append({"pid": lp or hpid, "host_pid": hpid, "local_pid": lp, "visible": bool(lp),
                    "name": name or "not visible from Argos",
                    "gpu": ",".join(sorted(rec["vram"], key=lambda g: int(g) if g.isdigit() else 99)) or "?",
                    "vram_gib": round(sum(rec["vram"].values()) / GIB, 2) if rec["vram"] else None,
                    "busy": bool(rec["cu"] > 0 or moved > 0), "cu_occupancy": rec["cu"], "sdma_bytes": max(0, moved)})
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
        kfd = kfd_processes()
        if kfd is not None:
            tool, procs = "kernel (KFD)", kfd
    if procs is None:
        tool, procs = "rocm-smi", _rocm()
    if procs is None or (not procs and sys.platform.startswith("linux")):
        found = _kfd()
        if found or procs is None:
            tool, procs = "/dev/kfd", found
    if procs is None:
        return {"tool": None, "processes": [], "note": "No GPU tool found here (nvidia-smi or rocm-smi)."}
    cmds = _commands([p["pid"] for p in procs if p.get("visible", True)])
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
        p.setdefault("visible", True)
        p["argos"] = bool(p["owner"] or p["self"])
    note = ""
    hidden = [p for p in procs if not p["visible"]]
    if hidden:
        note = (f"{len(hidden)} process(es) are outside this container, so only their PID and VRAM are known here. "
                "On the host: ps -o pid,comm,args -p <PID> names it, and cat /proc/<PID>/cgroup shows the container. "
                "To see names here and stop them from Argos, give Argos the host's PID namespace (pid: host).")
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
