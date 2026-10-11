"""Parse llama.cpp server logs into perf stats.

Covered line shapes (from real llama.cpp / llama-server output):

    [ . ] new prompt length: N
    [ . ] prompt eval time:  <ms> ms / <N> toks (<x>.tok/s), context size: <ctx>
    [ . ] eval time:         <ms> ms / <n> toks (<x>.token/s), runtime = <rt> s
    prompt processed per second: <x> tokens
    eval tokens per second: <x> tokens

Also parses nvidia-smi JSON snapshots captured at launch. Produces the same
field names as llama-swap-rescan's activity table so results are comparable.
"""
import json
import re
from dataclasses import dataclass
from typing import Optional


RE_NEW_PROMPT = re.compile(r"new prompt length:\s+(\d+)")
RE_PROMPT_EVAL = re.compile(
    r"prompt eval time:\s+([\d.]+)\s*ms\s*/\s*(\d+)\s*toks?\s*\(([\d.]+)[^)]*?tok/s",
    re.IGNORECASE,
)
RE_EVAL_TIME = re.compile(
    r"eval time:\s+([\d.]+)\s*ms\s*/\s*(\d+)\s*toks?\s*\(([\d.]+)[^)]*?token/s",
    re.IGNORECASE,
)
RE_PROMPT_RATE = re.compile(r"prompt processed per second:\s+([\d.]+)")
RE_EVAL_RATE = re.compile(r"eval tokens per second:\s+([\d.eE+-]+)")
RE_GPU_NVIDIA = re.compile(r"\[Usage:(\d+)%\]")


@dataclass
class ParsedRun:
    input_tokens: int = 0
    output_tokens: int = 0
    prompt_per_second: float = 0.0
    tokens_per_second: float = 0.0
    prompt_eval_ms: float = 0.0
    eval_ms: float = 0.0
    gpu_util_pct: Optional[float] = None
    mem_used_gb: Optional[float] = None
    temp_c: Optional[float] = None
    draft_acc: Optional[float] = None
    cache_tokens: int = 0
    n_ctx: int = 0
    source: str = "log"

    def to_dict(self):
        return {k: v for k, v in self.__dict__.items()}


def parse_log_text(text):
    """Extract stats from a raw llama.cpp / llama-server log."""
    r = ParsedRun(source="log")
    for line in text.splitlines():
        m = RE_NEW_PROMPT.search(line)
        if m:
            r.input_tokens = max(safe_int(r.input_tokens), safe_int(m.group(1)))
        m = RE_PROMPT_EVAL.search(line)
        if m:
            try:
                r.prompt_eval_ms = float(m.group(1))
                r.input_tokens = max(r.input_tokens, safe_int(m.group(2)))
                r.prompt_per_second = safe_float(m.group(3))
            except (IndexError, ValueError):
                pass
        m = RE_EVAL_TIME.search(line)
        if m:
            try:
                r.eval_ms = float(m.group(1))
                r.output_tokens = max(r.output_tokens, safe_int(m.group(2)))
                r.tokens_per_second = safe_float(m.group(3))
            except (IndexError, ValueError):
                pass
        m = RE_PROMPT_RATE.search(line)
        if m and not r.prompt_per_second:
            r.prompt_per_second = safe_float(m.groups()[0])
        m = RE_EVAL_RATE.search(line)
        if m and not r.tokens_per_second:
            r.tokens_per_second = safe_float(re.sub(r"[^\d.]", "", m.groups()[0]))
        m = RE_GPU_NVIDIA.search(line)
        if m:
            r.gpu_util_pct = int(m.group(1)) / 100.0
    return r


def parse_metrics_json(obj):
    """Feed llama-swap activity JSON directly."""
    r = ParsedRun(source="metrics")
    for k in ("input_tokens", "output_tokens", "cache_tokens"):
        if k in obj:
            setattr(r, k, safe_int(obj[k]))
    r.prompt_per_second = safe_float(obj.get("prompt_per_second"))
    r.tokens_per_second = safe_float(obj.get("tokens_per_second"))
    r.draft_acc = safe_float(obj.get("draft_acc_tokens"))
    r.duration_ms = safe_int(obj.get("duration_ms"))
    meta = obj.get("metadata_json")
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except Exception:
            meta = {}
    if isinstance(meta, dict):
        r.n_ctx = safe_int(meta.get("context_size") or meta.get("n_ctx"))
    return r


def parse_nvidia_smi(text):
    """Parse nvidia-smi JSON output into gpu util + mem used (GB)."""
    r = ParsedRun(source="smi")
    try:
        data = json.loads(text)
    except Exception:
        return r
    gpus = data.get("gpus", []) if isinstance(data, dict) else []
    for g in gpus:
        util = g.get("utilization_gpu")
        if util and util.get("gpu_percentage"):
            r.gpu_util_pct = int(util["gpu_percentage"]) / 100.0
        used = g.get("memory_used")
        if used and used.get("used_total_bytes"):
            r.mem_used_gb = round(int(used["used_total_bytes"]) / (1024**3), 2)
    return r


def safe_int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def safe_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0
