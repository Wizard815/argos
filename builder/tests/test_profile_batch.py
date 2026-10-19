"""A pull writes many profiles: they should cost one commit, not one each."""
import pytest

from src import dialect

# This one is about SQLite's rollback journal holding the file lock until commit.
# Postgres has no such lock, so the behaviour it describes does not exist there.
needs_sqlite = pytest.mark.skipif(
    dialect.is_postgres(dialect.url()),
    reason="the writer lock is the SQLite file's, not Postgres's")


def test_a_batch_of_profiles_is_committed_once(tmp_path):
    from src.db import Store

    path = str(tmp_path / "argos.db")
    s = Store(path)
    for i in range(5):
        s.save_profile({"name": f"p{i}", "launch_command": "llama-server -m /m/x.gguf"}, commit=False)
    s.commit()

    # a second connection seeing them is what proves the commit happened
    fresh = Store(path)
    assert sorted(p["name"] for p in fresh.list_profiles()) == [f"p{i}" for i in range(5)]


@needs_sqlite
def test_an_uncommitted_write_holds_the_lock(tmp_path):
    """With a rollback journal a writer holds the lock until it commits. That is the
    reason commit=False is only for a run of writes that ends in one commit, and it is
    also why the journal mode matters on a slow filesystem."""
    import sqlite3

    from src.db import Store

    path = str(tmp_path / "argos.db")
    s = Store(path)
    s.save_profile({"name": "held", "launch_command": "llama-server -m /m/x.gguf"}, commit=False)

    # A plain connection, not a Store: Store's migrations write, and a writer holding
    # the lock would make this a different test.
    other = sqlite3.connect(path, timeout=2)
    assert other.execute("SELECT COUNT(*) FROM profiles").fetchone()[0] == 0
    other.close()

    s.commit()
    again = sqlite3.connect(path, timeout=2)
    assert again.execute("SELECT COUNT(*) FROM profiles").fetchone()[0] == 1
    again.close()
