"""Load every page's route against a synthetic database, at three volumes.

    python scripts/bench_pages.py            # low, medium, high
    python scripts/bench_pages.py high       # one volume

"The page takes a minute" is impossible to argue about from the outside, and three
rounds of diagnosis guessed at three different causes. This seeds a database with a
known amount of history, starts the builder against it on a throwaway port and times
every route the UI calls - so a result points at the code or at the machine, not at a
guess. On a normal disk every route is under a second even at 300k requests; the same
code against an 8.5 MiB database on a FUSE share times out, which is the difference
this is built to show.
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
WORK = os.environ.get("ARGOS_BENCH_DIR") or os.path.join(ROOT, "bench-data")

VOLUMES = {"low": (2_000, 2_000, 8101), "medium": (50_000, 20_000, 8102), "high": (300_000, 100_000, 8103)}

ROUTES = [
    "/api/settings", "/api/profiles", "/api/launches", "/api/runs?limit=1", "/api/runs?limit=1000",
    "/api/requests?range=24h&limit=500", "/api/events?limit=100", "/api/gpu/series?range=1h",
    "/api/inventory", "/api/catalog", "/api/downloads", "/api/library",
]


def seed(path, n_requests, n_gpu, n_runs=200):
    sys.path.insert(0, ROOT)
    from src.db import Store

    if os.path.exists(path):
        os.remove(path)
    store = Store(path)
    now = time.time()
    rows = [{"source_id": i, "ts": now - (n_requests - i) * 0.5, "model": "model-%d" % (i % 12),
             "status": 200, "input_tokens": 500 + i % 900, "output_tokens": 100 + i % 400,
             "prompt_per_second": 300 + i % 700, "tokens_per_second": 20 + i % 60,
             "duration_ms": 900} for i in range(n_requests)]
    for i in range(0, len(rows), 5000):
        store.add_requests(rows[i:i + 5000])
    samples = [{"ts": now - (n_gpu - i), "gpu": i % 2, "name": "MI50", "util": 50 + i % 50,
                "mem_used_mb": 20000 + i % 5000, "mem_total_mb": 32768, "temp_c": 60,
                "power_w": 200, "source": "bench"} for i in range(n_gpu)]
    for i in range(0, len(samples), 5000):
        store.add_gpu_samples(samples[i:i + 5000])
    for i in range(n_runs):
        store.add_run({"run_id": "run-%d" % i, "base_model": "model-%d" % (i % 12),
                       "created_at": now - i, "launch_command": "llama-server -m /models/x.gguf -ngl 99 -c 8192"})


def probe(port, path, timeout=30):
    started = time.time()
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d%s" % (port, path), timeout=timeout) as response:
            return time.time() - started, response.status, len(response.read())
    except urllib.error.HTTPError as exc:
        return time.time() - started, exc.code, 0
    except Exception as exc:  # a failure here is the measurement
        return time.time() - started, type(exc).__name__, 0


def run(name):
    n_requests, n_gpu, port = VOLUMES[name]
    os.makedirs(WORK, exist_ok=True)
    db = os.path.join(WORK, "%s.db" % name)
    started = time.time()
    seed(db, n_requests, n_gpu)
    print("\n===== %s: %s requests, %s gpu samples (seeded in %.0fs)"
          % (name, format(n_requests, ","), format(n_gpu, ","), time.time() - started))

    env = dict(os.environ, ARGOS_DB=db, PORT=str(port), ARGOS_READ_CACHE_S="0")
    log = os.path.join(WORK, "%s.log" % name)
    with open(log, "w", encoding="utf-8") as handle:
        proc = subprocess.Popen([sys.executable, "-m", "src.app"], cwd=ROOT, env=env,
                                stdout=handle, stderr=subprocess.STDOUT, text=True)
    up = False
    for _ in range(40):
        time.sleep(0.4)
        if probe(port, "/api/settings", 3)[1] == 200:
            up = True
            break
    if not up:
        proc.kill()
        print("  the builder did not come up; see %s" % log)
        return

    worst = 0.0
    for path in ROUTES:
        took, status, size = probe(port, path)
        worst = max(worst, took)
        print("  %-34s %6.2fs  %s  %8.1f KiB" % (path, took, status, size / 1024))
    print("  worst: %.2fs" % worst)

    proc.send_signal(signal.SIGTERM)
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


if __name__ == "__main__":
    for which in (sys.argv[1:] or list(VOLUMES)):
        if which in VOLUMES:
            run(which)
        else:
            print("unknown volume %r; pick from %s" % (which, ", ".join(VOLUMES)))
