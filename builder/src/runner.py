"""Launch llama.cpp processes, capture their output, and benchmark servers.

A launched process gets its own process group and log file
(``<runs_dir>/<launch_id>.log``). ``${PORT}`` in a command (Argos style)
is replaced with a free port. Benchmarks call llama-server's ``/completion``
endpoint and read the ``timings`` block it returns, sampling ``nvidia-smi``
while the request runs when it is available.
"""
from __future__ import annotations

import errno
import json
import os
import shutil

from . import gputools
import signal
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Optional

from .command import parse_command
from .gpu_pick import resolve as resolve_gpus
from .db import new_id

_FILLER = (
    "The harbor master logged every ship that came in from the western sea: its cargo, "
    "its crew, the weather it met, and how long the crossing took. Over the years the "
    "ledger became a history of the town itself, of good seasons and lean ones. "
)


class LaunchError(Exception):
    pass


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def bench_prompt(words: int) -> str:
    text = _FILLER * (words // len(_FILLER.split()) + 1)
    return " ".join(text.split()[:words]) + "\n\nContinue the ledger:"


@dataclass
class Run:
    launch_id: str
    command: str
    base_model: str
    profile_name: str
    env: dict
    host: str
    port: int
    log_path: str
    started_at: float
    proc: Optional[subprocess.Popen] = field(default=None, repr=False)
    stopped: bool = False

    @property
    def exit_code(self) -> Optional[int]:
        return self.proc.poll() if self.proc else None

    @property
    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def to_dict(self) -> dict:
        return {
            "launch_id": self.launch_id, "command": self.command, "base_model": self.base_model,
            "profile_name": self.profile_name, "host": self.host, "port": self.port,
            "started_at": self.started_at, "running": self.running,
            "exit_code": self.exit_code, "stopped": self.stopped,
        }


class Runner:
    def __init__(self, runs_dir: str):
        self.runs_dir = runs_dir
        os.makedirs(runs_dir, exist_ok=True)
        self._runs: dict[str, Run] = {}
        self._lock = threading.Lock()

    # ----- lifecycle ------------------------------------------------------
    def launch(self, command: str, base_model: str = "", profile_name: str = "") -> Run:
        try:
            cmd = parse_command(command)
        except ValueError as exc:
            raise LaunchError(f"could not parse command: {exc}") from exc
        resolve_gpus(cmd)  # ARGOS_GPUS -> HIP / CUDA / ... for where this runs
        if "${PORT}" in (cmd.get("port") or ""):
            cmd.set("port", free_port())
        s = cmd.summary()
        host = s["host"] if s["host"] and s["host"] not in ("0.0.0.0", "::") else "127.0.0.1"
        launch_id = new_id("run")
        log_path = os.path.join(self.runs_dir, f"{launch_id}.log")
        env = {**os.environ, **cmd.env}
        with open(log_path, "w", encoding="utf-8") as log:
            log.write(f"$ {cmd.to_string()}\n")
            log.flush()
            try:
                proc = subprocess.Popen(
                    cmd.argv(), stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                    env=env, start_new_session=(os.name == "posix"),
                )
            except OSError as exc:
                if exc.errno == errno.ENOENT:
                    raise LaunchError(
                        f"could not start {cmd.program!r}: not found in this container. "
                        "If llama.cpp runs somewhere else, set Prefab -> Launch program to "
                        "its launcher, e.g. /app/bin/remote-llama rocm."
                    ) from exc
                raise LaunchError(f"could not start {cmd.program!r}: {exc.strerror or exc}") from exc
        run = Run(launch_id=launch_id, command=cmd.to_string(), env=dict(cmd.env),
                  base_model=base_model, profile_name=profile_name,
                  host=host, port=s["port"] or 8080, log_path=log_path,
                  started_at=time.time(), proc=proc)
        with self._lock:
            self._runs[launch_id] = run
        return run

    def get(self, launch_id: str) -> Optional[Run]:
        return self._runs.get(launch_id)

    def list(self) -> list[Run]:
        with self._lock:
            return sorted(self._runs.values(), key=lambda r: r.started_at, reverse=True)

    def stop(self, launch_id: str, timeout: float = 5.0) -> bool:
        run = self._runs.get(launch_id)
        if not run or not run.proc:
            return False
        run.stopped = True
        if run.proc.poll() is not None:
            return True
        self._signal(run.proc, signal.SIGTERM)
        try:
            run.proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self._signal(run.proc, signal.SIGKILL if os.name == "posix" else signal.SIGTERM)
            run.proc.wait(timeout=timeout)
        return True

    def stop_all(self) -> None:
        for run in self.list():
            if run.running:
                self.stop(run.launch_id, timeout=3)

    @staticmethod
    def _signal(proc: subprocess.Popen, sig: int) -> None:
        try:
            if os.name == "posix":
                os.killpg(os.getpgid(proc.pid), sig)
            else:
                proc.send_signal(sig)
        except (ProcessLookupError, PermissionError):
            pass

    def read_log(self, launch_id: str, tail_bytes: int = 0) -> Optional[str]:
        path = os.path.join(self.runs_dir, f"{launch_id}.log")
        try:
            with open(path, "rb") as f:
                if tail_bytes:
                    f.seek(0, os.SEEK_END)
                    f.seek(max(0, f.tell() - tail_bytes))
                return f.read().decode("utf-8", errors="replace")
        except OSError:
            return None

    # ----- server checks --------------------------------------------------
    @staticmethod
    def _url(run: Run, path: str) -> str:
        return f"http://{run.host}:{run.port}{path}"

    def health(self, run: Run, timeout: float = 0.5) -> bool:
        if not run.running:
            return False
        try:
            with urllib.request.urlopen(self._url(run, "/health"), timeout=timeout) as r:  # noqa: S310
                return r.status == 200
        except (urllib.error.URLError, OSError, ValueError):
            return False

    def bench(self, run: Run, prompt_words: int = 400, n_predict: int = 128,
              timeout: float = 900) -> dict:
        """One completion request; returns perf fields named like the runs table."""
        return self.bench_url(self._url(run, "/completion"), prompt_words, n_predict, timeout,
                              where=f"on port {run.port}")

    @staticmethod
    def bench_url(url: str, prompt_words: int = 400, n_predict: int = 128, timeout: float = 900,
                  where: str = "") -> dict:
        """The benchmark request against any llama-server ``/completion`` URL (a launch here, or a model the
        engine runs, through its ``/upstream/<model>/completion``)."""
        body = json.dumps({
            "prompt": bench_prompt(prompt_words), "n_predict": n_predict,
            "cache_prompt": False, "ignore_eos": True, "temperature": 0,
        }).encode()
        req = urllib.request.Request(url, data=body,
                                     headers={"Content-Type": "application/json"})
        with GpuSampler() as gpu:
            try:
                with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310
                    resp = json.loads(r.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")[:300]
                raise LaunchError(f"server returned HTTP {exc.code}: {detail}") from exc
            except (urllib.error.URLError, OSError) as exc:
                raise LaunchError(f"server not reachable {where or url}: {exc}") from exc
        t = resp.get("timings") or {}
        draft_n = t.get("draft_n") or 0
        return {
            "input_tokens": t.get("prompt_n", resp.get("tokens_evaluated")),
            "prompt_ms": t.get("prompt_ms"),
            "prompt_per_second": t.get("prompt_per_second"),
            "output_tokens": t.get("predicted_n", resp.get("tokens_predicted")),
            "eval_ms": t.get("predicted_ms"),
            "tokens_per_second": t.get("predicted_per_second"),
            "draft_tokens": draft_n or None,
            "draft_acc": round(t.get("draft_n_accepted", 0) / draft_n, 4) if draft_n else None,
            **gpu.result(),
        }


def parse_nvidia_smi(text: str) -> dict:
    """``utilization.gpu, memory.used, temperature.gpu`` CSV rows -> max util, total GB, max temp."""
    util, mem, temp = [], 0.0, []
    for line in text.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) != 3:
            continue
        try:
            util.append(float(parts[0]))
            mem += float(parts[1])
            temp.append(float(parts[2]))
        except ValueError:
            continue
    if not util:
        return {}
    return {"gpu_util": max(util), "mem_used_gb": round(mem / 1024, 2), "temp_c": max(temp)}


class GpuSampler:
    """Polls nvidia-smi in the background; keeps the peak of each reading."""

    QUERY = ["--query-gpu=utilization.gpu,memory.used,temperature.gpu",
             "--format=csv,noheader,nounits"]

    def __init__(self, interval: float = 0.25):
        self.exe = gputools.tool("nvidia-smi")
        self.interval = interval
        self.peak: dict = {}
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def __enter__(self) -> "GpuSampler":
        if self.exe:
            self._thread = threading.Thread(target=self._loop, daemon=True)
            self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                out = subprocess.run([self.exe, *self.QUERY], capture_output=True,  # type: ignore[list-item]
                                     text=True, timeout=2).stdout
            except (OSError, subprocess.SubprocessError):
                return
            for k, v in parse_nvidia_smi(out).items():
                self.peak[k] = max(self.peak.get(k, 0), v)
            self._stop.wait(self.interval)

    def result(self) -> dict:
        return {k: self.peak.get(k) for k in ("gpu_util", "mem_used_gb", "temp_c")}


__all__ = ["Runner", "Run", "LaunchError", "GpuSampler", "parse_nvidia_smi",
           "bench_prompt", "free_port"]
