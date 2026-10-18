import os
import sys

import pytest

# A few seconds of read cache deliberately means a read can be one poll old, which a
# test asserting state right after a write cannot have. The suite turns it off; the
# cache's own tests build one directly.
os.environ.setdefault("ARGOS_READ_CACHE_S", "0")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

FIXTURES = os.path.join(ROOT, "tests", "fixtures")
FAKE_SERVER = os.path.join(ROOT, "tests", "fake_llama_server.py")


def fake_command(*extra: str, port: str = "${PORT}") -> str:
    """A launch command that runs the fake llama-server with this interpreter."""
    return " ".join([sys.executable, FAKE_SERVER, "--port", port, "--load-delay", "0.1", *extra])


@pytest.fixture
def app(tmp_path):
    from src.app import create_app

    app = create_app({
        "DB": str(tmp_path / "test.db"),
        "RUNS_DIR": str(tmp_path / "runs"),
        "ARGOS_URL": "",
        "MODELS_DIR": str(tmp_path / "models"),
        "HF_ENDPOINT": "http://127.0.0.1:9",  # tests that need the Hub start a fake one
        "TELEMETRY": False,
        "BACKGROUND": False,
    })
    yield app
    app.extensions["runner"].stop_all()


@pytest.fixture
def client(app):
    return app.test_client()
