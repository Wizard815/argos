import time

import pytest

from .conftest import fake_command


def wait_ready(client, launch_id, timeout=15.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        launch = client.get(f"/api/launches/{launch_id}").get_json()["launch"]
        if launch["ready"]:
            return launch
        time.sleep(0.1)
    raise AssertionError(client.get(f"/api/launches/{launch_id}/log").get_data(as_text=True))


def test_ui_and_static(client):
    assert b"profilebuilder" in client.get("/").data
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/static/style.css").status_code == 200
    assert client.get("/static/../app.py").status_code == 404
    assert client.get("/static/../../data.db").status_code == 404
    assert client.get("/whatever").status_code == 404
    r = client.get("/api/nope")
    assert r.status_code == 404 and r.get_json() == {"ok": False, "error": "not found"}


def test_command_parse_and_build(client):
    r = client.post("/api/command/parse", json={"command": "llama-server -m /m/Qwen3-8B-Q4_K_M.gguf -ngl 20"})
    j = r.get_json()
    assert j["ok"] and j["base_model"] == "Qwen3-8B-Q4_K_M"
    assert j["args"][1] == {"flag": "-ngl", "name": "n-gpu-layers", "value": "20"}

    r = client.post("/api/command/build", json={
        "command": j["command"], "edits": {"n-gpu-layers": "99", "flash-attn": "on"},
        "env": {"CUDA_VISIBLE_DEVICES": "1"}, "name": "q"})
    j = r.get_json()
    assert j["command"] == "CUDA_VISIBLE_DEVICES=1 llama-server -m /m/Qwen3-8B-Q4_K_M.gguf -ngl 99 --flash-attn on"
    assert '"q":' in j["llama_swap_yaml"]
    assert j["multiline"].count("\\\n") == 3

    assert client.post("/api/command/parse", json={"command": "llama-server 'x"}).status_code == 400
    assert client.post("/api/command/build", json={"command": "llama-server", "edits": [1]}).status_code == 400
    assert client.post("/api/command/build", json={"command": "llama-server", "arg_edits": {"x": 1}}).status_code == 400
    assert client.post("/api/command/parse", data="not json").status_code == 400


def test_profiles_crud(client):
    assert client.post("/api/profiles", json={"name": "x"}).status_code == 400
    assert client.post("/api/profiles", json={"name": "x", "launch_command": "llama-server 'x"}).status_code == 400
    p = client.post("/api/profiles", json={"name": "fast", "base_model": "Q", "launch_command": "llama-server -c 8192 -fa on"}).get_json()["profile"]
    listed = client.get("/api/profiles").get_json()["profiles"]
    assert listed[0]["summary"]["ctx_size"] == 8192 and listed[0]["summary"]["flash_attn"] == "on"
    client.post("/api/profiles", json={"profile_id": p["profile_id"], "name": "faster", "launch_command": "llama-server"})
    assert [x["name"] for x in client.get("/api/profiles").get_json()["profiles"]] == ["faster"]
    assert client.delete(f"/api/profiles/{p['profile_id']}").status_code == 200
    assert client.delete(f"/api/profiles/{p['profile_id']}").status_code == 404
    assert client.delete("/api/profiles/bad id!").status_code == 404


def test_catalog_crud(client):
    assert client.post("/api/catalog", json={"kind": "lora", "base_model": "x", "file_path": "y"}).status_code == 400
    assert client.post("/api/catalog", json={"kind": "mmproj", "base_model": "x"}).status_code == 400
    fid = client.post("/api/catalog", json={"kind": "mmproj", "base_model": "gemma", "file_path": "/m/mm.gguf"}).get_json()["id"]
    assert client.get("/api/catalog?kind=mmproj").get_json()["files"][0]["id"] == fid
    assert client.get("/api/catalog?kind=mtp").get_json()["files"] == []
    assert client.get("/api/models").get_json()["models"] == ["gemma"]
    assert client.delete(f"/api/catalog/{fid}").status_code == 200
    assert client.delete(f"/api/catalog/{fid}").status_code == 404


def test_launch_bench_record_flow(client):
    assert client.post("/api/launches", json={}).status_code == 400
    r = client.post("/api/launches", json={"command": "no-such-binary-xyz --port 1"})
    assert r.status_code == 400 and "could not start" in r.get_json()["error"]

    cmd = "CUDA_VISIBLE_DEVICES=0 " + fake_command("-ngl", "99", "-c", "2048")
    launch = client.post("/api/launches", json={"command": cmd, "base_model": "Fake-8B", "profile_name": "p1"}).get_json()["launch"]
    lid = launch["launch_id"]
    wait_ready(client, lid)
    assert client.get("/api/launches").get_json()["launches"][0]["ready"] is True

    assert client.post(f"/api/launches/{lid}/bench", json={"n_predict": "x"}).status_code == 400
    run = client.post(f"/api/launches/{lid}/bench", json={"prompt_words": 300, "n_predict": 64}).get_json()["run"]
    assert run["log_source"] == "bench" and run["success"] == 1
    assert run["output_tokens"] == 64 and run["tokens_per_second"] > 0
    assert (run["base_model"], run["profile_name"], run["launch_id"]) == ("Fake-8B", "p1", lid)
    assert run["n_ctx"] == 2048 and run["layers_offloaded"] == "33/33" and run["gpu_layers"] == "99"
    assert run["mem_used_gb"] == 5.15  # from the fake log's CUDA0 buffers

    logged = client.post(f"/api/launches/{lid}/record", json={"note": "from log"}).get_json()["run"]
    assert logged["log_source"] == "log"
    assert logged["tokens_per_second"] == pytest.approx(run["tokens_per_second"], abs=0.01)  # log has 2 decimals

    assert client.get(f"/api/launches/{lid}/log?tail=40").get_data(as_text=True)
    stopped = client.post(f"/api/launches/{lid}/stop").get_json()["launch"]
    assert stopped["running"] is False and stopped["stopped"] is True
    assert client.post(f"/api/launches/{lid}/bench", json={}).status_code == 400
    assert client.get("/api/launches/run-nope").status_code == 404

    runs = client.get("/api/runs").get_json()["runs"]
    assert len(runs) == 2
    best = client.get("/api/analytics/best").get_json()["rows"]
    assert best[0]["base_model"] == "Fake-8B" and best[0]["runs"] == 2
    env = {r["flag"]: r for r in client.get("/api/env-flags").get_json()["rows"]}
    assert env["CUDA_VISIBLE_DEVICES"]["last_used_for"] == "Fake-8B"
    assert env["CUDA_VISIBLE_DEVICES"]["last_tps"] == pytest.approx(run["tokens_per_second"], abs=0.01)
    assert client.delete(f"/api/runs/{runs[0]['run_id']}").status_code == 200
    assert len(client.get("/api/runs").get_json()["runs"]) == 1


def test_integrations_endpoint(client):
    j = client.get("/api/integrations").get_json()
    assert j["odysseus"]["ok"] and len(j["odysseus"]["commands"]) == 2
    assert j["llama_swap"]["config_ok"] and len(j["llama_swap"]["commands"]) == 2
    assert "server" not in j["llama_swap"]


def test_sigterm_stops_launched_servers(tmp_path):
    """Stopping the app (docker stop / systemd) must not orphan llama-servers."""
    import json
    import os
    import signal
    import subprocess
    import sys
    import urllib.request

    from src.runner import free_port

    from .conftest import ROOT

    port = free_port()
    env = {**os.environ, "PORT": str(port), "HOST": "127.0.0.1",
           "PROFILEBUILDER_DB": str(tmp_path / "x.db"), "PROFILEBUILDER_RUNS_DIR": str(tmp_path / "runs")}
    app = subprocess.Popen([sys.executable, "-m", "src.app"], cwd=ROOT, env=env,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{port}"

    def call(method, path, body=None):
        req = urllib.request.Request(base + path, method=method, data=json.dumps(body).encode() if body else None,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as r:
            return json.loads(r.read())

    try:
        deadline = time.time() + 15
        while True:
            try:
                call("GET", "/api/launches")
                break
            except OSError:
                if time.time() > deadline:
                    raise
                time.sleep(0.1)
        launch = call("POST", "/api/launches", {"command": fake_command()})["launch"]
        while not call("GET", f"/api/launches/{launch['launch_id']}")["launch"]["ready"]:
            assert time.time() < deadline + 15
            time.sleep(0.1)
        app.send_signal(signal.SIGTERM)
        app.wait(timeout=10)
    finally:
        if app.poll() is None:
            app.kill()
    with pytest.raises(OSError):
        urllib.request.urlopen(f"http://127.0.0.1:{launch['port']}/health", timeout=1)
