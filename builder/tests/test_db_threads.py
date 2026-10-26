"""A connection per thread must not outlive its thread: on Postgres they ran out ("too many clients")."""
import gc
import threading

from src.db import Store


def test_Store_a_finished_thread_gives_its_connection_back(tmp_path, monkeypatch):
    store = Store(str(tmp_path / "t.db"))
    store.get_settings()
    released = []
    real = Store._release
    monkeypatch.setattr(Store, "_release", lambda self, conn: (released.append(conn), real(self, conn)))

    def work():
        store.get_settings()
        store.add_event("test", "from a short-lived thread")

    for _ in range(40):
        t = threading.Thread(target=work)
        t.start()
        t.join()
        del t
    gc.collect()
    assert len(released) == 40, f"{40 - len(released)} connections outlived their threads"
    assert len(store.list_events(kind="test")) == 40, "the writes were committed before the connections closed"
