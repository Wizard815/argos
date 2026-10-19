"""The thread dump answers on the machine the app runs on, and nowhere else."""


def test_debug_threads_lists_threads_with_stacks(client):
    r = client.get("/api/debug/threads", environ_overrides={"REMOTE_ADDR": "127.0.0.1"})
    assert r.status_code == 200
    threads = r.get_json()["threads"]
    assert threads and all("stack" in t and "thread" in t for t in threads)


def test_debug_threads_refuses_the_network(client):
    r = client.get("/api/debug/threads", environ_overrides={"REMOTE_ADDR": "192.168.1.9"})
    assert r.status_code == 403
