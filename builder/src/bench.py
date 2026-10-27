"""llama-bench from the Playground: measure a model the way llama.cpp's own benchmark does.

A Playground launch measures one server answering one prompt. llama-bench loads the model by itself and
measures prompt reading (pp) and generation (tg) at the sizes asked for, at a given depth of context already
in the cache (``-d``: how fast is generation once 64K is in the chat), across sweeps of settings: every field
takes a comma list (``-ub 256,512,1024``) and each combination is a test. It runs where the model runs:
next to llama-server on this machine, or in the llama.cpp container over SSH (``remote-llama <backend> --bench``).

The output is ``-o jsonl``: one JSON line per test, read as it comes, whatever else is on the stream (over
``ssh -tt`` the log and the results arrive mixed). Results are saved to Benchmarks when the run ends.
"""
from __future__ import annotations

import json
import os
import shlex
import signal
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from .command import LaunchCommand, parse_command

# form field -> llama-bench flag; a value may be a comma list, which llama-bench sweeps
FLAGS: dict[str, str] = {
    "n_prompt": "-p", "n_gen": "-n", "pg": "-pg", "depth": "-d", "repetitions": "-r",
    "n_gpu_layers": "-ngl", "flash_attn": "-fa", "cache_type_k": "-ctk", "cache_type_v": "-ctv",
    "split_mode": "-sm", "tensor_split": "-ts", "main_gpu": "-mg", "batch_size": "-b", "ubatch_size": "-ub",
    "threads": "-t", "mmap": "-mmp", "no_kv_offload": "-nkvo",
}
DEFAULTS = {"n_prompt": "512", "n_gen": "128", "depth": "0", "repetitions": "3"}
GPU_ENV = ("HIP_VISIBLE_DEVICES", "ROCR_VISIBLE_DEVICES", "CUDA_VISIBLE_DEVICES", "GGML_VK_VISIBLE_DEVICES",
           "HSA_OVERRIDE_GFX_VERSION", "LLAMA_SHEXP_SPLIT")
LOG_LIMIT = 4 << 20  # bytes of output kept per run


def _onoff(v: Optional[str]) -> Optional[str]:
    if v is None:
        return None
    return "0" if str(v).lower() in ("0", "off", "false", "no") else "1"


def params_from_command(command: str) -> dict:
    """The llama-bench settings a launch command implies: its model, offload, cache, split and batching."""
    cmd = parse_command(command)
    s = cmd.summary()
    out: dict[str, Any] = {"model": s.get("model") or ""}
    for key, flag in (("n_gpu_layers", "n-gpu-layers"), ("cache_type_k", "cache-type-k"), ("cache_type_v", "cache-type-v"),
                      ("split_mode", "split-mode"), ("tensor_split", "tensor-split"), ("main_gpu", "main-gpu"),
                      ("batch_size", "batch-size"), ("ubatch_size", "ubatch-size"), ("threads", "threads")):
        v = cmd.get(flag)
        if v not in (None, ""):
            out[key] = str(v).replace(" ", "")
    fa = s.get("flash_attn")
    if fa is not None:
        out["flash_attn"] = _onoff(fa) if fa not in ("auto",) else "1"
    if s.get("mmap") is not None:
        out["mmap"] = "1" if s["mmap"] else "0"
    if cmd.has("no-kv-offload"):
        out["no_kv_offload"] = "1"
    if out.get("n_gpu_layers") in ("all", "auto"):
        out["n_gpu_layers"] = "99"
    out["env"] = {k: v for k, v in cmd.env.items() if k in GPU_ENV}
    return out


def program_for(cmd: LaunchCommand) -> list[str]:
    """llama-bench where this launch command's llama-server runs: beside it on this machine, or through
    remote-llama on the server it reaches (its backend word, with --bench)."""
    prog = cmd.program or "llama-server"
    if os.path.basename(prog) == "remote-llama" and cmd.args and not cmd.args[0].flag:
        return [prog, cmd.args[0].value or "", "--bench"]
    folder = os.path.dirname(prog)
    return [os.path.join(folder, "llama-bench") if folder else "llama-bench"]


def build(params: dict, base_command: str, server_parts: Optional[dict] = None) -> tuple[list[str], dict[str, str]]:
    """The llama-bench argv and env for a run. ``base_command`` decides where it runs (and is pointed at
    ``server_parts`` when it is a plain llama-server); ``params`` are the form, each a value or a comma list."""
    from .servers import on_server
    cmd = parse_command(base_command)
    if server_parts:
        on_server(cmd, server_parts)
    argv = program_for(cmd)
    env = {k: v for k, v in cmd.env.items() if k.endswith(("_SSH_HOST", "_SSH_PORT", "_SSH_USER", "_SSH_BIN")) or k == "REMOTE_SSH_KEY"}
    env.update({k: str(v) for k, v in (params.get("env") or {}).items() if v not in (None, "")})
    model = str(params.get("model") or "").strip()
    if not model:
        raise ValueError("pick a model (or a profile) to benchmark")
    argv += ["-m", model]
    for key, flag in FLAGS.items():
        v = params.get(key, DEFAULTS.get(key))
        if v in (None, "", False):
            continue
        v = str(v).replace(" ", "")
        if key in ("flash_attn", "mmap", "no_kv_offload"):
            v = ",".join(_onoff(x) or "" for x in v.split(","))
        argv += [flag, v]
    extra = str(params.get("extra") or "").strip()
    if extra:
        argv += shlex.split(extra)
    argv += ["-o", "jsonl"]
    return argv, env


def test_label(r: dict) -> str:
    p, g, d = int(r.get("n_prompt") or 0), int(r.get("n_gen") or 0), int(r.get("n_depth") or 0)
    name = f"pp{p}+tg{g}" if p and g else f"pp{p}" if p else f"tg{g}"
    return name + (f" @ d{d}" if d else "")


def parse_line(line: str) -> Optional[dict]:
    """One result, if this line is one of llama-bench's JSON lines."""
    line = line.strip()
    if not (line.startswith("{") and line.endswith("}")):
        return None
    try:
        r = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(r, dict) or "avg_ts" not in r:
        return None
    r["test"] = test_label(r)
    r["kind"] = "pp" if int(r.get("n_prompt") or 0) and not int(r.get("n_gen") or 0) else "tg" if int(r.get("n_gen") or 0) and not int(r.get("n_prompt") or 0) else "pg"
    return r


@dataclass
class BenchRun:
    bench_id: str
    argv: list
    env: dict
    meta: dict
    started_at: float = field(default_factory=time.time)
    ended_at: Optional[float] = None
    exit_code: Optional[int] = None
    stopped: bool = False
    results: list = field(default_factory=list)
    output: str = ""
    proc: Any = None
    saved: int = 0

    @property
    def running(self) -> bool:
        return self.ended_at is None

    def to_dict(self, with_output: bool = True) -> dict:
        d = {"bench_id": self.bench_id, "command": " ".join(shlex.quote(a) for a in self.argv),
             "env": {k: v for k, v in self.env.items() if k != "REMOTE_SSH_KEY"}, "meta": self.meta,
             "started_at": self.started_at, "ended_at": self.ended_at, "exit_code": self.exit_code,
             "stopped": self.stopped, "running": self.running, "results": self.results, "saved": self.saved}
        if with_output:
            d["output"] = self.output[-200000:]
        return d


class Benches:
    """llama-bench runs, one at a time: two would measure each other."""

    def __init__(self, on_done=None):
        self._lock = threading.Lock()
        self._runs: dict[str, BenchRun] = {}
        self._order: list[str] = []
        self.on_done = on_done  # called with a finished BenchRun (saves its results)

    def current(self) -> Optional[BenchRun]:
        with self._lock:
            return next((self._runs[i] for i in reversed(self._order) if self._runs[i].running), None)

    def start(self, argv: list[str], env: dict, meta: dict) -> BenchRun:
        if self.current():
            raise RuntimeError("a benchmark is already running: stop it first, or wait for it")
        run = BenchRun(bench_id=f"bench-{int(time.time() * 1000)}-{os.urandom(2).hex()}", argv=argv, env=env, meta=meta)
        try:
            run.proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                        env={**os.environ, **env}, start_new_session=True)
        except OSError as exc:
            raise RuntimeError(f"could not start {argv[0]}: {exc}") from None
        with self._lock:
            self._runs[run.bench_id] = run
            self._order = (self._order + [run.bench_id])[-20:]
            self._runs = {i: self._runs[i] for i in self._order}
        threading.Thread(target=self._read, args=(run,), daemon=True, name=f"bench-{run.bench_id}").start()
        return run

    def _read(self, run: BenchRun) -> None:
        for raw in iter(run.proc.stdout.readline, b""):
            line = raw.decode("utf-8", "replace").replace("\r", "")
            r = parse_line(line)
            with self._lock:
                if r:
                    run.results.append(r)
                elif len(run.output) < LOG_LIMIT:
                    run.output += line
        run.exit_code = run.proc.wait()
        run.ended_at = time.time()
        if self.on_done:
            try:
                self.on_done(run)
            except Exception:  # noqa: BLE001 - a failed save must not lose the run
                pass

    def stop(self, bench_id: str) -> bool:
        run = self.get(bench_id)
        if not run or not run.running:
            return False
        run.stopped = True
        try:
            os.killpg(run.proc.pid, signal.SIGTERM)
        except OSError:
            pass
        return True

    def get(self, bench_id: str) -> Optional[BenchRun]:
        with self._lock:
            return self._runs.get(bench_id)

    def list(self) -> list[BenchRun]:
        with self._lock:
            return [self._runs[i] for i in reversed(self._order)]


def run_rows(run: BenchRun) -> list[dict]:
    """A finished run as Benchmarks rows: one per test."""
    m = run.meta
    rows = []
    for i, r in enumerate(run.results):
        row = {
            "run_id": f"{run.bench_id}-{i}", "launch_id": run.bench_id, "created_at": run.started_at,
            "base_model": m.get("base_model") or os.path.basename(str(r.get("model_filename") or "")),
            "model_id": m.get("model_id") or "", "profile_name": m.get("profile_name") or "",
            "launch_command": " ".join(shlex.quote(a) for a in run.argv), "env_json": {k: v for k, v in run.env.items() if k != "REMOTE_SSH_KEY"},
            "gguf_path": r.get("model_filename") or "", "backend": "llama-bench " + str(r.get("backends") or r.get("backend") or ""),
            "gpu_layers": str(r.get("n_gpu_layers") or ""), "n_ctx": int(r.get("n_depth") or 0) + int(r.get("n_prompt") or 0) + int(r.get("n_gen") or 0),
            "n_batch": r.get("n_batch"), "n_ubatch": r.get("n_ubatch"), "n_threads": r.get("n_threads"),
            "flash_attention": str(r.get("flash_attn")), "cache_type_k": r.get("type_k"), "cache_type_v": r.get("type_v"),
            "input_tokens": int(r.get("n_prompt") or 0), "output_tokens": int(r.get("n_gen") or 0),
            "prompt_per_second": float(r["avg_ts"]) if r["kind"] == "pp" else None,
            "tokens_per_second": float(r["avg_ts"]) if r["kind"] == "tg" else None,
            "note": f"llama-bench {r['test']}: {float(r['avg_ts']):.2f} ± {float(r.get('stddev_ts') or 0):.2f} t/s",
            "log_source": "llama-bench", "success": 1,
        }
        if r["kind"] == "pg":  # a combined test: its one speed covers both
            row["tokens_per_second"] = float(r["avg_ts"])
        rows.append(row)
    return rows


__all__ = ["Benches", "BenchRun", "build", "params_from_command", "parse_line", "program_for", "run_rows", "test_label"]
