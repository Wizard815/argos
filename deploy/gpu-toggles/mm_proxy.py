#!/usr/bin/env python3
"""mm_proxy.py — the mechanism behind the Multi-Model toggle.

llama-swap runs ONE process per model id. To serve the same model as several
instances (so each instance owns `--parallel` chats) we make that one process a
small OpenAI-compatible proxy that manages N llama-server backends, one per GPU.

This file has two layers:

  * Pool / Backend  — pure scheduling logic (no sockets), unit-testable here.
  * serve()         — the HTTP front door that forwards /v1/* to a chosen backend.

The toggles themselves live in llama-swap (a per-model toggle state file). This
proxy is launched INSTEAD OF llama-server by the model's cmd when multi_model is
on; it reads the effective launch for --parallel and the model size, then decides
how many backends to run using mm_engine.
"""
from __future__ import annotations

import dataclasses
import json
import os
import subprocess
import threading

import mm_engine


# --- backend + pool (pure logic) --------------------------------------------

@dataclasses.dataclass
class Backend:
    gpu: str
    port: int
    parallel: int
    active: int = 0

    def has_slot(self) -> bool:
        return self.active < self.parallel

    def url(self, path: str = "") -> str:
        return f"http://127.0.0.1:{self.port}{path}"


class Pool:
    """Tracks live backends and hands out a backend with a free slot.

    `starter` is injected so tests can observe/derive the device without
    actually spawning a process. It is called as starter(gpu, port) -> Backend.
    """

    def __init__(self, parallel: int, model_gb: int, margin_gb: int,
                 free_gb_fn, starter, base_port: int = 9000):
        self.parallel = max(1, parallel)
        self.model_gb = model_gb
        self.margin_gb = margin_gb
        self.free_gb_fn = free_gb_fn          # () -> {gpu: free_gib}
        self.starter = starter
        self.base_port = base_port
        self.backends: list[Backend] = []
        self.queued = 0
        self._lock = threading.Lock()

    # -- capacity ----------------------------------------------------------
    def capacity(self) -> int:
        return sum(b.parallel for b in self.backends)

    def active(self) -> int:
        return sum(b.active for b in self.backends)

    def _next_port(self) -> int:
        return self.base_port + len(self.backends)

    def _start_on(self, gpu: str) -> Backend:
        b = self.starter(gpu, self._next_port())
        b.parallel = self.parallel
        self.backends.append(b)
        return b

    # -- acquire / release -------------------------------------------------
    def acquire(self) -> Backend | None:
        """Reserve a slot for one chat.

        Order of preference: an existing backend with a free slot (least loaded),
        else start a new backend on a fitting GPU, else queue (return None).
        """
        with self._lock:
            ready = [b for b in self.backends if b.has_slot()]
            if ready:
                b = min(ready, key=lambda b: b.active)
                b.active += 1
                return b

            free = self.free_gb_fn()
            plan = mm_engine.plan_multi_model(
                active_chats=max(1, self.active() + 1),
                parallel=self.parallel,
                free_vram=free,
                model_gb=self.model_gb,
                margin_gb=self.margin_gb,
                existing_instances=len(self.backends),
            )
            if plan.start:
                b = self._start_on(plan.start[0])
                b.active += 1
                return b

            # No free slot and nowhere to put another instance: queue.
            self.queued += 1
            return None

    def release(self, backend: Backend) -> None:
        with self._lock:
            if backend.active > 0:
                backend.active -= 1
            if self.queued > 0:
                self.queued -= 1

    def state(self) -> dict:
        return {
            "backends": [dataclasses.asdict(b) for b in self.backends],
            "capacity": self.capacity(),
            "active": self.active(),
            "queued": self.queued,
        }


# --- default starter: really spawn llama-server ------------------------------

def _spawn_backend(gpu: str, port: int, argv: list[str], log) -> Backend:
    env = dict(os.environ)
    env["HIP_VISIBLE_DEVICES"] = gpu            # <- dynamic, per backend
    env.setdefault("HSA_FORCE_FINE_GRAIN_PCIE", "1")
    env.setdefault("GPU_MAX_HW_QUEUES", "8")
    # replace the port placeholder / existing --port with this backend's port
    cmd = _retarget_port(argv, port)
    log(f"[mm_proxy] starting backend GPU {gpu} -> {cmd}")
    subprocess.Popen(cmd, env=env)
    return Backend(gpu=gpu, port=port, parallel=1)


def _retarget_port(argv: list[str], port: int) -> list[str]:
    out = []
    i = 0
    while i < len(argv):
        t = argv[i]
        if t == "--port" and i + 1 < len(argv):
            out += [t, str(port)]
            i += 2
            continue
        if t.startswith("--port="):
            out.append(f"--port={port}")
            i += 1
            continue
        out.append(t)
        i += 1
    return out


# --- HTTP front door ---------------------------------------------------------

def serve(argv: list[str], listen_port: int, parallel: int, model_gb: int,
          margin_gb: int = 4, free_gb_fn=None) -> None:
    """OpenAI-compatible front door. Requires `aiohttp` or `flask`; deployed on
    the box. Kept dependency-light: the scheduling core (Pool) is above and is
    what actually carries the logic under test."""
    raise NotImplementedError(
        "HTTP forwarding is deployed on the box; the scheduling logic is in Pool.")


if __name__ == "__main__":
    # On the box this reads the effective launch args passed by llama-swap's cmd
    # and runs serve(); here it just prints the pool decision for a sample.
    launch = mm_engine.parse_effective_launch(
        "llama-server --parallel 2 -m /app/models/x.gguf",
        ["HIP_VISIBLE_DEVICES=0,1"])
    print(json.dumps({"parsed": dataclasses.asdict(launch)}))
