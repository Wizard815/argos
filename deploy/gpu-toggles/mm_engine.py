#!/usr/bin/env python3
"""mm_engine.py — decision engine for the llama-swap detail-view toggles.

Two toggles, both driven by the model's EFFECTIVE launch (the resolved cmd +
env that the detail card already shows):

  single_gpu  — don't trust HIP_VISIBLE_DEVICES=0,1 (or a hard-pinned 0/1).
                At launch time pick ONE GPU that actually has enough free VRAM;
                if the pinned GPU is full, bind to whichever is free instead.

  multi_model — scale the model to several instances so each instance serves
                exactly its --parallel worth of chats. reads --parallel from the
                cmd; instances = ceil(active_chats / parallel). Each instance is
                a separate process on its own GPU. When no GPU has room, the
                overflow chats queue until an instance frees a slot.

Everything here is pure logic (no GPU, no llama-server) so it can be unit
tested on a host without the MI50s. The launcher/proxy feeds it:
  * free VRAM per GPU (from /sys/class/drm/*/mem_info_vram_free)
  * the effective launch's HIP_VISIBLE_DEVICES and --parallel
  * the model's weight size estimate and a KV margin
"""
from __future__ import annotations

import dataclasses
import math
import shlex

# Each model is in exactly one mode. `standard` is the opt-out: the effective
# launch runs as-is and neither toggle applies.
MODES = ("standard", "single_gpu", "multi_model")


# --- effective launch parsing ------------------------------------------------

@dataclasses.dataclass
class Launch:
    hip_visible: str | None      # "0", "1", "0,1", or None
    parallel: int                # from --parallel N (default 1)

    @property
    def pinned_devices(self) -> list[str]:
        if not self.hip_visible:
            return []
        return [d.strip() for d in self.hip_visible.split(",") if d.strip()]

    @property
    def is_split(self) -> bool:
        return len(self.pinned_devices) > 1


def parse_effective_launch(cmd: str, env: list[str] | None) -> Launch:
    """Read HIP_VISIBLE_DEVICES from env and --parallel from the command.

    Handles both `--parallel N` and `--parallel=N` (and the -np short form).
    """
    hip = None
    for entry in env or []:
        if entry.startswith("HIP_VISIBLE_DEVICES="):
            hip = entry.split("=", 1)[1].strip() or None

    parallel = 1
    try:
        toks = shlex.split(cmd or "")
    except ValueError:
        toks = (cmd or "").split()
    i = 0
    while i < len(toks):
        t = toks[i]
        if t in ("--parallel", "-np") and i + 1 < len(toks):
            parallel = _as_int(toks[i + 1], 1)
            i += 2
            continue
        if t.startswith("--parallel="):
            parallel = _as_int(t.split("=", 1)[1], 1)
        i += 1
    if parallel < 1:
        parallel = 1
    return Launch(hip_visible=hip, parallel=parallel)


def _as_int(s: str, default: int) -> int:
    try:
        return int(s)
    except (TypeError, ValueError):
        return default


def resolve_mode(toggles: dict) -> str:
    """Normalise a toggle record to one of MODES.

    Accepts the canonical {"mode": ...} and, for older records, the legacy
    {"single_gpu": bool, "multi_model": bool} pair.
    """
    mode = toggles.get("mode")
    if mode in MODES:
        return mode
    if toggles.get("multi_model"):
        return "multi_model"
    if toggles.get("single_gpu"):
        return "single_gpu"
    return "standard"


# --- toggle 1: dynamic single-GPU -------------------------------------------

def single_gpu_pick(free_vram: dict[str, int], model_gb: int, margin_gb: int = 4,
                    prefer: str | None = None) -> str | None:
    """Pick ONE GPU with enough free VRAM.

    `prefer` is the GPU named by the effective launch's HIP_VISIBLE_DEVICES
    (e.g. "0"). It is honoured only when that card still fits; otherwise the
    emptiest fitting card wins. Returns None when nothing fits (caller should
    fail/queue rather than launch onto a full card).
    """
    needed = model_gb + margin_gb
    fitting = {g: f for g, f in free_vram.items() if f >= needed}
    if not fitting:
        return None
    if prefer and str(prefer) in fitting:
        return str(prefer)
    return max(fitting, key=lambda g: fitting[g])


# --- toggle 2: multi-model instance scaling ----------------------------------

def instances_needed(active_chats: int, parallel: int) -> int:
    """ceil(chats / parallel). One instance serves `parallel` chats."""
    if active_chats <= 0:
        return 0
    parallel = max(1, parallel)
    return math.ceil(active_chats / parallel)


@dataclasses.dataclass
class MultiModelPlan:
    start: list[str]        # GPUs to start a NEW instance on
    total_instances: int    # existing + newly started
    queued: int             # chats that cannot be served yet
    reason: str

    def as_dict(self) -> dict:
        return dataclasses.asdict(self)


def plan_multi_model(active_chats: int, parallel: int, free_vram: dict[str, int],
                     model_gb: int, margin_gb: int = 4,
                     existing_instances: int = 0) -> MultiModelPlan:
    """Decide how many new instances to start and where.

    Each instance occupies one GPU (one llama-server per card). Instances already
    running are `existing_instances`; the rest are placed on the emptiest cards
    that still fit. Any chats beyond the resulting capacity are queued.
    """
    need = instances_needed(active_chats, parallel)
    to_start = max(0, need - existing_instances)

    needed = model_gb + margin_gb
    fitting = [g for g, f in free_vram.items() if f >= needed]
    fitting.sort(key=lambda g: -free_vram[g])      # emptiest first

    start = fitting[:to_start]
    total = existing_instances + len(start)
    capacity = total * max(1, parallel)
    queued = max(0, active_chats - capacity)

    if need == 0:
        reason = "no active chats"
    elif to_start == 0:
        reason = f"{existing_instances} instance(s) cover {active_chats} chat(s)"
    elif len(start) < to_start:
        reason = (f"need {need} instance(s) but only {len(start)} free fitting "
                  f"GPU(s); {queued} chat(s) queued")
    else:
        reason = f"start {len(start)} new instance(s) on {start} for {active_chats} chat(s)"

    return MultiModelPlan(start=start, total_instances=total, queued=queued,
                          reason=reason)


# --- combined decision -------------------------------------------------------

@dataclasses.dataclass
class Decision:
    model: str
    mode: str
    # single_gpu result
    device: str | None
    # multi_model result
    start_devices: list[str]
    instances: int
    queued: int
    reason: str

    @property
    def single_gpu(self) -> bool:
        return self.mode == "single_gpu"

    @property
    def multi_model(self) -> bool:
        return self.mode == "multi_model"

    def as_dict(self) -> dict:
        d = dataclasses.asdict(self)
        d["single_gpu"] = self.single_gpu
        d["multi_model"] = self.multi_model
        return d


def decide(model_id: str, cmd: str, env: list[str] | None, toggles: dict,
           free_vram: dict[str, int], model_gb: int, margin_gb: int = 4,
           active_chats: int = 1, existing_instances: int = 0) -> Decision:
    """Apply the model's mode to its effective launch."""
    launch = parse_effective_launch(cmd, env)
    mode = resolve_mode(toggles)

    if mode == "standard":
        # Opt-out: the model is not meant to move or scale. Run as written.
        return Decision(model=model_id, mode="standard", device=launch.hip_visible,
                        start_devices=[], instances=1, queued=0,
                        reason="standard: effective launch used as-is")

    if mode == "multi_model":
        plan = plan_multi_model(active_chats, launch.parallel, free_vram,
                                model_gb, margin_gb, existing_instances)
        return Decision(model=model_id, mode="multi_model", device=None,
                        start_devices=plan.start, instances=plan.total_instances,
                        queued=plan.queued, reason=plan.reason)

    # single_gpu
    prefer = launch.pinned_devices[0] if launch.pinned_devices else None
    dev = single_gpu_pick(free_vram, model_gb, margin_gb, prefer=prefer)
    reason = (f"single GPU -> {dev} (preferred {prefer})" if dev
              else f"no GPU fits {model_gb + margin_gb} GiB; would fail/queue")
    return Decision(model=model_id, mode="single_gpu", device=dev,
                    start_devices=[], instances=1 if dev else 0,
                    queued=0 if dev else active_chats, reason=reason)
