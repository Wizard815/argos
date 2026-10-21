"""A page must never wait for a walk of the models folder.

The scan cache re-walked inside its lock once an entry passed SCAN_TTL_S. On a
share where the walk is slower than that TTL, every request re-walked and every
other thread queued behind the lock while it did - so one slow folder filled the
server's thread pool, the task queue grew without limit, and the page timed out.
"""
import time

import pytest

import src.library as lib


@pytest.fixture(autouse=True)
def clean():
    lib.forget_scans()
    yield
    lib.forget_scans()


def _slow_scanner(monkeypatch, walks, delay=0.4):
    real = lib._scan_models

    def slow(models_dir):
        walks.append(models_dir)
        time.sleep(delay)
        return real(models_dir)

    monkeypatch.setattr(lib, "_scan_models", slow)
    return real


def test_a_stale_entry_is_served_at_once_and_rebuilt_in_the_background(tmp_path, monkeypatch):
    walks = []
    _slow_scanner(monkeypatch, walks)

    # The first ask has nothing to serve, so it waits - that is the contract.
    first = lib.scan_models(str(tmp_path))
    assert len(walks) == 1

    # Age the entry past its TTL.
    key = ("models", str(tmp_path))
    with lib._scan_lock:
        stamp, value = lib._scans[key]
        lib._scans[key] = (stamp - lib.SCAN_TTL_S - 1, value)

    started = time.monotonic()
    served = lib.scan_models(str(tmp_path))
    waited = time.monotonic() - started

    assert served == first, "the stale list should be what comes back"
    assert waited < 0.2, f"the request waited {waited:.2f}s for a walk it should not wait for"

    # ...and a walk is under way, off the request.
    deadline = time.monotonic() + 3
    while len(walks) < 2 and time.monotonic() < deadline:
        time.sleep(0.02)
    assert len(walks) == 2, "the background rebuild did not run"


def test_only_one_rebuild_runs_at_a_time(tmp_path, monkeypatch):
    walks = []
    _slow_scanner(monkeypatch, walks)
    lib.scan_models(str(tmp_path))

    key = ("models", str(tmp_path))
    with lib._scan_lock:
        stamp, value = lib._scans[key]
        lib._scans[key] = (stamp - lib.SCAN_TTL_S - 1, value)

    # A burst of requests while the rebuild is running must not start more walks.
    for _ in range(20):
        lib.scan_models(str(tmp_path))
    time.sleep(0.6)
    assert len(walks) == 2, f"{len(walks)} walks for one stale entry; expected one rebuild"


def test_a_failed_rebuild_keeps_the_last_good_list(tmp_path, monkeypatch):
    real = lib._scan_models
    lib.scan_models(str(tmp_path))

    def broken(models_dir):
        raise OSError("the share went away")

    monkeypatch.setattr(lib, "_scan_models", broken)
    key = ("models", str(tmp_path))
    with lib._scan_lock:
        stamp, value = lib._scans[key]
        lib._scans[key] = (stamp - lib.SCAN_TTL_S - 1, value)

    assert lib.scan_models(str(tmp_path)) == []  # tmp_path has no models in it
    time.sleep(0.4)  # the rebuild fails in the background
    monkeypatch.setattr(lib, "_scan_models", real)
    assert lib.scan_models(str(tmp_path)) == [], "the cache should still answer after a failed rebuild"
