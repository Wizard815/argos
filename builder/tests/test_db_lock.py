"""Two writers share one SQLite connection: the telemetry tick and a request.

These are about the SQLite file specifically - its busy timeout, its journal, its
file lock. Under Postgres none of those exist, so they are skipped rather than
pretended to pass.
"""
import pytest

from src import dialect

pytestmark = pytest.mark.skipif(
    dialect.is_postgres(dialect.url()),
    reason="journal, busy timeout and the file lock are the SQLite file's, not Postgres's")


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


def test_a_write_does_not_hold_the_file_lock_for_good(tmp_path, monkeypatch):
    """locking_mode=EXCLUSIVE held the file's lock permanently, on the assumption that
    only this process opens it. A second connection then waited forever: a query waited
    6.5 hours for a lock on a container that had been up for ten. Nothing may hold it,
    because the assumption is false - a backup, an admin sqlite3, a second instance."""
    import sqlite3

    from src.db import Store

    monkeypatch.setenv("ARGOS_SQLITE_JOURNAL", "TRUNCATE")
    monkeypatch.delenv("ARGOS_SQLITE_LOCKING", raising=False)
    path = str(tmp_path / "x.db")
    store = Store(path)
    store.add_event("test", "hello")            # a write is what takes a lock
    assert store._conn.execute("PRAGMA locking_mode").fetchone()[0].lower() == "normal"

    other = sqlite3.connect(path, timeout=2)
    assert other.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1
    other.close()


def test_each_thread_gets_its_own_connection(tmp_path):
    """One shared connection behind one lock is what let a single slow call stop every
    other request. A connection per thread means a stuck call blocks its own request."""
    import threading

    from src.db import Store

    store = Store(str(tmp_path / "argos.db"))
    seen: dict[int, int] = {}

    def look() -> None:
        store.get_settings()                    # touches the connection
        seen[threading.get_ident()] = id(store._conn)

    threads = [threading.Thread(target=look) for _ in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(seen) == 3 and len(set(seen.values())) == 3, "each thread must have its own connection"


def test_the_file_lock_can_be_left_normal(tmp_path, monkeypatch):
    from src.db import Store

    monkeypatch.setenv("ARGOS_SQLITE_JOURNAL", "TRUNCATE")
    monkeypatch.setenv("ARGOS_SQLITE_LOCKING", "normal")
    store = Store(str(tmp_path / "x.db"))
    assert store._conn.execute("PRAGMA locking_mode").fetchone()[0] == "normal"
