#!/usr/bin/env python3
"""Tests for mm_engine.py — the two-toggle decision logic."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mm_engine as e

# The user's actual single-GPU launch command (384K variant).
CMD_384K = (
    "llama-server --model /app/models/HFCache/hub/models--ornith-ai--Ornith-1.5-35B-A3B-GGUF/"
    "snapshots/12393612fd4f730ff5aadc23e9b8f9648aa49ceb/Ornith-1.5-35B-Q6_K.gguf "
    "--host 0.0.0.0 --port 8000 -ngl 99 -c 393216 --flash-attn on "
    "--cache-type-k turbo3 --cache-type-v turbo3 --fit off --split-mode none "
    "-b 2048 -ub 512 --jinja -lm dio -tps 0 --rope-scaling yarn --rope-scale 2 "
    "--reasoning-preserve --parallel 1 --cache-ram 4096"
)
ENV_SINGLE = ["HIP_VISIBLE_DEVICES=0", "HSA_FORCE_FINE_GRAIN_PCIE=1", "GPU_MAX_HW_QUEUES=8"]

# The two-chat tensor-split variant.
CMD_2CHAT = CMD_384K.replace("--parallel 1", "--parallel 2")
ENV_SPLIT = ["HIP_VISIBLE_DEVICES=0,1", "HSA_FORCE_FINE_GRAIN_PCIE=1"]

passed = failed = 0


def check(name, cond, detail=""):
    global passed, failed
    if cond:
        print(f"{name} pass: {detail}")
        passed += 1
    else:
        print(f"{name} FAIL: {detail}")
        failed += 1


def main():
    # --- parsing the effective launch ---
    l = e.parse_effective_launch(CMD_384K, ENV_SINGLE)
    check("parse/single", l.hip_visible == "0" and l.parallel == 1 and not l.is_split,
          f"hip={l.hip_visible} parallel={l.parallel}")

    l2 = e.parse_effective_launch(CMD_2CHAT, ENV_SPLIT)
    check("parse/split", l2.hip_visible == "0,1" and l2.parallel == 2 and l2.is_split,
          f"hip={l2.hip_visible} parallel={l2.parallel}")

    l3 = e.parse_effective_launch("llama-server --parallel=4 -m x", [])
    check("parse/equals-form", l3.parallel == 4 and l3.hip_visible is None,
          f"parallel={l3.parallel} hip={l3.hip_visible}")

    l4 = e.parse_effective_launch("llama-server -m x", None)
    check("parse/default-parallel", l4.parallel == 1, f"parallel={l4.parallel}")

    # --- toggle 1: dynamic single-GPU ---
    d = e.single_gpu_pick({"0": 31, "1": 30}, 10, 4, prefer="0")
    check("single/prefer-fits", d == "0", f"picked {d}")

    # KEY CASE: launch pins GPU 0, but GPU 0 is nearly full -> must move to GPU 1
    d = e.single_gpu_pick({"0": 5, "1": 30}, 10, 4, prefer="0")
    check("single/pin-full-moves", d == "1", f"picked {d} (pin was full)")

    d = e.single_gpu_pick({"0": 5, "1": 6}, 28, 4, prefer="0")
    check("single/nothing-fits", d is None, f"picked {d}")

    # --- instances_needed: ceil(chats/parallel) ---
    for chats, par, want in [(0, 1, 0), (1, 1, 1), (2, 1, 2), (3, 2, 2),
                             (4, 2, 2), (5, 2, 3), (6, 2, 3), (7, 2, 4)]:
        got = e.instances_needed(chats, par)
        check(f"need/{chats}ch@{par}p", got == want, f"{got} (want {want})")

    # --- toggle 2: multi-model plans (32GB cards, small model) ---
    free = {"0": 31, "1": 30}

    p = e.plan_multi_model(active_chats=1, parallel=1, free_vram=free, model_gb=10)
    check("mm/p1-1chat", p.start == ["0"] and p.total_instances == 1 and p.queued == 0,
          f"start={p.start} total={p.total_instances}")

    p = e.plan_multi_model(active_chats=2, parallel=1, free_vram=free, model_gb=10)
    check("mm/p1-2chat-start2", p.start == ["0", "1"] and p.total_instances == 2 and p.queued == 0,
          f"start={p.start} total={p.total_instances}")

    p = e.plan_multi_model(active_chats=3, parallel=2, free_vram=free, model_gb=10)
    check("mm/p2-3chat-start2", p.start == ["0", "1"] and p.total_instances == 2 and p.queued == 0,
          f"start={p.start} total={p.total_instances} cap=4")

    p = e.plan_multi_model(active_chats=5, parallel=2, free_vram=free, model_gb=10)
    check("mm/p2-5chat-queues1", p.start == ["0", "1"] and p.total_instances == 2 and p.queued == 1,
          f"start={p.start} total={p.total_instances} queued={p.queued}")

    p = e.plan_multi_model(active_chats=4, parallel=2, free_vram=free, model_gb=10,
                           existing_instances=1)
    check("mm/p2-4chat-reuse1", p.start == ["0"] and p.total_instances == 2 and p.queued == 0,
          f"start={p.start} total={p.total_instances}")

    # only one card fits -> 2nd instance can't start -> queue
    p = e.plan_multi_model(active_chats=2, parallel=1, free_vram={"0": 31, "1": 4},
                           model_gb=10)
    check("mm/one-card-only-queues", p.start == ["0"] and p.queued == 1,
          f"start={p.start} queued={p.queued}")

    # --- combined decide() ---
    toggles = {"single_gpu": True, "multi_model": False}
    d = e.decide("ornith-1.5-35b-q6_k", CMD_384K, ENV_SINGLE, toggles,
                 free_vram={"0": 6, "1": 30}, model_gb=10)
    check("decide/single-dynamic", d.device == "1" and d.single_gpu, f"device={d.device} ({d.reason})")

    toggles = {"single_gpu": False, "multi_model": True}
    d = e.decide("ornith-1.5-35b-q6_k", CMD_384K, ENV_SINGLE, toggles,
                 free_vram=free, model_gb=10, active_chats=2)
    check("decide/mm-spawns", d.start_devices == ["0", "1"] and d.instances == 2,
          f"start={d.start_devices} instances={d.instances}")

    toggles = {"single_gpu": False, "multi_model": False}
    d = e.decide("ornith-1.5-35b-q6_k", CMD_384K, ENV_SINGLE, toggles, free_vram=free, model_gb=10)
    check("decide/off-unchanged", d.device == "0" and d.instances == 1, f"device={d.device}")

    # --- mode-based (the explicit per-model opt-out) ---
    check("resolve/empty-is-standard", e.resolve_mode({}) == "standard", "")
    check("resolve/legacy-single", e.resolve_mode({"single_gpu": True}) == "single_gpu", "")
    check("resolve/legacy-multi", e.resolve_mode({"multi_model": True}) == "multi_model", "")
    check("resolve/canonical", e.resolve_mode({"mode": "standard"}) == "standard", "")

    # standard = opt-out: launch as-is even when nothing else fits
    d = e.decide("some-embed", CMD_384K, ENV_SINGLE, {"mode": "standard"},
                 free_vram={"0": 6, "1": 6}, model_gb=30)
    check("decide/mode-standard-as-is",
          d.mode == "standard" and d.device == "0" and d.instances == 1
          and not d.single_gpu and not d.multi_model,
          f"mode={d.mode} device={d.device}")

    d = e.decide("ornith-1.5-35b-q6_k", CMD_384K, ENV_SINGLE, {"mode": "single_gpu"},
                 free_vram={"0": 6, "1": 30}, model_gb=10)
    check("decide/mode-single", d.mode == "single_gpu" and d.device == "1",
          f"device={d.device}")

    d = e.decide("ornith-1.5-35b-q6_k", CMD_384K, ENV_SINGLE, {"mode": "multi_model"},
                 free_vram=free, model_gb=10, active_chats=2)
    check("decide/mode-multi", d.mode == "multi_model" and d.instances == 2,
          f"instances={d.instances}")

    print(f"\n{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
