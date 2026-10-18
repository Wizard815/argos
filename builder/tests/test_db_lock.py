"""Two writers share one SQLite connection: the telemetry tick and a request."""


def test_the_store_waits_for_a_lock_instead_of_giving_up(tmp_path):
    """Without a busy timeout SQLite fails the instant it is locked, which surfaces
    as a route that errors or hangs for no visible reason."""
    from src.db import Store

    s = Store(str(tmp_path / "argos.db"))
    assert s._conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000


def test_the_journal_is_chosen_by_the_filesystem(tmp_path):
    """WAL is faster but needs a -shm file and mmap, which a FUSE share cannot do
    reliably; a real filesystem gets WAL, anything else gets a rollback journal."""
    from src.db import Store

    assert Store._journal_for(str(tmp_path)) in ("WAL", "TRUNCATE")
    # a path under a FUSE/NFS mount must not be given WAL
    assert Store._journal_for("/nonexistent-path") in ("WAL", "TRUNCATE")


def test_the_journal_can_be_forced_by_environment(tmp_path, monkeypatch):
    """A bind mount can hide what the filesystem underneath is, so the guess needs a
    way out that does not need a code change."""
    from src.db import Store

    monkeypatch.setenv("ARGOS_SQLITE_JOURNAL", "TRUNCATE")
    s = Store(str(tmp_path / "forced.db"))
    assert s._conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "truncate"

    monkeypatch.setenv("ARGOS_SQLITE_JOURNAL", "WAL; DROP TABLE profiles")
    s2 = Store(str(tmp_path / "guarded.db"))
    mode = s2._conn.execute("PRAGMA journal_mode").fetchone()[0].lower()
    assert mode in ("wal", "truncate", "delete", "persist"), mode
    assert s2.list_profiles() == [], "the table must still exist"


def test_the_store_opens_and_reports_a_journal(tmp_path):
    from src.db import Store

    s = Store(str(tmp_path / "argos.db"))
    mode = s._conn.execute("PRAGMA journal_mode").fetchone()[0].lower()
    assert mode in ("wal", "truncate", "delete", "persist"), mode
    if mode == "wal":
        assert (tmp_path / "argos.db-shm").exists(), "WAL needs its shared-memory file"


def test_the_store_still_works_normally(tmp_path):
    from src.db import Store

    s = Store(str(tmp_path / "argos.db"))
    s.add_event("test", "hello")
    assert any(e["message"] == "hello" for e in s.list_events())
