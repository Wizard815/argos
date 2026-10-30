"""Pull performance numbers out of llama.cpp output.

Understands the timing blocks printed by llama-server (per request), llama-cli
(``llama_perf_context_print`` / older ``llama_print_timings``) and the markdown
table printed by llama-bench::

    prompt eval time =     58.33 ms /    13 tokens (    4.49 ms per token,   222.87 tokens per second)
           eval time =   1007.31 ms /    64 tokens (   15.74 ms per token,    63.54 tokens per second)
    draft acceptance rate = 0.68208 (  118 accepted /   173 generated)
    | qwen3 8B Q4_K - Medium | 4.68 GiB | 8.19 B | CUDA | 99 | pp512 | 5012.33 ± 21.10 |

Plus load-time facts: context size, layers offloaded, GPU buffer sizes, and
the first error line. When several requests are logged the last one wins.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Optional

_NUM = r"([\d.]+|inf|nan)"
RE_TIMING = re.compile(
    r"(prompt )?eval time\s*=\s*([\d.]+)\s*ms\s*/\s*(\d+)\s*(?:tokens|runs)\s*"
    r"\(\s*[\d.a-z]+\s*ms per token,\s*" + _NUM + r"\s*tokens per second\)",
    re.IGNORECASE,
)
RE_LOAD = re.compile(r"\bload time\s*=\s*([\d.]+)\s*ms")
RE_DRAFT = re.compile(r"draft acceptance rate\s*=\s*([\d.]+)\s*\(\s*(\d+)\s*accepted\s*/\s*(\d+)\s*generated")
RE_NCTX = re.compile(r"\bn_ctx(?:_slot)?\s*=\s*(\d+)")
RE_OFFLOAD = re.compile(r"offloaded (\d+)/(\d+) layers to GPU")
RE_BUFFER = re.compile(r"\b([A-Za-z_]+\d*)\s+(?:model|KV|RS|compute)\s+buffer size\s*=\s*([\d.]+)\s*MiB")
RE_BENCH_ROW = re.compile(r"^\|.*\|\s*(pp|tg)(\d+)[^|]*\|\s*([\d.]+)\s*±")
# llama-server's progress while it reads a long prompt; the speed it prints is the average so far
RE_PROGRESS = re.compile(r"task\s+(\d+)\s*\|\s*prompt processing, n_tokens\s*=\s*(\d+), progress\s*=\s*[\d.]+, t\s*=\s*([\d.]+)\s*s")
RE_READY = re.compile(r"server is listening on|HTTP server listening|all slots are idle")
RE_ERROR = re.compile(
    r"(error:|failed to|out of memory|CUDA error|ROCm error|exception|invalid argument|unknown argument)",
    re.IGNORECASE,
)


def _f(v: str) -> float:
    try:
        x = float(v)
    except ValueError:
        return 0.0
    return x if x == x and x != float("inf") else 0.0


@dataclass
class ParsedRun:
    input_tokens: int = 0
    output_tokens: int = 0
    prompt_ms: float = 0.0
    eval_ms: float = 0.0
    prompt_per_second: float = 0.0
    tokens_per_second: float = 0.0
    load_ms: float = 0.0
    draft_acc: Optional[float] = None
    draft_tokens: int = 0
    n_ctx: Optional[int] = None
    layers_offloaded: Optional[int] = None
    layers_total: Optional[int] = None
    gpu_mem_mb: float = 0.0
    gpu_by_device: Optional[dict] = None  # {"ROCm0": MiB, ...}: model + KV + RS + compute buffers per device
    samples: int = 0
    # the fastest and slowest stretch of the last long prompt (between two progress lines): reading starts fast
    # and slows as the context fills, which prompt_per_second (the average) hides
    prefill_high_tps: Optional[float] = None
    prefill_low_tps: Optional[float] = None
    ready: bool = False
    error: str = ""

    @property
    def duration_ms(self) -> int:
        return int(self.prompt_ms + self.eval_ms)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["duration_ms"] = self.duration_ms
        return d


def parse_log_text(text: str) -> ParsedRun:
    r = ParsedRun()
    buffers: dict[tuple[str, str], float] = {}
    task, last_t, last_n = None, 0.0, 0
    for line in (text or "").splitlines():
        m = RE_PROGRESS.search(line)
        if m:
            t_id, n, t = m.group(1), int(m.group(2)), float(m.group(3))
            if t_id != task:  # a new prompt: its own range
                task, last_t, last_n = t_id, 0.0, 0
                r.prefill_high_tps = r.prefill_low_tps = None
            if t - last_t > 0.01 and n > last_n:
                stretch = round((n - last_n) / (t - last_t), 1)
                r.prefill_high_tps = max(r.prefill_high_tps or 0.0, stretch)
                r.prefill_low_tps = stretch if r.prefill_low_tps is None else min(r.prefill_low_tps, stretch)
            last_t, last_n = t, n
            continue
        m = RE_TIMING.search(line)
        if m:
            ms, n, tps = float(m.group(2)), int(m.group(3)), _f(m.group(4))
            if m.group(1):
                r.prompt_ms, r.input_tokens, r.prompt_per_second = ms, n, tps
            else:
                r.eval_ms, r.output_tokens, r.tokens_per_second = ms, n, tps
                r.samples += 1
            continue
        m = RE_BENCH_ROW.search(line)
        if m:
            kind, n, tps = m.group(1), int(m.group(2)), float(m.group(3))
            if kind == "pp":
                r.input_tokens, r.prompt_per_second = n, tps
            else:
                r.output_tokens, r.tokens_per_second = n, tps
                r.samples += 1
            continue
        m = RE_DRAFT.search(line)
        if m:
            r.draft_acc, r.draft_tokens = float(m.group(1)), int(m.group(3))
            continue
        m = RE_LOAD.search(line)
        if m:
            r.load_ms = float(m.group(1))
        m = RE_NCTX.search(line)
        if m:
            r.n_ctx = int(m.group(1))
        m = RE_OFFLOAD.search(line)
        if m:
            r.layers_offloaded, r.layers_total = int(m.group(1)), int(m.group(2))
        m = RE_BUFFER.search(line)
        if m and not m.group(1).upper().startswith("CPU") and "Host" not in m.group(1):
            kind = "model" if " model " in line else ("KV" if " KV " in line else ("RS" if " RS " in line else "compute"))
            buffers[(m.group(1), kind)] = float(m.group(2))
        if RE_READY.search(line):
            r.ready = True
        if not r.error and RE_ERROR.search(line):
            r.error = line.strip()[:300]
    r.gpu_mem_mb = round(sum(buffers.values()), 2)
    by_device: dict[str, float] = {}
    for (device, _kind), mib in buffers.items():
        by_device[device] = round(by_device.get(device, 0.0) + mib, 2)
    r.gpu_by_device = by_device or None
    return r


RE_TENSOR = re.compile(r"tensor|buffer size|offloading|offloaded|layer|load_tensors|n_ctx|flash|kv cache|graph|split", re.IGNORECASE)


def insight(text: str, max_lines: int = 60) -> dict:
    """What the playground shows live: load readings (tensors, buffers), then one row per finished request."""
    requests: list[dict] = []
    cur: dict = {}
    readings: list[str] = []
    for line in (text or "").splitlines():
        m = RE_TIMING.search(line)
        if m:
            ms, n, tps = float(m.group(2)), int(m.group(3)), _f(m.group(4))
            if m.group(1):
                if cur:
                    requests.append(cur)
                cur = {"n": len(requests) + 1, "prompt_tokens": n, "prompt_ms": ms, "prefill": tps}
            else:
                cur = {**({"n": len(requests) + 1, "prompt_tokens": 0, "prefill": None} if not cur else cur),
                       "output_tokens": n, "eval_ms": ms, "decode": tps}
                requests.append(cur)
                cur = {}
            continue
        if not requests and RE_TENSOR.search(line):
            readings.append(line.strip()[:220])
    if cur:
        requests.append(cur)
    parsed = parse_log_text(text)
    return {"parsed": parsed.to_dict(), "requests": requests[-300:], "readings": readings[-max_lines:]}


__all__ = ["ParsedRun", "parse_log_text", "insight"]
