"""An empty Postgres next to a populated SQLite file should say so.

The compose file once defaulted ARGOS_DATABASE_URL to the Postgres URL, so every
deployment that had not opted in quietly switched to a freshly created, empty
database. The app came up in a fraction of a second showing no profiles, no runs
and no history - which reads as "fast" until you notice the data is gone.

It cannot fail: a genuinely new install looks exactly the same. It should never
be silent, though, and this is the test that keeps it from being.
"""
import logging

import pytest

from src import dialect

pytestmark = pytest.mark.skipif(
    not dialect.is_postgres(dialect.url()),
    reason="this is about noticing an empty Postgres; run with ARGOS_DATABASE_URL set")


def test_an_empty_postgres_next_to_a_sqlite_file_warns(tmp_path, monkeypatch, caplog):
    from src.db import Store

    dsn = dialect.url()
    schema = __import__("os").environ.get("ARGOS_PG_SCHEMA")

    # A SQLite database with something in it, sitting where /data would have it.
    monkeypatch.delenv("ARGOS_DATABASE_URL", raising=False)
    monkeypatch.delenv("ARGOS_PG_SCHEMA", raising=False)
    seed = Store(str(tmp_path / "builder.db"))
    seed.save_profile({"name": "on the file", "launch_command": "llama-server -m /m/x.gguf"})
    seed.close()

    # Now the same path, opened on an empty Postgres.
    monkeypatch.setenv("ARGOS_DATABASE_URL", dsn)
    if schema:
        monkeypatch.setenv("ARGOS_PG_SCHEMA", schema)
    with caplog.at_level(logging.WARNING, logger="argos.db"):
        store = Store(str(tmp_path / "builder.db"))
        store.close()

    assert any("showing no data" in r.getMessage() for r in caplog.records), \
        "an empty Postgres beside a populated file was silent"
    assert any("migrate_to_postgres.py" in r.getMessage() for r in caplog.records), \
        "the warning should name the script that fixes it"


def test_a_postgres_with_data_does_not_warn(tmp_path, monkeypatch, caplog):
    from src.db import Store

    store = Store(str(tmp_path / "builder.db"))
    store.save_profile({"name": "on postgres", "launch_command": "llama-server -m /m/x.gguf"})
    with caplog.at_level(logging.WARNING, logger="argos.db"):
        again = Store(str(tmp_path / "builder.db"))
        again.close()
    assert not any("showing no data" in r.getMessage() for r in caplog.records)
    store.close()
