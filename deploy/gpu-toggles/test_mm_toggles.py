#!/usr/bin/env python3
"""Tests for mm_toggles.py — the per-model mode state."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mm_toggles

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
    p = os.path.join(tempfile.mkdtemp(), "model_toggles.json")

    # default: standard (the opt-out)
    check("default-standard",
          mm_toggles.get(mm_toggles.load(p), "m") ==
          {"mode": "standard", "single_gpu": False, "multi_model": False},
          "standard")

    # set single_gpu
    r = mm_toggles.set_mode(p, "ornith-1.5-35b-q6_k", "single_gpu")
    check("set-single", r == {"mode": "single_gpu", "single_gpu": True, "multi_model": False},
          f"{r}")

    # switching mode replaces, does not accumulate
    r = mm_toggles.set_mode(p, "ornith-1.5-35b-q6_k", "multi_model")
    check("switch-mode-replaces",
          r == {"mode": "multi_model", "single_gpu": False, "multi_model": True}, f"{r}")

    # back to the opt-out
    r = mm_toggles.set_mode(p, "ornith-1.5-35b-q6_k", "standard")
    check("back-to-standard",
          r == {"mode": "standard", "single_gpu": False, "multi_model": False}, f"{r}")

    # models are independent
    mm_toggles.set_mode(p, "other-model", "single_gpu")
    s = mm_toggles.load(p)
    check("independent-models",
          mm_toggles.get(s, "ornith-1.5-35b-q6_k")["mode"] == "standard"
          and mm_toggles.get(s, "other-model")["mode"] == "single_gpu", f"{s}")

    # legacy record (booleans only) still reads correctly
    s["legacy"] = {"single_gpu": True, "multi_model": False}
    check("legacy-record", mm_toggles.get(s, "legacy")["mode"] == "single_gpu",
          f"{mm_toggles.get(s, 'legacy')}")
    s["legacy2"] = {"single_gpu": False, "multi_model": True}
    check("legacy-record-mm", mm_toggles.get(s, "legacy2")["mode"] == "multi_model", "")

    # invalid mode rejected
    try:
        mm_toggles.set_mode(p, "x", "bogus")
        check("reject-invalid", False, "no error raised")
    except ValueError:
        check("reject-invalid", True, "ValueError")

    print(f"\n{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
