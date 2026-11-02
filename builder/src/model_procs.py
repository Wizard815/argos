"""What each model's own process uses: its RAM (resident) and how many CPU cores it keeps busy.

The CPU view's bars are the whole machine; this is the model itself. A model the engine starts here is a
llama-server process this container sees in /proc. One started through remote-llama runs in the llama.cpp
container, so remote-llama is asked (``remote-llama <backend> --ps``, over the same SSH it starts models with).
Through a Servers entry the wrapper learns its host from the launch's own environment (``ROCM_SRV3_SSH_HOST``
and friends), so ``--ps`` is run with that same environment. A process is matched to its model by the --port
it listens on. Cores busy is CPU time between two readings over
the time between them, so it is what the model does now, not its average since it started.
"""
from __future__ import annotations

import os
import re
import subprocess
import threading
import time
from typing import Optional

from .command import parse_command

PORT = re.compile(r"--port[= ](\d+)")
LLAMA = re.compile(r"llama-(server|bench)")


def parse_ps(text: str) -> list[dict]:
    """``ps -eo pid=,rss=,times=,args=`` lines: pid, RSS KiB, CPU seconds so far, command line."""
    out = []
    for line in (text or "").splitlines():
        parts = line.split(None, 3)
        if len(parts) < 4 or not parts[0].isdigit() or not parts[1].isdigit() or not parts[2].isdigit():
            continue
        if not LLAMA.search(os.path.basename(parts[3].split()[0])):
            continue  # a wrapper or shell naming llama-server, not the process itself
        out.append({"pid": int(parts[0]), "rss_kib": int(parts[1]), "cpu_s": float(parts[2]), "args": parts[3]})
    return out


def local_ps(proc: str = "/proc") -> list[dict]:
    """The same, for llama.cpp processes in this container."""
    out = []
    tick = os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100
    try:
        pids = [p for p in os.listdir(proc) if p.isdigit()]
    except OSError:
        return out
    for pid in pids:
        try:
            with open(f"{proc}/{pid}/cmdline", "rb") as f:
                argv = f.read().split(b"\0")
            args = b" ".join(argv).decode("utf-8", "replace").strip()
            # the program itself, not a wrapper whose command line names it (remote-llama, the ssh it runs, a shell)
            if not argv or not LLAMA.search(os.path.basename(argv[0].decode("utf-8", "replace"))):
                continue
            with open(f"{proc}/{pid}/stat", encoding="utf-8") as f:
                fields = f.read().rsplit(")", 1)[1].split()
            rss = 0
            with open(f"{proc}/{pid}/status", encoding="utf-8") as f:
                for line in f:
                    if line.startswith("VmRSS:"):
                        rss = int(line.split()[1])
        except (OSError, ValueError, IndexError):
            continue
        out.append({"pid": int(pid), "rss_kib": rss, "cpu_s": (int(fields[11]) + int(fields[12])) / tick, "args": args})
    return out


class ModelProcs:
    """Per-model RAM and cores busy, from the processes' own readings."""

    def __init__(self, run=None):
        self._prev: dict[tuple, tuple[float, float]] = {}  # (where, pid) -> (when, cpu seconds)
        self._lock = threading.Lock()
        # run(argv, env): a test's run takes the argv alone
        self._run = (lambda argv, env=None: run(argv)) if run else self._ssh_ps
        self._last_error = ""
        self._cache: tuple[float, dict] = (0.0, {})
        self.errors: dict[str, str] = {}  # model -> why its process could not be read, for the Schedule page

    def _ssh_ps(self, argv: list[str], env: Optional[dict] = None) -> str:
        try:
            r = subprocess.run(argv, capture_output=True, text=True, timeout=8, check=False,
                               env={**os.environ, **(env or {})})
        except (OSError, subprocess.SubprocessError) as exc:
            self._last_error = f"{os.path.basename(argv[0])} --ps: {exc}"
            return ""
        if r.returncode != 0:
            tail = (r.stderr or "").strip().splitlines()
            self._last_error = tail[-1][:300] if tail else f"{os.path.basename(argv[0])} --ps exited {r.returncode}"
        return r.stdout or ""

    def sample(self, running: list[dict], max_age: float = 3.0) -> dict[str, dict]:
        """``running``: the engine's /running rows ({"model", "cmd"}). Returns {model: {"rss_gib", "cores", "pid",
        "where"}} for the models whose process was found; cores is None on the first reading of a process."""
        now = time.time()
        with self._lock:
            if now - self._cache[0] < max_age:
                return self._cache[1]
            by_port: dict[str, str] = {}
            wrappers: dict[tuple, str] = {}  # (program, backend) -> where
            wrapper_env: dict[tuple, dict] = {}
            wrapper_models: dict[tuple, list] = {}
            for r in running:
                cmd = r.get("cmd") or ""
                m = PORT.search(cmd)
                if m:
                    by_port[m.group(1)] = r["model"]
                try:
                    pc = parse_command(cmd)
                except ValueError:
                    continue
                if os.path.basename(pc.program or "") in ("remote-llama", "docker-llama") and pc.args and not pc.args[0].flag:
                    key = (pc.program, pc.args[0].value or "")
                    wrappers[key] = pc.args[0].value or "remote"
                    env = dict(pc.env or {})
                    for item in r.get("env") or []:  # the engine's env list for the launch: "K=V"
                        k, sep, v = str(item).partition("=")
                        if sep:
                            env.setdefault(k, v)
                    wrapper_env.setdefault(key, {}).update(env)
                    wrapper_models.setdefault(key, []).append(r["model"])
            procs = [("local", p) for p in local_ps()]
            self.errors = {}
            for key, where in wrappers.items():
                program, backend = key
                self._last_error = ""
                text = self._run([program, backend, "--ps"], wrapper_env.get(key) or {})
                found = parse_ps(text)
                procs += [(where, p) for p in found]
                if not found:
                    why = self._last_error or f"{os.path.basename(program)} {backend} --ps listed no llama.cpp process"
                    for model in wrapper_models.get(key) or []:
                        self.errors[model] = why
            out: dict[str, dict] = {}
            seen = set()
            for where, p in procs:
                m = PORT.search(p["args"])
                model = by_port.get(m.group(1)) if m else None
                if not model:
                    continue
                key = (where, p["pid"])
                seen.add(key)
                prev = self._prev.get(key)
                cores = None
                if prev and now > prev[0] and p["cpu_s"] >= prev[1]:
                    cores = round((p["cpu_s"] - prev[1]) / (now - prev[0]), 2)
                self._prev[key] = (now, p["cpu_s"])
                out[model] = {"rss_gib": round(p["rss_kib"] / 1048576, 2), "cores": cores, "pid": p["pid"], "where": where}
            self._prev = {k: v for k, v in self._prev.items() if k in seen}
            self._cache = (now, out)
            return out


__all__ = ["ModelProcs", "local_ps", "parse_ps"]
