"""The sidebar wants a number, not the history."""
import time


def test_the_run_list_carries_a_total(client, app):
    store = app.extensions["pb"].store
    for i in range(3):
        store.add_run({"run_id": f"r{i}", "base_model": "m", "created_at": time.time(), "launch_command": "llama-server -m /m/x.gguf"})

    out = client.get("/api/runs?limit=1").get_json()
    assert out["ok"]
    assert len(out["runs"]) == 1, "one row is enough to know it is not empty"
    assert out["total"] == 3, "and the count comes with it"


def test_a_filtered_list_still_reports_the_whole_total(client, app):
    """The total is the table's, not the page's: a badge showing 1 for a filtered list
    would be a different number every time."""
    store = app.extensions["pb"].store
    for i in range(2):
        store.add_run({"run_id": f"a{i}", "base_model": "alpha", "created_at": time.time(), "launch_command": "llama-server -m /m/a.gguf"})
    store.add_run({"run_id": "b0", "base_model": "beta", "created_at": time.time(), "launch_command": "llama-server -m /m/b.gguf"})

    out = client.get("/api/runs?base_model=alpha&limit=10").get_json()
    assert [r["base_model"] for r in out["runs"]] == ["alpha", "alpha"]
    assert out["total"] == 3
