#!/usr/bin/env python3
"""Tests for mm_toggles.py."""
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
    check("defaults", mm_toggles.get(mm_toggles.load(p), "m") ==
          {"single_gpu": False, "multi_model": False}, "both off")

    mm_toggles.set_flag(p, "ornith-1.5-35b-q6_k", single_gpu=True)
    check("set-single", mm_toggles.get(mm_toggles.load(p), "ornith-1.5-35b-q6_k")["single_gpu"] is True,
          "")
    mm_toggles.set_flag(p, "ornith-1.5-35b-q6_k", multi_model=True)
    g = mm_toggles.get(mm_toggles.load(p), "ornith-1.5-35b-q6_k")
    check("both-on", g == {"single_gpu": True, "multi_model": True}, f"{g}")

    mm_toggles.set_flag(p, "other-model", single_gpu=True)
    s = mm_toggles.load(p)
    check("independent-models", mm_toggles.get(s, "other-model")["multi_model"] is False,
          f"{s}")

    mm_toggles.set_flag(p, "ornith-1.5-35b-q6_k", single_gpu=False)
    g = mm_toggles.get(mm_toggles.load(p), "ornith-1.5-35b-q6_k")
    check("turn-off", g["single_gpu"] is False and g["multi_model"] is True, f"{g}")

    print(f"\n{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
