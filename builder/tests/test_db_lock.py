"""Two writers share one SQLite connection: the telemetry tick and a request."""


def test_the_store_waits_for_a_lock_instead_of_giving_up(tmp_path):
    """Without a busy timeout SQLite fails the instant it is locked, which surfaces
    as a route that errors or hangs for no visible reason."""
    from src.db import Store

    s = Store(str(tmp_path / "argos.db"))
    assert s._conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
    assert s._conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"


def test_the_store_still_works_normally(tmp_path):
    from src.db import Store

    s = Store(str(tmp_path / "argos.db"))
    s.add_event("test", "hello")
    assert any(e["message"] == "hello" for e in s.list_events())
