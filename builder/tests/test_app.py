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
    assert b"Argos" in client.get("/").data
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/static/style.css").status_code == 200
    assert client.get("/static/../app.py").status_code == 404
    assert client.get("/static/../../data.db").status_code == 404
    assert client.get("/whatever").status_code == 404
    r = client.get("/api/nope")
    assert r.status_code == 404 and r.get_json() == {"ok": False, "error": "not found"}


def test_command_parse_and_build(client):
    r = client.post("/api/command/parse", json={"command": "llama-server -m /m/Qwen3-8B-Q4_K_M.gguf -ngl 20",
                                                "format": "plain"})
    j = r.get_json()
    assert j["ok"] and j["base_model"] == "Qwen3-8B-Q4_K_M" and j["model_id"] == "qwen3-8b-q4_k_m"
    assert j["args"][1] == {"flag": "-ngl", "name": "n-gpu-layers", "value": "20"}

    r = client.post("/api/command/build", json={
        "command": j["command"], "edits": {"n-gpu-layers": "99", "flash-attn": "on"},
        "env": {"CUDA_VISIBLE_DEVICES": "1"}, "name": "q", "format": "plain"})
    j = r.get_json()
    assert j["command"] == "CUDA_VISIBLE_DEVICES=1 llama-server -m /m/Qwen3-8B-Q4_K_M.gguf -ngl 99 --flash-attn on"
    assert "engine_yaml" not in j
    assert j["multiline"].count("\\\n") == 3

    # default format is Odysseus' Cookbook shape
    j = client.post("/api/command/parse", json={"command": "llama-server -c 4096 -ngl 20 -m /m/a.gguf -fa on"}).get_json()
    assert j["format"] == "odysseus"
    assert j["command"] == 'llama-server --model "/m/a.gguf" --host 0.0.0.0 --port 8080 -ngl 20 -c 4096 -fa on'

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
    assert j["odysseus"] == {"configured": False, "ok": True, "error": "", "commands": []}
    assert "engine" not in j and "export" not in j


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
           "ARGOS_DB": str(tmp_path / "x.db"), "ARGOS_RUNS_DIR": str(tmp_path / "runs")}
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


def test_flag_library_counts_how_profiles_use_each_flag(client):
    client.post("/api/profiles", json={"name": "a", "launch_command": "llama-server -m /m/a.gguf --jinja --temp 0.6 -lm dio --weird-new 3"})
    client.post("/api/profiles", json={"name": "b", "launch_command": "llama-server -m /m/b.gguf --jinja --temp 0.2"})
    rows = {r["flag"]: r for r in client.get("/api/flags").get_json()["rows"]}
    assert rows["jinja"]["profiles"] == 2 and rows["jinja"]["spellings"] == ["--jinja"] and rows["jinja"]["category"] == "Chat & tools"
    assert rows["temp"]["profiles"] == 2 and sorted(rows["temp"]["values"]) == ["0.2", "0.6"]
    assert rows["load-mode"]["profiles"] == 1 and rows["load-mode"]["values"] == ["dio"] and "-lm" in rows["load-mode"]["spellings"]
    assert rows["weird-new"]["category"] == "Seen in your profiles" and rows["weird-new"]["profiles"] == 1
    assert rows["top-k"]["profiles"] == 0 and rows["top-k"]["description"]
    assert len({r["flag"] for r in rows.values()}) == len(rows), "no flag is listed twice"


def test_ui_files_are_versioned_so_a_browser_never_runs_an_old_app_js(client):
    page = client.get("/")
    html = page.get_data(as_text=True)
    assert page.headers["Cache-Control"] == "no-cache"
    assert '/static/app.js?v=' in html and '/static/style.css?v=' in html
    assert "immutable" in client.get("/static/app.js?v=123").headers["Cache-Control"]
    assert client.get("/static/app.js").headers["Cache-Control"] == "no-cache"


def test_flags_page_add_edit_remove_and_restore(client):
    def row(kind, name):
        rows = client.get("/api/flags" if kind == "flag" else "/api/env-flags").get_json()["rows"]
        return next((r for r in rows if r["flag"] == name), None)

    assert client.post("/api/flags/flag", json={"name": "--my-flag", "category": "Mine", "description": "does a thing",
                                                 "spellings": "-mf --my-flag"}).get_json()["name"] == "my-flag"
    mine = row("flag", "my-flag")
    assert mine["custom"] and mine["spellings"] == ["-mf", "--my-flag"] and mine["description"] == "does a thing"
    assert client.post("/api/flags/flag", json={"name": "-c"}).status_code == 400, "ctx-size is already on the list"
    assert client.post("/api/flags/flag", json={"name": "bad name!"}).status_code == 400

    assert client.put("/api/flags/flag/ctx-size", json={"notes": "64k is my default"}).get_json()["ok"]
    ctx = row("flag", "ctx-size")
    assert ctx["edited"] and ctx["notes"] == "64k is my default" and ctx["description"], "other texts are kept"

    assert client.delete("/api/flags/flag/ctx-size").get_json()["result"] == "hidden"
    assert row("flag", "ctx-size")["removed"] is True
    assert client.post("/api/flags/flag/ctx-size/restore").get_json()["ok"]
    restored = row("flag", "ctx-size")
    assert not restored["removed"] and not restored["edited"] and restored["notes"] != "64k is my default"

    assert client.delete("/api/flags/flag/my-flag").get_json()["result"] == "deleted"
    assert row("flag", "my-flag") is None
    assert client.post("/api/flags/flag/my-flag/restore").status_code == 404

    assert client.post("/api/flags/env", json={"name": "MY_VAR", "description": "x", "recommended": "1"}).get_json()["ok"]
    assert row("env", "MY_VAR")["custom"] and row("env", "MY_VAR")["recommended"] == "1"
    assert client.post("/api/flags/env", json={"name": "9BAD"}).status_code == 400
    assert client.delete("/api/flags/env/HIP_VISIBLE_DEVICES").get_json()["result"] == "hidden"
    assert row("env", "HIP_VISIBLE_DEVICES")["removed"]
    assert client.put("/api/flags/env/NOPE", json={"description": "x"}).status_code == 404
    assert client.post("/api/flags/other", json={"name": "x"}).status_code == 404
