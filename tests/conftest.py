import os
import sys

import pytest

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
        "ODYSSEUS_COOKBOOK": os.path.join(FIXTURES, "cookbook_state.json"),
        "LLAMA_SWAP_CONFIG": os.path.join(FIXTURES, "llama-swap.yaml"),
        "LLAMA_SWAP_URL": "",
    })
    yield app
    app.extensions["runner"].stop_all()


@pytest.fixture
def client(app):
    return app.test_client()
