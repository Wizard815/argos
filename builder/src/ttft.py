"""Time to first token for a launch command: how long a cold start and a prompt take, and which settings move it.

TTFT has two parts:
  - load (cold start only): reading the model file, uploading it to the GPUs, warmup and server start;
  - prefill: prompt tokens / prompt speed, paid on every request (minus any reused prompt prefix).

Measured numbers win over estimates:
  - load: ``model_sessions.load_seconds`` of earlier loads of this model (same config first, then the same
    load mode, then any), else file size / read speed + a fixed start overhead;
  - prefill: the median prompt speed of this config from Argos's request log and benchmarks (``model_perf``),
    else the median over the model's other configs, else unknown.
"""
from __future__ import annotations

from statistics import median
from typing import Any, Optional

from .command import parse_command
from .db import Store
from .library import model_id_for_command
from .model_perf import model_perf, signature
from .schedule_rows import file_bytes

GB = 1e9
PROMPTS = (512, 2048, 8192, 32768)

# Read speeds used when no load was measured, by load mode. dio (O_DIRECT) always reads the disk; buffered
# reads (none, mmap) come from the page cache when the file was read recently and RAM was not reclaimed.
DEFAULT_DISK_GBPS = 2.0      # a typical NVMe behind PCIe 3.0; an HDD array is ~0.2
DEFAULT_START_S = 10.0       # HIP init, graph reserve, warmup, server start (rough)

# settings that change TTFT, as (normalised flag name, what it does). Only the ones the command could change.
TTFT_FLAGS = ("load-mode", "ubatch-size", "batch-size", "flash-attn", "cache-type-k", "cache-type-v", "split-mode",
              "parallel", "spec-type", "ctx-size", "n-gpu-layers", "no-warmup", "cache-reuse", "tensor-split")


def _num(v: Any) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _flags(command: str) -> dict[str, Any]:
    """The TTFT-relevant flags of a command, by long name; boolean flags map to True."""
    try:
        cmd = parse_command(command)
    except ValueError:
        return {}
    out: dict[str, Any] = {}
    for a in cmd.args:
        if a.name in TTFT_FLAGS:
            out[a.name] = a.value if a.value is not None else True
    return out


def _tips(f: dict[str, Any], load_mode: str) -> list[dict]:
    """Settings worth trying, each with the edit the Builder applies (``edits``, null removes a flag)."""
    tips: list[dict] = []
    ub = _num(f.get("ubatch-size")) or 512
    if load_mode == "dio":
        tips.append({"flag": "-lm", "current": "dio", "suggest": "none", "edits": {"load-mode": "none"}, "phase": "load",
                     "why": "dio reads with O_DIRECT, past the page cache, so every reload goes back to the disk. "
                            "'none' reads through the page cache: a reload while the file is still cached comes from RAM. "
                            "Measure the cold load too: on some setups dio is faster cold."})
    elif load_mode in ("", "auto", "mmap"):
        tips.append({"flag": "-lm", "current": load_mode or "default (mmap)", "suggest": "none", "edits": {"load-mode": "none"}, "phase": "load",
                     "why": "mmap can hang on ROCm stacks (see the fork's FEATURES.md); 'none' keeps the page-cache benefit."})
    if "no-warmup" not in f:
        tips.append({"flag": "--no-warmup", "current": "off", "suggest": "on", "edits": {"no-warmup": True}, "phase": "load",
                     "why": "skips the empty warmup run at start (a second or two); the first request pays the kernel loading instead."})
    if ub < 512:
        tips.append({"flag": "-ub", "current": str(int(ub)), "suggest": "512", "edits": {"ubatch-size": "512", "batch-size": "2048"}, "phase": "prefill",
                     "why": "a larger micro-batch feeds the matrix kernels better and usually raises prompt speed; it costs some VRAM."})
    elif ub < 1024:
        tips.append({"flag": "-ub", "current": str(int(ub)), "suggest": "1024", "edits": {"ubatch-size": "1024", "batch-size": "2048"}, "phase": "prefill",
                     "why": "try a larger micro-batch for long prompts; check the gain in the measured configs below, it is model dependent."})
    fa = str(f.get("flash-attn", "")).lower()
    if fa in ("off", "0", "false"):
        tips.append({"flag": "-fa", "current": "off", "suggest": "on", "edits": {"flash-attn": "on"}, "phase": "prefill",
                     "why": "flash attention keeps long prompts fast and the KV cache small; tensor split and quantized V need it anyway."})
    if "cache-reuse" not in f:
        tips.append({"flag": "--cache-reuse", "current": "off", "suggest": "256", "edits": {"cache-reuse": "256"}, "phase": "prefill",
                     "why": "reuses a matching prompt prefix from the slot's cache, so a chat's next turn only processes the new part."})
    np_ = _num(f.get("parallel")) or 1
    if np_ > 1:
        tips.append({"flag": "-np", "current": str(int(np_)), "suggest": None, "edits": None, "phase": "prefill",
                     "why": "slots share the GPUs: a prompt that arrives while another slot is busy waits or runs slower."})
    if f.get("spec-type"):
        tips.append({"flag": "--spec-type", "current": str(f.get("spec-type")), "suggest": None, "edits": None, "phase": "load",
                     "why": "a draft context (MTP, DFlash, draft model) adds its own load and reserve time; it speeds up generation, not the first token."})
    ck = str(f.get("cache-type-k") or "")
    if ck.startswith("turbo"):
        tips.append({"flag": "-ctk", "current": ck, "suggest": "q8_0", "edits": None, "phase": "prefill",
                     "why": "turbo caches rotate Q and dequantize in attention; long prompts are a little slower than with q8_0/f16."})
    return tips


def ttft_estimate(store: Store, command: str, disk_gbps: float = DEFAULT_DISK_GBPS, start_s: float = DEFAULT_START_S,
                  days: float = 180) -> dict:
    """TTFT of a command for a few prompt lengths, cold and warm, with where each number comes from."""
    try:
        s = parse_command(command).summary()
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    path = s.get("model") or ""
    size = file_bytes(path) if path.lower().endswith(".gguf") else 0
    size += sum(file_bytes(p) for p in (s.get("mmproj"), s.get("model_draft")) if p)
    load_mode = str(s.get("load_mode") or "")
    flags = _flags(command)
    mid = model_id_for_command(command)
    cid, _settings = signature(command)

    # load: earlier loads of this model
    load_s, load_src, load_n = None, "estimate", 0
    if mid:
        sessions = [x for x in store.list_sessions(model=mid, limit=500) if (x.get("load_seconds") or 0) > 0]
        same_cfg = [x["load_seconds"] for x in sessions if x.get("command") and signature(x["command"])[0] == cid]
        same_mode = [x["load_seconds"] for x in sessions if (x.get("load_mode") or "") == load_mode]
        anyl = [x["load_seconds"] for x in sessions]
        for vals, src in ((same_cfg, "measured (this config)"), (same_mode, "measured (same load mode)"), (anyl, "measured (other configs)")):
            if vals:
                load_s, load_src, load_n = float(median(vals)), src, len(vals)
                break
    est_load = (size / GB / disk_gbps + start_s) if size else None
    if load_s is None:
        load_s = est_load

    # prefill speed: this config, else the model's other configs
    pp, pp_src, pp_n = None, "unknown", 0
    configs: list[dict] = []
    if mid:
        perf = model_perf(store, mid, days=days)
        for c in perf.get("configs", []):
            med = (c.get("prefill") or {}).get("median")
            n = (c.get("prefill") or {}).get("n") or 0
            if c["id"] == cid and med:
                pp, pp_src, pp_n = float(med), "measured (this config)", int(n)
            if med and c["id"] != "untracked":
                diff = {k: v for k, v in (c.get("settings") or {}).items() if k in TTFT_FLAGS and flags.get(k) != v}
                diff.update({k: None for k in flags if k in TTFT_FLAGS and k not in (c.get("settings") or {})})
                configs.append({"id": c["id"], "label": c.get("label") or c["id"], "prefill_tps": round(float(med), 1), "n": int(n),
                                "current": c["id"] == cid, "diff": diff})
        if pp is None and perf.get("summary", {}).get("prefill_median"):
            pp, pp_src = float(perf["summary"]["prefill_median"]), "measured (other configs of this model)"

    ctx = s.get("ctx_size") or 0
    rows = []
    for n in PROMPTS:
        if ctx and n > ctx:
            continue
        warm = n / pp if pp else None
        rows.append({"tokens": n, "warm_s": round(warm, 2) if warm is not None else None,
                     "cold_s": round(warm + load_s, 1) if warm is not None and load_s is not None else None})
    configs.sort(key=lambda c: (not c["current"], -c["prefill_tps"]))
    return {
        "ok": True, "model_id": mid, "config_id": cid, "size_gb": round(size / GB, 2) if size else None,
        "load_mode": load_mode or "default", "disk_gbps": disk_gbps, "start_s": start_s,
        "load": {"seconds": round(load_s, 1) if load_s is not None else None, "source": load_src, "n": load_n,
                 "estimate_s": round(est_load, 1) if est_load is not None else None},
        "prefill": {"tps": round(pp, 1) if pp else None, "source": pp_src, "n": pp_n},
        "rows": rows, "configs": configs[:8], "tips": _tips(flags, load_mode),
    }


__all__ = ["ttft_estimate", "TTFT_FLAGS", "PROMPTS"]
