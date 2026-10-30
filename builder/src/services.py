"""Turning launches into perf-sheet rows, and the analytics built on them."""
from __future__ import annotations

import time
from typing import Optional

from .command import FLAGS, guess_base_model, parse_command, swap_model_id
from .flaglib import FLAG_LIBRARY
from .db import Store, new_id
from .flag_edits import FlagEdits
from .log_analyzer import parse_log_text
from .runner import Runner

# Reference rows for the Env Flags view. Every entry is a real variable read by
# llama.cpp/ggml or the GPU runtime; usage columns fill in from recorded runs.
ENV_FLAG_LIBRARY: list[dict] = [
    {"flag": "CUDA_VISIBLE_DEVICES", "category": "gpu-select",
     "description": "Which NVIDIA GPUs the process can see (comma-separated indices or UUIDs).",
     "recommended": "Pin each server to its own GPU(s) when running several models."},
    {"flag": "CUDA_DEVICE_ORDER", "category": "gpu-select",
     "description": "Order CUDA enumerates devices in.",
     "recommended": "PCI_BUS_ID so indices match nvidia-smi."},
    {"flag": "GGML_CUDA_ENABLE_UNIFIED_MEMORY", "category": "gpu-memory",
     "description": "Let the CUDA backend spill into system RAM instead of failing to allocate (Linux).",
     "recommended": "1 only when a model barely does not fit; it is much slower than VRAM."},
    {"flag": "GGML_CUDA_NO_PINNED", "category": "gpu-memory",
     "description": "Disable pinned host memory buffers in the CUDA backend.",
     "recommended": "1 if pinned allocations fail or starve the host of RAM."},
    {"flag": "GGML_CUDA_DISABLE_GRAPHS", "category": "gpu-scheduling",
     "description": "Turn off CUDA graphs for token generation.",
     "recommended": "Leave unset; set to 1 when debugging crashes or odd slowdowns."},
    {"flag": "HIP_VISIBLE_DEVICES", "category": "gpu-select",
     "description": "Which AMD GPUs the ROCm/HIP backend can see.",
     "recommended": "0 on single-GPU hosts with an iGPU, to skip the iGPU."},
    {"flag": "ROCR_VISIBLE_DEVICES", "category": "gpu-select",
     "description": "Device filter applied by the ROCm runtime itself (below HIP).",
     "recommended": "Use instead of HIP_VISIBLE_DEVICES inside containers."},
    {"flag": "HSA_OVERRIDE_GFX_VERSION", "category": "amd-tuning",
     "description": "Make ROCm treat the GPU as a supported gfx target.",
     "recommended": "9.0.6 for gfx906 (MI50 / Radeon VII), 10.3.0 for RDNA2, 11.0.0 for RDNA3."},
    {"flag": "HSA_ENABLE_SDMA", "category": "amd-tuning",
     "description": "Use the SDMA copy engines for host/device and GPU-to-GPU copies; 0 does them with shader "
                    "(blit) kernels instead.",
     "recommended": "0 on MI50/MI60 and other multi-GPU ROCm boxes when copies hang, are slow or corrupt output."},
    {"flag": "HSA_FORCE_FINE_GRAIN_PCIE", "category": "amd-tuning",
     "description": "Make ROCm allocate host-visible memory as fine-grained over PCIe, so GPUs can access each "
                    "other's and the host's memory directly (peer to peer) without an xGMI bridge.",
     "recommended": "1 when splitting a model over several AMD cards on PCIe."},
    {"flag": "HIP_FORCE_DEV_KERNARG", "category": "amd-tuning",
     "description": "Put HIP kernel arguments in device memory instead of host memory, which cuts kernel "
                    "launch latency.",
     "recommended": "1 on MI-series cards; it mostly helps generation speed, which launches many small kernels."},
    {"flag": "GGML_SCHED_N_COPIES", "category": "gpu-scheduling",
     "description": "In llama.cpp builds that read it: how many copies of a graph's inputs the scheduler keeps for "
                    "pipeline parallelism across GPUs (upstream fixes this at build time, GGML_SCHED_MAX_COPIES=4). "
                    "1 turns pipelining off.",
     "recommended": "1 to save VRAM on multi-GPU splits, at some prompt-reading speed."},
    {"flag": "GGML_CUDA_ALLREDUCE", "category": "gpu-scheduling",
     "description": "In llama.cpp builds with tensor-parallel splitting (-sm tensor): which all-reduce combines "
                    "the GPUs' partial results; internal is llama.cpp's own, with no NCCL or RCCL. Also read by "
                    "the HIP build, despite the CUDA in its name.",
     "recommended": "internal on ROCm cards without RCCL, such as MI50s split with -sm tensor."},
    {"flag": "LLAMA_ENABLE_MTP_OPT", "category": "speculative",
     "description": "MTP fast path in builds that carry it (the RaidLab llama.cpp image does); "
                    "pairs with --spec-type draft-mtp.",
     "recommended": "1 only on models with an MTP head; leave it off for the rest."},
    {"flag": "GGML_VK_VISIBLE_DEVICES", "category": "gpu-select",
     "description": "Which Vulkan devices the Vulkan backend uses.",
     "recommended": "Index list from vulkaninfo; skip software/iGPU devices."},
    {"flag": "LLAMA_ARG_CTX_SIZE", "category": "server-args",
     "description": "Same as -c/--ctx-size; every llama-server flag has a LLAMA_ARG_* twin.",
     "recommended": "Use flags in the command instead unless a wrapper sets args by env."},
    {"flag": "LLAMA_ARG_N_GPU_LAYERS", "category": "server-args",
     "description": "Same as -ngl/--n-gpu-layers.",
     "recommended": "A command-line flag wins over the env var."},
    {"flag": "LLAMA_CACHE", "category": "downloads",
     "description": "Directory where -hf/--hf-repo downloads are cached.",
     "recommended": "Point at a big, persistent volume in containers."},
    {"flag": "HF_TOKEN", "category": "downloads",
     "description": "Hugging Face token for gated or private repos with -hf.",
     "recommended": "Set in the environment, not in saved profiles."},
]


class Recorder:
    """Stores the result of a benchmark (or the numbers found in a log) as a run row."""

    def __init__(self, store: Store, runner: Runner):
        self.store = store
        self.runner = runner

    def record(self, launch_id: str, timings: Optional[dict] = None, note: str = "", run=None,
               log_text: Optional[str] = None) -> dict:
        """``run`` and ``log_text`` stand in for a launch of this session: a model the engine runs has no Run here."""
        run = run or self.runner.get(launch_id)
        if run is None:
            raise KeyError(f"no launch {launch_id} in this session")
        parsed = parse_log_text(log_text if log_text is not None else self.runner.read_log(launch_id) or "")
        cmd = parse_command(run.command)
        s = cmd.summary()
        perf = {
            "input_tokens": parsed.input_tokens, "output_tokens": parsed.output_tokens,
            "prompt_ms": parsed.prompt_ms, "eval_ms": parsed.eval_ms,
            "prompt_per_second": parsed.prompt_per_second,
            "tokens_per_second": parsed.tokens_per_second,
            "draft_tokens": parsed.draft_tokens or None, "draft_acc": parsed.draft_acc,
            "mem_used_gb": round(parsed.gpu_mem_mb / 1024, 2) if parsed.gpu_mem_mb else None,
            "prefill_high_tps": parsed.prefill_high_tps, "prefill_low_tps": parsed.prefill_low_tps,
        }
        perf.update({k: v for k, v in (timings or {}).items() if v is not None})
        now = time.time()
        base_model = run.base_model or guess_base_model(s["model"]) or "unknown"
        rec = {
            "run_id": new_id("bench" if timings else "log"),
            "launch_id": launch_id, "created_at": now, "base_model": base_model,
            "profile_name": run.profile_name, "launch_command": run.command,
            "env_json": run.env, "gguf_path": s["model"],
            "model_id": swap_model_id(s["model"]) if s["model"].lower().endswith(".gguf") else None,
            "mmproj_file": s["mmproj"], "draft_model": s["model_draft"], "spec_type": s["spec_type"],
            "load_mode": s["load_mode"], "port": run.port,
            "backend": cmd.program.rsplit("/", 1)[-1], "gpu_layers": s["n_gpu_layers"],
            "n_ctx": parsed.n_ctx or s["ctx_size"], "n_batch": s["batch_size"],
            "n_ubatch": s["ubatch_size"], "n_threads": s["threads"], "n_parallel": s["parallel"],
            "mmap": None if s["mmap"] is None else int(s["mmap"]),
            "flash_attention": s["flash_attn"], "cache_type_k": s["cache_type_k"],
            "cache_type_v": s["cache_type_v"], "load_ms": parsed.load_ms or None,
            "layers_offloaded": (f"{parsed.layers_offloaded}/{parsed.layers_total}"
                                 if parsed.layers_total else None),
            "duration_ms": int((perf.get("prompt_ms") or 0) + (perf.get("eval_ms") or 0)),
            "note": note or (parsed.error if not perf.get("tokens_per_second") else ""),
            "log_source": "bench" if timings else "log",
            "success": 1 if (perf.get("tokens_per_second") or 0) > 0 else 0,
            **perf,
        }
        self.store.add_run(rec)
        for flag in run.env:
            self.store.set_last_used(flag, base_model, perf.get("tokens_per_second"),
                                     perf.get("prompt_per_second"), now)
        return self.store.get_run(rec["run_id"])  # type: ignore[return-value]


class Analytics:
    def __init__(self, store: Store):
        self.store = store

    def best_by_base_model(self) -> list[dict]:
        best: dict[str, dict] = {}
        counts: dict[str, int] = {}
        for r in self.store.list_runs(limit=100_000):
            base = r["base_model"]
            counts[base] = counts.get(base, 0) + 1
            if (r.get("tokens_per_second") or 0) <= 0:
                continue
            if base not in best or r["tokens_per_second"] > best[base]["tokens_per_second"]:
                best[base] = r
        rows = [{**r, "runs": counts[base]} for base, r in best.items()]
        return sorted(rows, key=lambda r: r["tokens_per_second"], reverse=True)

    def env_table(self, edits: bool = True) -> list[dict]:
        """Environment variables: the reference list, ones seen in runs, and your own additions and changes."""
        usage = {row["flag"]: row for row in self.store.list_env_flags()}
        out = []
        for lib in ENV_FLAG_LIBRARY:
            out.append({**lib, **_usage(usage.pop(lib["flag"], {}))})
        for flag, row in sorted(usage.items()):
            out.append({"flag": flag, "category": row.get("category") or "seen in runs",
                        "description": row.get("description") or "",
                        "recommended": row.get("recommended") or "", **_usage(row)})
        if not edits:
            return out
        return FlagEdits(self.store).apply("env", out, blank={"category": "yours", "description": "", "recommended": "", **_usage({})})


    def flag_table(self, edits: bool = True) -> list[dict]:
        """llama-server flags: the reference list, how many saved profiles use each and with which values,
        and your own additions and changes (removed rows come back marked ``removed``)."""
        uses: dict[str, dict] = {}
        for p in self.store.list_profiles():
            try:
                cmd = parse_command(p["launch_command"])
            except ValueError:
                continue
            seen = set()
            for a in cmd.args:
                if not a.flag or a.name in seen:
                    continue
                seen.add(a.name)
                u = uses.setdefault(a.name, {"profiles": 0, "values": {}, "spelling": a.flag})
                u["profiles"] += 1
                val = a.value if a.value is not None else ""
                u["values"][val] = u["values"].get(val, 0) + 1

        def row(name: str, category: str, description: str, notes: str) -> dict:
            u = uses.pop(name, {})
            spellings = list(FLAGS[name][0]) if name in FLAGS else [u.get("spelling") or f"--{name}"]
            top = sorted(u.get("values", {}).items(), key=lambda kv: -kv[1])[:3]
            return {"flag": name, "spellings": spellings, "category": category, "description": description, "notes": notes,
                    "profiles": u.get("profiles", 0), "values": [v for v, _ in top if v]}

        rows = [row(f["flag"], f["category"], f["description"], f["notes"]) for f in FLAG_LIBRARY]
        for name in sorted(uses):  # flags your profiles use that the list does not know
            rows.append(row(name, "Seen in your profiles", "Not in the reference list. Check llama-server --help for what it does.", ""))
        if not edits:
            return rows
        return FlagEdits(self.store).apply("flag", rows, blank={"category": "Yours", "description": "", "notes": "", "profiles": 0, "values": []})


def _usage(row: dict) -> dict:
    return {"last_used_for": row.get("last_used_for") or "",
            "last_tps": row.get("last_tokens_per_second"),
            "last_pps": row.get("last_prompt_per_second"),
            "last_run_at": row.get("last_run_at")}


__all__ = ["Recorder", "Analytics", "ENV_FLAG_LIBRARY"]
