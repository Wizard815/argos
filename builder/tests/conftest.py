import os
import sys
import uuid

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


@pytest.fixture(autouse=True)
def _pg_schema(monkeypatch):
    """Give each test its own Postgres schema.

    The suite was written against a database file per test (tmp_path / "test.db").
    One Postgres database would have every test writing into the same tables, so
    the schema is what separates them instead. With no ARGOS_DATABASE_URL this
    does nothing and the file is used, exactly as before.
    """
    dsn = (os.environ.get("ARGOS_DATABASE_URL") or "").strip()
    if not dsn.startswith(("postgres://", "postgresql://")):
        yield
        return
    import psycopg

    name = "t" + uuid.uuid4().hex[:16]
    with psycopg.connect(dsn, autocommit=True) as c:
        c.execute('CREATE SCHEMA "' + name + '"')
    monkeypatch.setenv("ARGOS_PG_SCHEMA", name)
    try:
        yield
    finally:
        with psycopg.connect(dsn, autocommit=True) as c:
            # A drop waits on every open connection that touched these objects.
            # close() above should have released them; the timeout is so a missed
            # one turns into a leftover schema rather than a hung suite.
            c.execute("SET lock_timeout = '5s'")
            try:
                c.execute('DROP SCHEMA IF EXISTS "' + name + '" CASCADE')
            except Exception:
                pass


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
    pb = app.extensions.get("pb")
    if pb is not None:
        pb.store.close()  # Postgres holds locks until this happens; SQLite does not care


@pytest.fixture
def client(app):
    return app.test_client()
