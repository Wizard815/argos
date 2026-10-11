"""Orchestration layer: profile copy/paste, launch/run lifecycle, log analysis,
analytics aggregation, and the curated env-var / launch-command reference table.

Works against :mod:`src.db.Store`, :mod:`src.integrations` and
:mod:`src.log_analyzer`. Pure Python so it can be unit-tested without a GPU or a
running llama-swap / Odysseus instance.
"""
from __future__ import annotations

import os
import signal
import subprocess
import time
from dataclasses import asdict, dataclass, field
from typing import Optional

from .db import Store
from .integrations.command_parser import parse_launch_command
from .log_analyzer import ParsedRun, parse_log_text


# Curated env-var reference library. Each entry becomes one row in the big
# "env vars + launch commands + scale + last-used performance" table. The
# ``last_*`` fields are filled by record_run() from actual runs so you never
# have to guess which flag worked best for your hardware again.
ENV_FLAG_LIBRARY: list[dict] = [
    {"flag": "LLAMA_MMAP", "category": "load-mode",
     "description": "Memory-map the gguf instead of loading fully into VRAM.",
     "recommended": "1 (default); use 0 if the model OOMs or mmap fails"},
    {"flag": "GGML_CUDA_MEM_ALLOC", "category": "gpu-vram",
     "description": "VRAM allocation policy (CUDA). 0=GPU mem pool, 1=default.",
     "recommended": "0 for smoother multi-model layouts"},
    {"flag": "HIP_VISIBLE_DEVICES", "category": "gpu-select",
     "description": "Constrain llama-server to specific ROCm GPU index(es).",
     "recommended": "0 on single-GPU hosts"},
    {"flag": "NVIDIA_VISIBLE_DEVICES", "category": "gpu-select",
     "description": "Constrain llama.cpp CUDA backend to specific GPU(s).",
     "recommended": "ALL / 0 depending on host"},
    {"flag": "HSA_OVERRIDE_GFX_VERSION", "category": "amd-tuning",
     "description": "Force an AMD GFX target when auto-detection is wrong.",
     "recommended": "e.g. '9.0.0' for RDNA3; only set if kernels miscompile"},
    {"flag": "LLAMA_ENABLE_MTP_OPT", "category": "mtp-opt",
     "description": "Enable the MTP (multi-token prediction) fast-path opt.",
     "recommended": "1 when a matching .mtp file is supplied via --mtp"},
    {"flag": "GGML_MMAP_TIMEOUT", "category": "load-mode",
     "description": "Timeout (ms) for mmap mapping before falling back.",
     "recommended": "5000"},
    {"flag": "GPU_MAX_QUEUES", "category": "amd-tuning",
     "description": "Number of ROCm compute queues (amdhip64).",
     "recommended": "Set per GPU in the ROCm docs; often 1-8"},
    {"flag": "GPU_MAX_HW_QUEUES", "category": "amd-tuning",
     "description": "Hardware queues submitted by llama.cpp to the GPU.",
     "recommended": "Match or cap relative to GPU_MAX_QUEUES"},
    {"flag": "HSA_FORCE_FINE_GRAIN_PCIE", "category": "amd-tuning",
     "description": "Force fine-grained PCIe atomics for host/device sharing.",
     "recommended": "1 if you see stability issues with unified memory"},
    {"flag": "CUBLAS_WORKSPACE_CONFIG", "category": "nv-tuning",
     "description": "cuBLAS workspace sizing mode (e.g. ':4096:8').",
     "recommended": "':16M:8' for large batched prompt evals"},
    {"flag": "GGML_SCHED_SYNC_STALL", "category": "gpu-scheduling",
     "description": "Synchronise scheduler stalls instead of dropping them.",
     "recommended": "1 to reduce tail-latency spikes"},
]


@dataclass
class LaunchResult:
    run_id: str
    proc: Optional[subprocess.Popen] = None
    log_path: str = ""
    started_at: float = 0.0
    ok: bool = False
    error: str = ""

    def to_dict(self):
        # Build manually so we never deep-copy a running Popen (its lock is unpicklable).
        d = {k: getattr(self, k) for k in ("run_id", "log_path", "started_at", "ok", "error")}
        d["running"] = bool(self.proc) and self.proc.poll() is None
        return d


@dataclass
class RunRecord:
    """A recorded test of one launch command against one model."""
    base_model: str
    gguf_path: str = ""
    launch_command: str = ""
    port: Optional[int] = None
    backend: str = "llama.cpp"
    gpu_layers: int = -1
    n_ctx: int = 4096
    load_mode: str = ""
    kv_size: str = ""
    mmap: int = 1
    flash_attention: bool = False
    env_vars: dict = field(default_factory=dict)
    tokens_per_second: float = 0.0
    prompt_per_second: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_tokens: int = 0
    draft_tokens: int = 0
    draft_acc: float = 0.0
    duration_ms: int = 0
    gpu_util: Optional[float] = None
    mem_used_gb: Optional[float] = None
    temp_c: Optional[float] = None
    success: bool = True
    note: str = ""

    def to_dict(self):
        return asdict(self)


def _new_profile_id() -> str:
    return f"prof-{int(time.time()*1000)}-{os.urandom(2).hex()}"


def _base_to_id(base_model: str) -> str:
    return base_model.split("/")[-1] if "/" in base_model else base_model


# Canonical directory for captured run logs / analytics input.
RUNS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "runs")

class ProfileService:
    """Copy/paste launch profiles between models + register model files."""

    def __init__(self, store: Store):
        self.store = store

    def copy_profile(
        self, source_cmd: str, target_model_id: str,
        *, edits: Optional[dict] = None, name: str = "", copied_from: str = "",
    ) -> dict:
        """Return a new LaunchCommand built from ``source_cmd`` retargeted at
        ``target_model_id``. Only fields named in ``edits`` change; every other
        flag (flash-attn, mmproj, mtp, env vars) carries over verbatim. This is
        the "copy from one model, paste to another with info fixed" flow.
        """
        src = parse_launch_command(source_cmd)
        edits = edits or {}
        flag_map = {
            "model": "model", "port": "port", "n-gpu-layers": "n-gpu-layers",
            "ctx-size": "ctx-size", "n-batch": "n-batch", "n-ubatch": "n-ubatch",
            "n-threads": "n-threads", "load-mode": "load-mode",
            "kv-size-tokens": "kv-size-tokens",
        }
        overrides = {flag_map.get(k, k): v for k, v in edits.items() if v is not None}
        new_cmd = src.clone_with(**overrides)
        return {
            "profile_id": _new_profile_id(),
            "name": name or f"{target_model_id}.{src.model.split('/')[-1]}",
            "source_model": copied_from or "",
            "launch_command": new_cmd.to_string(),
            "base_model": target_model_id,
            "copied_from": copied_from,
            "env_json": dict(src.env_vars),
            "flags_json": list(new_cmd.args),
        }

    def add_mtp(self, base_model: str, *, hf_repo: str = "", file_path: str = "",
                name: str = "", notes: str = "") -> int:
        return self.store.add_mtp({
            "model_id": _base_to_id(base_model),
            "name": name or os.path.basename(hf_repo or file_path or "mtp"),
            "hf_repo": hf_repo, "file_path": file_path, "base_model": base_model, "notes": notes})

    def add_mmjog(self, base_model: str, *, type_: str = "mmjog", hf_repo: str = "",
                  file_path: str = "", modalities: str = "image", name: str = "", notes: str = "") -> int:
        return self.store.add_mmjog({
            "model_id": _base_to_id(base_model),
            "name": name or os.path.basename(hf_repo or file_path or "mmjog"),
            "type": type_, "hf_repo": hf_repo, "file_path": file_path,
            "base_model": base_model, "modalities": modalities, "notes": notes})


__all__ = ["ProfileService", "RunRecord", "LaunchResult", "ENV_FLAG_LIBRARY"]


# --------------------------------------------------------------------------- #
# Launch / run lifecycle
# --------------------------------------------------------------------------- #
import shlex as _shlex


def _shlex_split(s: str) -> list[str]:
    try:
        return _shlex.split(s)
    except ValueError:
        return s.split()


def _new_run_id() -> str:
    return f"run-{int(time.time()*1000)}-{os.urandom(2).hex()}"


class LaunchService:
    """Spawn llama-server (or any command) as a background process and capture
    its log so we can parse performance back off the real output."""

    def __init__(self, store: Store, work_dir: Optional[str] = None):
        self.store = store
        self.work_dir = work_dir or RUNS_DIR
        os.makedirs(self.work_dir, exist_ok=True)
        self._active: dict[str, LaunchResult] = {}

    def launch(self, record: RunRecord, *, background: bool = True,
               n_predict: int = 512) -> LaunchResult:
        cmd = record.launch_command
        lc = parse_launch_command(cmd)
        run_id = _new_run_id()
        log_path = os.path.join(self.work_dir, f"{run_id}.log")
        env = os.environ.copy()
        env.update({k: str(v) for k, v in (record.env_vars or {}).items()})
        import multiprocessing
        if not any(a.startswith("--n-threads") for a in lc.args):
            lc.args += ["--n-threads", str(multiprocessing.cpu_count())]
        tokens = [lc.program] + list(lc.args)
        result = LaunchResult(run_id=run_id, log_path=log_path, started_at=time.time(), ok=True)
        try:
            proc = subprocess.Popen(
                tokens, stdout=open(log_path, "w"), stderr=subprocess.STDOUT,
                env=env, start_new_session=True,
            )
        except Exception as exc:
            result.ok = False
            result.error = str(exc)
            self._active[run_id] = result
            return result
        result.proc = proc
        self._active[run_id] = result
        if background:
            return result
        try:
            proc.wait(timeout=n_predict * 5 + 30)
        except subprocess.TimeoutExpired:
            pass
        return result

    def stop(self, run_id: str) -> bool:
        """Terminate the process group for ``run_id`` (SIGTERM to the leader).

        The Popen handle is retained so callers can still ``poll()`` it and see
        the exit code once the child dies; only the live pid is cleared because
        the kernel has already taken ownership of that pgid.
        """
        res = self._active.get(run_id)
        if not res or not res.proc:
            return False
        try:
            os.killpg(os.getpgid(res.proc.pid), signal.SIGTERM)
        except Exception:
            # Fall back to signalling just the leader pid.
            try:
                res.proc.terminate()
            except Exception:
                pass
        # Keep proc so status_run/poll can observe termination.
        return True

    def read_log(self, run_id: str) -> str:
        res = self._active.get(run_id)
        path = (res.log_path if res and res.log_path
                else os.path.join(self.work_dir, f"{run_id}.log"))
        try:
            with open(path, encoding="utf-8", errors="ignore") as f:
                return f.read()
        except OSError:
            return ""


class LogAnalysisService:
    """Parse a captured log file into perf stats for a recorded run."""

    def __init__(self, store: Store):
        self.store = store

    def analyze_file(self, log_path: str) -> ParsedRun:
        try:
            with open(log_path, encoding="utf-8", errors="ignore") as f:
                text = f.read()
        except OSError:
            text = ""
        return parse_log_text(text)

    def analyze_string(self, text: str) -> ParsedRun:
        return parse_log_text(text or "")

    def record_run(self, run_id: str, base_model: str, launch_command: str,
                   *, env_vars: Optional[dict] = None, backend: str = "llama.cpp",
                   note: str = "", source: str = "manual") -> dict:
        path = os.path.abspath(os.path.join(RUNS_DIR, f"{run_id}.log"))
        parsed = self.analyze_file(path)
        rec = {
            "run_id": run_id, "created_at": time.time(), "base_model": base_model,
            "launch_command": launch_command, "backend": backend,
            "tokens_per_second": parsed.tokens_per_second,
            "prompt_per_second": parsed.prompt_per_second,
            "input_tokens": parsed.input_tokens, "output_tokens": parsed.output_tokens,
            "cache_tokens": parsed.cache_tokens, "draft_tokens": None,
            "duration_ms": int(parsed.eval_ms + parsed.prompt_eval_ms),
            "gpu_util_real": None, "mem_used_gb": None, "temp_c": None,
            "note": note, "log_source": source,
            "success": 1 if parsed.tokens_per_second > 0 else 0,
        }
        self.store.add_run(rec)
        for k in (env_vars or {}):
            try:
                self.store.set_last_used(k, parsed.tokens_per_second,
                                         parsed.prompt_per_second, time.time())
            except Exception:
                pass
        return rec


class AnalyticsService:
    """Aggregate runs into the big comparison tables."""

    def __init__(self, store: Store):
        self.store = store

    def best_by_base_model(self) -> list[dict]:
        rows = []
        for base in {r["base_model"] for r in self.store.list_runs(limit=5000)}:
            runs = [r for r in self.store.recorded_for(base)
                    if r.get("tokens_per_second", 0) > 0]
            if not runs:
                continue
            best = max(runs, key=lambda r: r["tokens_per_second"])
            rows.append({
                "base_model": base,
                "best_tokens_per_second": best["tokens_per_second"],
                "best_prompt_per_second": best["prompt_per_second"],
                "launch_command": best["launch_command"],
                "created_at": best["created_at"],
                "n_gpu_layers": best.get("gpu_layers"),
            })
        rows.sort(key=lambda x: x["best_tokens_per_second"], reverse=True)
        return rows

    def env_table(self) -> list[dict]:
        out = []
        existing = {row["flag"]: row for row in self.store.list_env_flags()}
        for lib in ENV_FLAG_LIBRARY:
            flag = lib["flag"]
            last = existing.get(flag) or {}
            out.append({**lib, "last_run_for": last.get("last_used_for") or "",
                        "last_tps": last.get("last_tokens_per_second"),
                        "last_pps": last.get("last_prompt_per_second"),
                        "last_run_at": last.get("last_run_at")})
        for k, v in existing.items():
            if not any(o["flag"] == k for o in out):
                out.append({"flag": k, "category": v.get("category", ""),
                            "description": v.get("description", ""),
                            "recommended": v.get("recommended", "")})
        return out


__all__ += ["LaunchService", "LogAnalysisService", "AnalyticsService"]
