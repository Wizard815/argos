"""Measure how much VRAM a launch really takes.

The estimate in ``scheduler.estimate_gib`` is a guess. A measurement launches
the command, waits for it to load, and records what it occupies: the larger of
the GPU memory that appeared (before vs after, from the telemetry reader) and
the buffers llama.cpp reports in its own log. The result is stored per model id
and replaces the estimate in the exported plan.

It runs in a background thread; the UI polls the job.
"""
from __future__ import annotations

import threading
import time
from typing import Callable, Optional

from .command import parse_command
from .db import new_id
from .log_analyzer import parse_log_text
from .runner import LaunchError, Runner
from .scheduler import Plans

MIB_PER_GIB = 1024.0
DEVICE_OVERHEAD_GIB = 0.4  # HIP/CUDA context per device, which the log's buffers do not include


def delta_per_gpu(before: list[dict], after: list[dict]) -> list[float]:
    """GiB of VRAM that appeared on each GPU, matched by index and never negative."""
    prev = {int(g.get("gpu", i)): float(g.get("mem_used_mb") or 0) for i, g in enumerate(before)}
    out = []
    for i, g in enumerate(after):
        idx = int(g.get("gpu", i))
        out.append(round(max(float(g.get("mem_used_mb") or 0) - prev.get(idx, 0.0), 0.0) / MIB_PER_GIB, 3))
    return out


class Measurer:
    def __init__(self, runner: Runner, plans: Plans, sample: Callable[[], list[dict]],
                 event: Callable[..., None]):
        self.runner, self.plans, self.sample, self.event = runner, plans, sample, event
        self.jobs: dict[str, dict] = {}
        self._cancel: set[str] = set()
        self._lock = threading.Lock()

    def start(self, command: str, model_id: str, label: str = "", timeout_s: float = 900,
              settle_s: float = 6.0) -> str:
        job_id = new_id("meas")
        with self._lock:
            self.jobs[job_id] = {"job_id": job_id, "state": "queued", "message": "starting", "model_id": model_id,
                                 "label": label or model_id, "launch_id": "", "started_at": time.time(),
                                 "result": None}
        threading.Thread(target=self._run, args=(job_id, command, model_id, timeout_s, settle_s),
                         daemon=True, name=f"measure-{job_id}").start()
        return job_id

    def get(self, job_id: str) -> Optional[dict]:
        with self._lock:
            job = self.jobs.get(job_id)
            return dict(job) if job else None

    def cancel(self, job_id: str) -> bool:
        with self._lock:
            if job_id not in self.jobs or self.jobs[job_id]["state"] in ("done", "error", "cancelled"):
                return False
            self._cancel.add(job_id)
        return True

    def _set(self, job_id: str, **kw) -> None:
        with self._lock:
            self.jobs[job_id].update(kw)

    def _cancelled(self, job_id: str) -> bool:
        with self._lock:
            return job_id in self._cancel

    def _fail(self, job_id: str, message: str, state: str = "error") -> None:
        self._set(job_id, state=state, message=message)
        self.event("measure", f"{self.jobs[job_id]['label']}: {message}", level="error" if state == "error" else "info")

    def _run(self, job_id: str, command: str, model_id: str, timeout_s: float, settle_s: float) -> None:
        run = None
        try:
            self._set(job_id, state="baseline", message="reading GPU memory before the launch")
            before = self.sample()
            self._set(job_id, state="loading", message="launching and loading the model")
            started = time.time()
            cmd = parse_command(command)
            cmd.set("port", "${PORT}")  # a free port, never one the real server is using
            run = self.runner.launch(cmd.to_string(), base_model=model_id, profile_name="measure")
            self._set(job_id, launch_id=run.launch_id)
            while not self.runner.health(run):
                if self._cancelled(job_id):
                    return self._fail(job_id, "cancelled", "cancelled")
                if not run.running:
                    problem = parse_log_text(self.runner.read_log(run.launch_id, tail_bytes=20000) or "").error
                    return self._fail(job_id, f"the server exited (code {run.exit_code})" + (f": {problem}" if problem else ""))
                if time.time() - started > timeout_s:
                    return self._fail(job_id, f"not ready after {timeout_s:.0f}s")
                time.sleep(0.25)
            load_seconds = round(time.time() - started, 1)

            self._set(job_id, state="settling", message="warming up and letting the GPU readings settle")
            try:  # touch the compute buffers so they are allocated before we read
                self.runner.bench(run, prompt_words=64, n_predict=8, timeout=120)
            except LaunchError:
                pass
            deadline = time.time() + settle_s
            while time.time() < deadline and not self._cancelled(job_id):
                time.sleep(0.2)
            after = self.sample()
            parsed = parse_log_text(self.runner.read_log(run.launch_id) or "")

            per_card = delta_per_gpu(before, after) if before and after else []
            seen = sum(per_card)
            from_log = parsed.gpu_mem_mb / MIB_PER_GIB + DEVICE_OVERHEAD_GIB * max(len(parsed.gpu_by_device or {}), 1) \
                if parsed.gpu_mem_mb else 0.0
            if not seen and not from_log:
                return self._fail(job_id, "no VRAM change seen and no GPU buffers in the log; "
                                          "this ran on the CPU, or GPU readings are not available here")
            gib, source = (seen, "gpu") if seen >= from_log else (from_log, "log")
            self.plans.set_measured(model_id, gib, per_card, load_seconds)
            result = {"gib": round(gib, 2), "per_card": per_card, "source": source, "load_seconds": load_seconds,
                      "log_gib": round(from_log, 2), "gpu_gib": round(seen, 2)}
            self._set(job_id, state="done", message=f"{gib:.1f} GiB, loaded in {load_seconds:.0f}s", result=result)
            self.event("measure", f"{model_id}: {gib:.1f} GiB (from the {source}), loaded in {load_seconds:.0f}s",
                       data={"model_id": model_id, **result})
        except LaunchError as exc:
            self._fail(job_id, str(exc))
        except Exception as exc:  # noqa: BLE001 - a background thread must report, not die silently
            self._fail(job_id, f"unexpected error: {exc}")
        finally:
            if run is not None:
                self.runner.stop(run.launch_id)


__all__ = ["Measurer", "delta_per_gpu"]
