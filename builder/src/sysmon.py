"""The raw system monitor: what rocm-smi, nvidia-smi and a top-style process list say, unprocessed.

The rest of the app turns these readings into bars and plans. When a number looks wrong (a card that is
full while no model runs, a wattage that seems too low), the raw tool output is what settles it. The page
asks for one tab at a time and only while the monitor panel is open, so nothing here runs in the
background.

The process list reads /proc, so it needs no ``top`` in the image. Like the GPU processes list, it sees
other containers' processes only when Argos shares the host's PID namespace (``pid: host``).
"""
from __future__ import annotations

import os
import subprocess
import threading
import time
from typing import Optional

from . import gputools

ROCM_CALLS = (
    [],                                   # the summary table: temperature, power, clocks, fan, VRAM %, GPU %
    ["--showmeminfo", "vram"],            # VRAM total and used, in bytes, per card
    ["--showpids"],                       # which processes hold the cards (KFD), and how much VRAM each
)
NVIDIA_CALLS = ([],)                      # nvidia-smi's own table, processes included
TIMEOUT_S = 8.0
TOP_N = 40
PROC = "/proc"


def _run(argv: list[str], timeout: float = TIMEOUT_S) -> str:
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False, env=gputools.env())
    except subprocess.TimeoutExpired:
        return f"(no answer after {timeout:g} s)"
    except OSError as exc:
        return f"(could not run it: {exc})"
    out = (r.stdout or "").rstrip()
    err = (r.stderr or "").strip()
    if err and r.returncode != 0:
        out = f"{out}\n{err}".strip()
    return out or f"(no output, exit code {r.returncode})"


def smi(name: str) -> dict:
    """{"tool", "found", "text"}: the raw output of each call, under a "$ command" line like a terminal."""
    exe = gputools.tool(name)
    if not exe:
        return {"tool": name, "found": False,
                "text": f"{name} is not in this container. Mount it, or set its path in Settings -> GPU tools."}
    calls = ROCM_CALLS if name == "rocm-smi" else NVIDIA_CALLS
    parts = []
    for args in calls:
        parts.append(f"$ {name} {' '.join(args)}".rstrip() + "\n" + _run([exe, *args]))
    return {"tool": name, "found": True, "path": exe, "text": "\n\n".join(parts)}


# ----- top ----------------------------------------------------------------------------------------------------
_LOCK = threading.Lock()
_PREV: dict = {}  # the counters of the last reading: CPU % is the change since then


def _read(path: str) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return ""


def _cpu_lines(root: str) -> dict[str, tuple[int, int]]:
    """"cpu", "cpu0", ... -> (busy jiffies, total jiffies)."""
    out = {}
    for line in _read(os.path.join(root, "stat")).splitlines():
        if not line.startswith("cpu"):
            continue
        p = line.split()
        nums = [int(x) for x in p[1:] if x.isdigit()]
        if len(nums) < 4:
            continue
        idle = nums[3] + (nums[4] if len(nums) > 4 else 0)
        out[p[0]] = (sum(nums) - idle, sum(nums))
    return out


def _meminfo(root: str) -> dict[str, int]:
    out = {}
    for line in _read(os.path.join(root, "meminfo")).splitlines():
        k, _, v = line.partition(":")
        v = v.split()
        if v and v[0].isdigit():
            out[k] = int(v[0])
    return out


def _procs(root: str) -> dict[int, dict]:
    page_kb = (os.sysconf("SC_PAGE_SIZE") // 1024) if hasattr(os, "sysconf") else 4
    out = {}
    try:
        names = os.listdir(root)
    except OSError:
        return out
    for n in names:
        if not n.isdigit():
            continue
        stat = _read(os.path.join(root, n, "stat"))
        if not stat or ")" not in stat:
            continue
        name = stat[stat.find("(") + 1:stat.rfind(")")]
        f = stat[stat.rfind(")") + 2:].split()
        try:
            state, ticks = f[0], int(f[11]) + int(f[12])
            threads, rss_kb = int(f[17]), int(f[21]) * page_kb
        except (IndexError, ValueError):
            continue
        cmd = _read(os.path.join(root, n, "cmdline")).replace("\0", " ").strip()
        out[int(n)] = {"pid": int(n), "name": name, "state": state, "ticks": ticks, "threads": threads,
                       "rss_mb": round(rss_kb / 1024, 1), "cmd": cmd[:300] or f"[{name}]"}
    return out


def top(root: str = PROC, now: Optional[float] = None, limit: int = TOP_N) -> dict:
    """The machine's CPU per core, memory, load and its busiest processes, like ``top``. CPU % is measured
    since the previous call, so the first call after opening the panel has none yet."""
    now = now or time.time()
    cpus, procs = _cpu_lines(root), _procs(root)
    mem = _meminfo(root)
    hz = os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100
    with _LOCK:
        prev = _PREV.get(root)
        _PREV[root] = {"at": now, "cpus": cpus, "ticks": {p: v["ticks"] for p, v in procs.items()}}
    pct: dict[str, Optional[float]] = {}
    for k, (busy, total) in cpus.items():
        b0, t0 = (prev or {}).get("cpus", {}).get(k, (None, None))
        pct[k] = round(100.0 * (busy - b0) / (total - t0), 1) if b0 is not None and total > t0 else None
    dt = now - prev["at"] if prev else 0
    rows = []
    for pid, p in procs.items():
        t0 = (prev or {}).get("ticks", {}).get(pid)
        cpu = round(100.0 * (p["ticks"] - t0) / hz / dt, 1) if t0 is not None and dt > 0 else None
        rows.append({**{k: v for k, v in p.items() if k != "ticks"}, "cpu": cpu})
    rows.sort(key=lambda r: (-(r["cpu"] or 0), -r["rss_mb"]))
    total_kb, avail_kb = mem.get("MemTotal", 0), mem.get("MemAvailable", mem.get("MemFree", 0))
    load = _read(os.path.join(root, "loadavg")).split()[:3]
    return {
        "cpu": pct.get("cpu"),
        "cores": [pct[k] for k in sorted((k for k in pct if k != "cpu"), key=lambda k: int(k[3:] or 0))],
        "mem_used_mb": round((total_kb - avail_kb) / 1024, 1), "mem_total_mb": round(total_kb / 1024, 1),
        "swap_used_mb": round((mem.get("SwapTotal", 0) - mem.get("SwapFree", 0)) / 1024, 1),
        "swap_total_mb": round(mem.get("SwapTotal", 0) / 1024, 1),
        "load": [float(x) for x in load] if len(load) == 3 else [],
        "tasks": len(procs), "running": sum(1 for p in procs.values() if p["state"] == "R"),
        "processes": rows[:limit], "first": prev is None,
    }
