"""Profiles run on the server picked at the top of the Profiles page, like the server select in Odysseus' Cookbook."""
from src.command import parse_command
from src.servers import launch_parts, on_server

SERVER = {"id": 3, "name": "llamacpp", "kind": "ssh", "host": "root@mx-llamacpp-ssh", "port": 22, "gpu": "rocm",
          "llama_server": "/opt/llama/bin/llama-server", "engines": {}}


def test_ProfileServer_a_server_gets_a_launcher_of_its_own():
    parts = launch_parts(SERVER, key="/data/ssh/server-3")
    assert parts["program"] == ["/app/bin/remote-llama", "rocm_srv3"]
    assert parts["env"] == {"ROCM_SRV3_SSH_HOST": "mx-llamacpp-ssh", "ROCM_SRV3_SSH_PORT": "22", "ROCM_SRV3_SSH_USER": "root",
                            "ROCM_SRV3_SSH_BIN": "/opt/llama/bin/llama-server", "REMOTE_SSH_KEY": "/data/ssh/server-3"}
    detected = {**SERVER, "gpu": "auto", "engines": {"gpu": "cuda", "llama_server": "/usr/bin/llama-server"}}
    assert launch_parts(detected)["program"][1] == "cuda_srv3", "auto takes what the server's Check found"
    assert launch_parts({**SERVER, "kind": "local"}) is None


def test_ProfileServer_only_a_plain_llama_server_is_moved():
    parts = launch_parts(SERVER)
    cmd = on_server(parse_command("HIP_VISIBLE_DEVICES=0,1 llama-server --model /m/a.gguf --port ${PORT}"), parts).to_string()
    assert cmd.startswith("HIP_VISIBLE_DEVICES=0,1 ROCM_SRV3_SSH_HOST=mx-llamacpp-ssh ")
    assert " /app/bin/remote-llama rocm_srv3 --model /m/a.gguf" in cmd
    own = "/app/bin/remote-llama cuda --model /m/a.gguf"
    assert on_server(parse_command(own), parts).to_string() == own, "a profile that names its program keeps it"
    plain = "llama-server --model /m/a.gguf"
    assert on_server(parse_command(plain), None).to_string() == plain, "Local: as the base models run"


def test_ProfileServer_the_engine_gets_profiles_on_the_picked_server(app, client):
    store = app.extensions["pb"].store
    client.get("/api/servers")  # makes the Local row
    sid = client.post("/api/servers", json={"name": "llamacpp", "host": "root@mx-llamacpp-ssh", "port": 22, "gpu": "rocm"}).get_json()["server"]["id"]
    client.post("/api/profiles", json={"name": "p", "base_model": "a", "launch_command": "HIP_VISIBLE_DEVICES=0 llama-server --model /m/a.gguf --port 8080"})
    ours = lambda: [x for x in client.get("/api/cookbook/state").get_json()["presets"] if (x.get("fields") or {}).get("source") == "argos"]  # noqa: E731
    local = next(s for s in store.list_servers() if s["kind"] == "local")
    store.set_default_server(local["id"])
    assert ours()[0]["cmd"].split("llama-server", 1)[0].strip() == "HIP_VISIBLE_DEVICES=0", "Local: unchanged"

    assert client.post(f"/api/servers/{sid}/default").get_json()["ok"]
    cmd = ours()[0]["cmd"]
    assert f"/app/bin/remote-llama rocm_srv{sid} " in cmd and f"ROCM_SRV{sid}_SSH_HOST=mx-llamacpp-ssh" in cmd
    assert "HIP_VISIBLE_DEVICES=0" in cmd, "the GPU pin rides along"


def test_ProfileServer_the_gpu_pick_reads_the_servers_runtime(app, client):
    j = client.post("/api/command/build", json={"command": "/app/bin/remote-llama cuda_srv4 --model /m/a.gguf", "format": "plain",
                                                "gpus": ["1"]}).get_json()
    assert j["command"].startswith("CUDA_VISIBLE_DEVICES=1 ")
