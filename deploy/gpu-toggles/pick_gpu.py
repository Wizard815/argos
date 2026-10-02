#!/usr/bin/env python3
"""pick_gpu.py — generic free-VRAM GPU picker for llama-swap launches.

Reads per-card free VRAM from kernel sysfs (works on MI50 / gfx906 and all
modern AMD cards, no rocm-smi dependency), applies a toggle mode, optionally
checks whether the model fits, and (for the match-parallel mode) decides
whether to reuse an already-loaded instance or pick another card.

Design goal: llama-swap never needs to know the GPU answer at scan time. The
scan group's launch command is always `python3 pick_gpu.py <mode> <model_gb>
<margin_gb>` plus the llama-server flags; this script prints the chosen
HIP_VISIBLE_DEVICES value to stdout, and the shell wrapper sets it and execs.

Toggles are read from files if present:
  /app/config.d/pick_gpu_mode      -> auto|0|1|split
  /app/config.d/pick_gpu_parallel  -> on|off   (match-parallel policy)

Because this host (and likely any dev/test host) has no MI50 GPUs, every
function takes an injectable sysfs root so the logic can be unit-tested here.
The default sysfs path is real and used on the box.
"""
from __future__ import annotations
import argparse
import glob
import json
import os
import re
import sys

# --- sysfs reading -----------------------------------------------------------

def read_bytes(path: str) -> int | None:
    try:
        with open(path) as fh:
            return int(fh.read().strip())
    except (OSError, ValueError):
        return None


def enumerate_cards(sysfs: str = "/sys/class/drm") -> dict[str, int]:
    """Return {gpu_id: free_vram_gib}.

    GPU id is the card number stripped from /sys/class/drm/card<N>, which is
    exactly what HIP_VISIBLE_DEVICES wants. renderD12x nodes are only used to
    discover that a card exists; the free VRAM comes from card<N>/device/.
    """
    cards: dict[str, int] = {}
    for card_path in sorted(glob.glob(os.path.join(sysfs, "card[0-9]*"))):
        base = os.path.basename(card_path)
        mm = re.match(r"card(\d+)", base)
        if not mm:
            continue
        gpu_id = mm.group(1)
        free = read_bytes(os.path.join(card_path, "device", "mem_info_vram_free"))
        if free is None:
            # Not an AMD ROCm card (e.g. the Intel iGPU here) — skip.
            continue
        cards[gpu_id] = free
    return cards


def free_gib(sysfs: str = "/sys/class/drm") -> dict[str, int]:
    out: dict[str, int] = {}
    for gpu_id, free in enumerate_cards(sysfs).items():
        if free is not None and free >= 0:
            out[gpu_id] = free // (1024**3)
    return out


# --- toggle files ------------------------------------------------------------

def read_toggle(name: str, default: str, cfg_dirs=()) -> str:
    """Read a toggle file.

    On the box the toggles live at the fixed absolute path /app/config.d/.
    Tests inject a fake dir via cfg_dirs. The real /app/config.d is always
    checked first, then any injected dirs, so a test never accidentally reads
    the box's live toggle.
    """
    candidates = ["/app/config.d"]
    candidates.extend(cfg_dirs)
    for path in candidates:
        full = os.path.join(path, name)
        if os.path.isfile(full):
            val = open(full).read().strip()
            if val:
                return val
    return default


# --- matching (reuse vs spin-up) --------------------------------------------

def _count_key(model_id: str, model_name: str) -> str:
    return f"count::{model_id or model_name}"


def _device_key(model_id: str, model_name: str, gpu_id: str) -> str:
    return f"dev::{(model_id or model_name)}::{gpu_id}"


def read_state(state_file: str | None) -> dict:
    if not state_file or not os.path.isfile(state_file):
        return {}
    try:
        return json.load(open(state_file))
    except (OSError, ValueError):
        return {}


def write_state(state_file: str, state: dict) -> None:
    os.makedirs(os.path.dirname(state_file) or "/", exist_ok=True)
    tmp = f"{state_file}.tmp"
    json.dump(state, open(tmp, "w"), indent=2)
    os.replace(tmp, state_file)


def match_parallel_decision(
    model_id: str,
    model_name: str,
    state_file: str,
    available: dict[str, int],
    model_gb: int,
) -> dict:
    """Decide whether to reuse an in-memory instance or start on a new card.

    Returns {reuse: bool, device: "0"|"1"|..., reason: str}.
    """
    state = read_state(state_file)
    total = sum(int(v) for k, v in state.items()
                if k.startswith(_count_key(model_id, model_name).split("::", 1)[0]))
    # Simpler: count per (model_id) instances across all cards.
    instances = {}
    for dev_key, val in state.items():
        if dev_key.startswith("dev::"):
            _, mid, gpu = dev_key.split("::", 2)
            instances.setdefault(mid, []).append(gpu)
    used = instances.get(model_id or model_name, [])
    n_used = len(used)

    if n_used == 0:
        # Nothing loaded: pick the emptier card that fits.
        best = _emptier_fit(available, model_gb)
        return {"reuse": False, "device": best,
                "reason": f"no running instance, picked {best} (emptier fit)"}

    # An instance already exists. Reuse it only when its card still fits the
    # model -- a model is un-pinned to a card, so if used[0] is now full we
    # must start a fresh instance on an emptier card instead.
    first = used[0]
    first_free = available.get(first)
    if first_free is not None and (model_gb <= 0 or first_free >= model_gb):
        return {"reuse": True, "device": first,
                "reason": f"reused existing instance on GPU {first} "
                          f"({first_free} GB free, fits)"}

    # Reuse card is full; spin up on the emptier fitting card.
    best = _emptier_fit(available, model_gb, preferred=first)
    return {"reuse": False, "device": best,
            "reason": f"existing card GPU {first} full; new instance on {best}"}


def _emptier_fit(available: dict[str, int], model_gb: int, margin_gb: int = 4,
                 preferred: str | None = None) -> str:
    """Pick the emptier card that fits (model_gb + margin). If `preferred`
    (a card already known free from the caller) fits, prefer it unchanged."""
    needed = model_gb + margin_gb
    fitting = {g: f for g, f in available.items()
               if model_gb <= 0 or f >= needed}
    if preferred and preferred in fitting:
        return preferred
    if not fitting:
        # No card fits (or size unknown): fall back to emptier card overall.
        return max(available, key=lambda g: available[g]) if available else "0"
    return max(fitting, key=lambda g: available[g])


# --- main --------------------------------------------------------------------

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="pick_gpu.py")
    ap.add_argument("mode", nargs="?", default="auto",
                    help="auto|0|1|split (defaults to /app/config.d/pick_gpu_mode)")
    ap.add_argument("model_gb", nargs="?", default="0",
                    help="estimated model weight in GiB (0 = skip fit check)")
    ap.add_argument("margin_gb", nargs="?", default="4",
                    help="KV headroom GiB to add for fit checks")
    ap.add_argument("--sysfs", default="/sys/class/drm",
                    help="override sysfs root (testing only)")
    ap.add_argument("--cfg-dir", action="append", default=[],
                    help="dir(s) to look for toggle files like "
                         "pick_gpu_mode / pick_gpu_parallel (repeatable)")
    ap.add_argument("--state-file", default="/app/config.d/gpu_instance_state.json",
                    help="match-parallel instance count state file")
    ap.add_argument("--model-id", default="", help="llama-swap model id (for match-parallel)")
    ap.add_argument("--model-name", default="", help="display name fallback for match-parallel")
    ap.add_argument("--parallel", type=int, default=1,
                    help="--parallel the caller is using; affects reuse semantics")
    args = ap.parse_args(argv)

    try:
        model_gb = int(args.model_gb)
        margin_gb = int(args.margin_gb)
        mode = args.mode or "auto"
    except ValueError:
        print(f"pick_gpu: bad numeric arg model_gb={args.model_gb} margin_gb={args.margin_gb}",
              file=sys.stderr)
        return 2

    available = free_gib(args.sysfs)

    # Toggle-driven mode override (auto only reads the toggle; hard 0/1/split
    # from the positional arg always win).
    if mode == "auto":
        mode = read_toggle("pick_gpu_mode", "auto", args.cfg_dir)

    if mode == "split":
        print(json.dumps({"mode": "split", "device": "0,1", "parallel": args.parallel,
                          "instances": 1, "reason": "split mode"}))
        return 0

    # Explicit single-card mode from the positional arg always wins over any
    # toggle. HIP_VISIBLE_DEVICES accepts the bare card number.
    if mode in ("0", "1"):
        print(json.dumps({"mode": mode, "device": mode, "parallel": args.parallel,
                          "instances": 1, "reason": f"hard-pinned to GPU {mode}"}))
        return 0

    # Match-parallel mode.
    parallel_on = (read_toggle("pick_gpu_parallel", "off", args.cfg_dir) == "on")
    if parallel_on and model_gb > 0:
        decision = match_parallel_decision(
            args.model_id, args.model_name, args.state_file,
            available, model_gb)
        # Record the instance on its device when not reusing.
        if not decision["reuse"]:
            st = read_state(args.state_file)
            st[_device_key(args.model_id, args.model_name, decision["device"])] = True
            write_state(args.state_file, st)
        print(json.dumps({"mode": "auto", "device": decision["device"],
                          "parallel": args.parallel, "reuse": decision["reuse"],
                          "instances": 1, "reason": decision["reason"]}))
        return 0

    # Simple auto-fit.
    device = _emptier_fit(available, model_gb, margin_gb)
    print(json.dumps({"mode": "auto", "device": device, "parallel": args.parallel,
                      "instances": 1, "reason": "emptier fitting card"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
