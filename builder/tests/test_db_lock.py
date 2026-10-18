"""Two writers share one SQLite connection: the telemetry tick and a request."""


def test_the_store_waits_for_a_lock_instead_of_giving_up(tmp_path):
    """Without a busy timeout SQLite fails the instant it is locked, which surfaces
    as a route that errors or hangs for no visible reason."""
    from src.db import Store

    s = Store(str(tmp_path / "argos.db"))
    assert s._conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000


def test_the_store_avoids_the_shared_memory_file(tmp_path):
    """WAL coordinates through the -shm file and mmap, and on a FUSE filesystem
    (Unraid's /mnt/user is shfs) that is what fails: the -wal stops checkpointing,
    grows, and reads block forever. The journal must not need shared memory."""
    from src.db import Store

    s = Store(str(tmp_path / "argos.db"))
    mode = s._conn.execute("PRAGMA journal_mode").fetchone()[0].lower()
    assert mode != "wal", mode
    assert mode in ("truncate", "delete", "persist"), mode
    assert not (tmp_path / "argos.db-shm").exists(), "no shared-memory file should be needed"


def test_the_store_still_works_normally(tmp_path):
    from src.db import Store

    s = Store(str(tmp_path / "argos.db"))
    s.add_event("test", "hello")
    assert any(e["message"] == "hello" for e in s.list_events())
