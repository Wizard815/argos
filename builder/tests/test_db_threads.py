"""A connection per thread must not outlive its thread: on Postgres they ran out ("too many clients")."""
import gc
import threading

from src.db import Store


def test_Store_a_finished_thread_gives_its_connection_back(tmp_path):
    store = Store(str(tmp_path / "t.db"))
    store.get_settings()
    before = len(store._open)

    def work():
        store.get_settings()
        store.add_event("test", "from a short-lived thread")

    for _ in range(40):
        t = threading.Thread(target=work)
        t.start()
        t.join()
        del t
    gc.collect()
    assert len(store._open) <= before + 1, f"{len(store._open) - before} connections left open by finished threads"
    assert len(store.list_events(kind="test")) == 40, "the writes were committed before the connections closed"


def test_Store_close_still_reaches_every_connection(tmp_path):
    store = Store(str(tmp_path / "t.db"))
    store.get_settings()
    store.close()
    assert store._open == []
