"""The first page load after a restart should not pay for a cold cache.

Two reads are cached but expensive the first time: the walk of the models
folder, and the assistant's digest. Both used to be built on the first request
that wanted them, which on a share showed up in the container log as a slow
request minutes after starting.
"""
import pytest

import src.app as app_module
from src.app import _prewarm_digest, _prewarm_library


class FakeSettings:
    def __init__(self, models_dir):
        self._models_dir = models_dir
        self.asked = 0

    def get(self, key):
        self.asked += 1
        return {"models_dir": self._models_dir}


def test_the_library_is_walked_before_any_request(tmp_path, monkeypatch):
    from src import library

    library.forget_scans()
    walked = []
    real = library._scan_models
    monkeypatch.setattr(library, "_scan_models",
                        lambda d: (walked.append(d), real(d))[1])

    _prewarm_library(FakeSettings(str(tmp_path)))
    assert walked == [str(tmp_path)], "the models folder should be walked by the warm-up"


def test_a_broken_models_folder_does_not_stop_startup(tmp_path, monkeypatch):
    from src import library

    def boom(_d):
        raise OSError("the share is not mounted")

    library.forget_scans()
    monkeypatch.setattr(library, "_scan_models", boom)
    _prewarm_library(FakeSettings(str(tmp_path)))  # must not raise


def test_the_digest_is_built_before_any_request():
    calls = []

    class FakeAssistant:
        def context_tokens(self, digest=None):
            calls.append(digest)
            return 123

    _prewarm_digest(FakeAssistant())
    assert calls == [None], "the warm-up should build the digest"


def test_a_failing_digest_does_not_stop_startup():
    class BadAssistant:
        def context_tokens(self, digest=None):
            raise RuntimeError("the database is not up yet")

    _prewarm_digest(BadAssistant())  # must not raise


def test_the_warm_ups_run_without_the_request_path(tmp_path, monkeypatch):
    """They are started as threads, so a slow one cannot delay startup."""
    import threading

    started = []
    real_thread = threading.Thread

    class Recording(real_thread):
        def start(self):
            started.append(self.name)
            return real_thread.start(self)

    monkeypatch.setattr(app_module.threading, "Thread", Recording)
    app = app_module.create_app({
        "DB": str(tmp_path / "t.db"), "RUNS_DIR": str(tmp_path / "runs"), "ARGOS_URL": "",
        "MODELS_DIR": str(tmp_path / "models"), "HF_ENDPOINT": "http://127.0.0.1:9",
        "TELEMETRY": False, "BACKGROUND": True,
    })
    try:
        for name in ("inventory-prewarm", "library-prewarm", "assistant-prewarm"):
            assert name in started, f"{name} was not started; started: {started}"
    finally:
        app.extensions["runner"].stop_all()
        app.extensions["pb"].store.close()
