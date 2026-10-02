#!/usr/bin/env python3
"""Test pick_gpu.py against a fake two-card sysfs.

Run on the dev host (no MI50) to prove the picker + match-parallel logic before
deploying to the box.

NOTE: setup for each case MUST run inside the loop (right before the case), not
while building the case list -- an earlier version built every fake sysfs up
front, so only the last one survived and cases read each other's toggles.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PICK = os.path.join(HERE, "pick_gpu.py")

BASE = tempfile.mkdtemp(prefix="pickgpu-test-")
SYSFS = os.path.join(BASE, "fakesysfs")
CFG = os.path.join(BASE, "fakecfg")
ST_FILE = os.path.join(BASE, "state.json")


def build_fake_sysfs(cards):
    """cards = {'0': free_gib_0, '1': free_gib_1, ...}"""
    if os.path.isdir(SYSFS):
        shutil.rmtree(SYSFS, ignore_errors=True)
    os.makedirs(SYSFS, exist_ok=True)
    for gpu_id, free_gib in cards.items():
        dev = os.path.join(SYSFS, f"card{gpu_id}", "device")
        os.makedirs(dev, exist_ok=True)
        with open(os.path.join(dev, "mem_info_vram_free"), "w") as fh:
            fh.write(str(free_gib * 1024**3) + "\n")
        os.symlink(
            f"../../../devices/pci0000:00/0000:00:02.{gpu_id}/drm/card{gpu_id}",
            os.path.join(SYSFS, f"renderD12{gpu_id}"))


def clear_toggles():
    os.makedirs(CFG, exist_ok=True)
    for f in ("pick_gpu_mode", "pick_gpu_parallel", "gpu_instance_state.json"):
        p = os.path.join(CFG, f)
        if os.path.exists(p):
            os.remove(p)
    if os.path.exists(ST_FILE):
        os.remove(ST_FILE)


def write_toggle(name, value):
    os.makedirs(CFG, exist_ok=True)
    with open(os.path.join(CFG, name), "w") as fh:
        fh.write(value + "\n")


def run(args):
    r = subprocess.run([sys.executable, PICK, *args], capture_output=True, text=True,
                       cwd=HERE)
    if r.returncode != 0:
        return None, r.returncode, r.stderr.strip()
    try:
        return json.loads(r.stdout.strip()), 0, r.stderr.strip()
    except json.JSONDecodeError:
        return None, 1, r.stdout.strip()


def main():
    passed = failed = 0
    clear_toggles()

    # Each case: (name, setup_callable, args, check_callable)
    def setup_30_31():
        build_fake_sysfs({"0": 30, "1": 31})

    def setup_20_19():
        build_fake_sysfs({"0": 20, "1": 19})

    def setup_10_12():
        build_fake_sysfs({"0": 10, "1": 12})

    def setup_par_on():
        build_fake_sysfs({"0": 30, "1": 31})
        write_toggle("pick_gpu_parallel", "on")

    def setup_31_2_par_on():
        build_fake_sysfs({"0": 31, "1": 2})
        write_toggle("pick_gpu_parallel", "on")

    def setup_mode_split():
        build_fake_sysfs({"0": 30, "1": 31})
        write_toggle("pick_gpu_mode", "split")

    cases = [
        ("A", setup_30_31, ["auto", "21", "4"],
         lambda r: r["device"] == "1"),
        ("B", setup_20_19, ["auto", "14", "4"],
         lambda r: r["device"] == "0"),
        ("C", setup_10_12, ["auto", "21", "4"],
         lambda r: r["device"] == "1"),
        ("D", setup_30_31, ["split", "21", "4"],
         lambda r: r["device"] == "0,1"),
        ("E", setup_30_31, ["auto", "21", "4",
                            "--model-id", "hermes-4_14b-q4_k_m",
                            "--model-name", "hermes-4_14b-q4_k_m",
                            "--parallel", "1"],
         lambda r: r["device"] == "1"),
        ("F", setup_par_on, ["auto", "21", "4", "--cfg-dir", CFG,
                             "--model-id", "hermes-4_14b-q4_k_m",
                             "--model-name", "hermes-4_14b-q4_k_m",
                             "--parallel", "1", "--state-file", ST_FILE],
         lambda r: r["device"] == "1" and not r["reuse"]),
        ("G", None, ["auto", "21", "4", "--cfg-dir", CFG,
                     "--model-id", "hermes-4_14b-q4_k_m",
                     "--model-name", "hermes-4_14b-q4_k_m",
                     "--parallel", "1", "--state-file", ST_FILE],
         lambda r: r["reuse"] is True and r["device"] == "1"),
        ("H", setup_31_2_par_on, ["auto", "21", "4", "--cfg-dir", CFG,
                                  "--model-id", "hermes-4_14b-q4_k_m",
                                  "--model-name", "hermes-4_14b-q4_k_m",
                                  "--parallel", "1", "--state-file", ST_FILE],
         lambda r: r["reuse"] is False and r["device"] == "0"),
        ("I", setup_mode_split, ["auto", "21", "4", "--cfg-dir", CFG],
         lambda r: r["device"] == "0,1"),
    ]

    for name, setup, args, check in cases:
        if setup is not None:
            setup()
        full = [*args, "--sysfs", SYSFS]
        res, code, err = run(full)
        if code != 0:
            print(f"{name} FAIL: code={code} err={err}")
            failed += 1
            continue
        if not check(res):
            print(f"{name} FAIL: got {res}")
            failed += 1
            continue
        print(f"{name} pass: device={res.get('device')} | {res.get('reason')}")
        passed += 1

    print(f"\n{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
