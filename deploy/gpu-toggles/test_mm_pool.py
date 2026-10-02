#!/usr/bin/env python3
"""Tests for mm_proxy.Pool — the multi-model scheduling logic."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mm_proxy

passed = failed = 0


def check(name, cond, detail=""):
    global passed, failed
    if cond:
        print(f"{name} pass: {detail}")
        passed += 1
    else:
        print(f"{name} FAIL: {detail}")
        failed += 1


def make_pool(free, parallel, model_gb, margin=4):
    """free is a dict we mutate as backends start (simulating real VRAM use)."""
    state = {"free": dict(free)}

    def free_fn():
        return dict(state["free"])

    def starter(gpu, port):
        # consume model+margin of that card's free VRAM
        state["free"][gpu] = max(0, state["free"][gpu] - (model_gb + margin))
        return mm_proxy.Backend(gpu=gpu, port=port, parallel=1)

    pool = mm_proxy.Pool(parallel=parallel, model_gb=model_gb, margin_gb=margin,
                         free_gb_fn=free_fn, starter=starter, base_port=9000)
    return pool, state


def main():
    # --- A: parallel 1, 2 cards, 10GB model -> 4 instances (2/card), 5th queues ---
    pool, _ = make_pool({"0": 31, "1": 30}, parallel=1, model_gb=10)
    got = [pool.acquire() for _ in range(4)]
    check("A/4-acquires-ok", all(g is not None for g in got) and len(pool.backends) == 4,
          f"backends={len(pool.backends)} gpus={[b.gpu for b in pool.backends]}")
    fifth = pool.acquire()
    check("A/5th-queues", fifth is None and pool.queued == 1,
          f"5th={fifth} queued={pool.queued}")

    # --- B: parallel 2, 2 cards, 10GB model -> reuse then spawn ---
    pool, _ = make_pool({"0": 31, "1": 30}, parallel=2, model_gb=10)
    b1 = pool.acquire()
    b2 = pool.acquire()
    check("B/reuse-two-slots", b1 is b2 and len(pool.backends) == 1,
          f"backend {b1.gpu if b1 else None}, count={len(pool.backends)}")
    b3 = pool.acquire()
    check("B/third-spawns-2nd", b3 is not None and b3 is not b1 and len(pool.backends) == 2,
          f"gpus={[b.gpu for b in pool.backends]}")
    b4 = pool.acquire()
    check("B/fourth-reuses-2nd", b4 is b3, f"reused {b4.gpu if b4 else None}")

    # --- C: realistic 35B single-GPU grid (27GB model + 4 = 31 need, 32GB cards) ---
    pool, _ = make_pool({"0": 32, "1": 31}, parallel=1, model_gb=27)
    c1 = pool.acquire()
    c2 = pool.acquire()
    check("C/two-instances", c1 is not None and c2 is not None and len(pool.backends) == 2,
          f"gpus={[b.gpu for b in pool.backends]}")
    check("C/one-per-card", {b.gpu for b in pool.backends} == {"0", "1"},
          f"gpus={[b.gpu for b in pool.backends]}")
    c3 = pool.acquire()
    check("C/third-queues-no-vram", c3 is None and pool.queued == 1,
          f"queued={pool.queued}")

    # --- D: releasing frees a slot, queue drains ---
    pool.release(c1)
    c4 = pool.acquire()
    check("D/release-serves-queue", c4 is c1 and pool.queued == 0,
          f"served on gpu {c4.gpu if c4 else None}, queued={pool.queued}")

    # --- E: state snapshot shape ---
    st = pool.state()
    check("E/state-shape", set(st) == {"backends", "capacity", "active", "queued"},
          f"{st}")

    print(f"\n{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
