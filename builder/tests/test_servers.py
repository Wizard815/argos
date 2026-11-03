"""Servers: Local plus SSH machines, keys, Check, seeding from a file, importing from Odysseus."""
import json
import os
import subprocess

import pytest

from src import servers as servers_mod
from src.app import create_app
from src.servers import from_odysseus, parse_facts

FACTS_ROCM = """argos=ok
hostname=mx-llama
llama_server=/app/llama-server
kv_probe=1
kv_pair=1-1
kv_pair=8-8
vllm=
sglang=
docker=
rocm_smi=1
kfd=1
gpu_rocm=Radeon VII
gpu_rocm=Radeon VII
"""
FACTS_CUDA = """argos=ok
hostname=llama-cuda
llama_server=/usr/local/bin/llama-server
vllm_module=1
docker=/usr/bin/docker
gpu_cuda=NVIDIA GeForce RTX 3090
"""


class FakeSSH:
    """Stands in for subprocess: records every command and answers from a queue."""

    def __init__(self):
        self.calls = []
        self.replies = []

    def __call__(self, argv, stdin, timeout):
        self.calls.append((list(argv), stdin))
        if argv[0] == "ssh-keygen" and "-t" in argv:  # making a key; -y (derive a .pub) and -R answer from the queue
            path = argv[argv.index("-f") + 1]
            open(path, "w").write("PRIVATE")
            open(path + ".pub", "w").write("ssh-ed25519 AAAATEST argos-test\n")
            return subprocess.CompletedProcess(argv, 0, "", "")
        reply = self.replies.pop(0) if self.replies else (0, "", "")
        if isinstance(reply, Exception):
            raise reply
        code, out, errtext = reply
        return subprocess.CompletedProcess(argv, code, out, errtext)


@pytest.fixture
def fake():
    return FakeSSH()


@pytest.fixture
def sapp(tmp_path, fake, monkeypatch):
    monkeypatch.setattr(servers_mod.shutil, "which", lambda name: "/usr/bin/" + name)
    return create_app({
        "DB": str(tmp_path / "t.db"), "RUNS_DIR": str(tmp_path / "runs"), "SSH_DIR": str(tmp_path / "ssh"),
        "MODELS_DIR": str(tmp_path / "models"), "TELEMETRY": False, "BACKGROUND": False, "SSH_RUN": fake,
    })


@pytest.fixture
def sclient(sapp):
    return sapp.test_client()


def add(client, **kw):
    body = {"name": "llamacpp", "host": "root@mx-llama", "port": 22, **kw}
    r = client.post("/api/servers", json=body)
    assert r.status_code == 200, r.get_json()
    return r.get_json()["server"]


# ----- list, add, edit, delete ----------------------------------------------------------------
def test_local_is_always_there_and_default(sclient):
    rows = sclient.get("/api/servers").get_json()["servers"]
    assert [(s["name"], s["kind"], s["is_default"]) for s in rows] == [("Local", "local", True)]


def test_add_validates_host_and_name(sclient):
    bad = [{"name": "x", "host": "-oProxyCommand=evil"}, {"name": "x", "host": ""}, {"name": "", "host": "a@b"},
           {"name": "x", "host": "a b@c"}, {"name": "x", "host": "a@b", "port": 70000}, {"name": "x", "host": "a@b", "gpu": "tpu"}]
    for body in bad:
        assert sclient.post("/api/servers", json=body).status_code == 400, body
    s = add(sclient)
    assert s["status"] == "unknown" and s["has_key"] is False and s["source"] == "ui"
    assert sclient.post("/api/servers", json={"name": "llamacpp", "host": "a@b"}).status_code == 400, "names are unique"


def test_edit_default_and_delete(sclient):
    a = add(sclient)
    r = sclient.put(f"/api/servers/{a['id']}", json={"port": 2222, "env_prefix": ". ~/venv/bin/activate",
                                                     "model_dirs": ["/app/models", {"path": "/data/m", "download": True}]})
    s = r.get_json()["server"]
    assert s["port"] == 2222 and s["env_prefix"].startswith(". ~") and s["model_dirs"][1] == {"path": "/data/m", "download": True}
    assert sclient.post(f"/api/servers/{a['id']}/default").status_code == 200
    rows = {x["name"]: x for x in sclient.get("/api/servers").get_json()["servers"]}
    assert rows["llamacpp"]["is_default"] and not rows["Local"]["is_default"]
    assert sclient.delete(f"/api/servers/{a['id']}").status_code == 200
    rows = sclient.get("/api/servers").get_json()["servers"]
    assert [x["name"] for x in rows] == ["Local"] and rows[0]["is_default"], "the default falls back to Local"


def test_local_cannot_be_removed_or_made_remote(sclient):
    local = sclient.get("/api/servers").get_json()["servers"][0]
    assert sclient.delete(f"/api/servers/{local['id']}").status_code == 400
    s = sclient.put(f"/api/servers/{local['id']}", json={"host": "root@x", "llama_server": "/app/bin/llama-server"}).get_json()["server"]
    assert s["host"] == "" and s["kind"] == "local" and s["llama_server"] == "/app/bin/llama-server"


# ----- keys -------------------------------------------------------------------------------------
def test_key_is_made_once_and_the_install_command_quotes_it(sclient, fake):
    a = add(sclient)
    assert sclient.post(f"/api/servers/{a['id']}/key", json={}).get_json()["public_key"].startswith("ssh-ed25519 AAAATEST")
    j = sclient.post(f"/api/servers/{a['id']}/key", json={}).get_json()
    assert sum(1 for argv, _ in fake.calls if argv[0] == "ssh-keygen") == 1, "an existing key is kept"
    assert "authorized_keys" in j["command"] and "-- root@mx-llama" in j["command"] and "AAAATEST" in j["command"]
    assert sclient.post(f"/api/servers/{a['id']}/key", json={"force": True}).status_code == 200
    assert sum(1 for argv, _ in fake.calls if argv[0] == "ssh-keygen") == 2
    assert sclient.get("/api/servers").get_json()["servers"][1]["has_key"] is True
    local = sclient.get("/api/servers").get_json()["servers"][0]
    assert sclient.post(f"/api/servers/{local['id']}/key", json={}).status_code == 400


# ----- check ------------------------------------------------------------------------------------
def test_check_needs_a_key_first(sclient, fake):
    a = add(sclient)
    s = sclient.post(f"/api/servers/{a['id']}/check").get_json()["server"]
    assert s["status"] == "no-key" and "Key" in s["last_error"]
    assert not any(argv[0] == "ssh" for argv, _ in fake.calls)


def test_check_finds_what_is_installed(sclient, fake):
    a = add(sclient, llama_server="/app/llama-server")
    sclient.post(f"/api/servers/{a['id']}/key", json={})
    fake.replies = [(0, FACTS_ROCM, "")]
    s = sclient.post(f"/api/servers/{a['id']}/check").get_json()["server"]
    assert s["status"] == "connected" and s["latency_ms"] is not None
    e = {k: v for k, v in s["engines"].items() if k != "models"}
    assert e == {"hostname": "mx-llama", "llama_server": "/app/llama-server", "vllm": "", "sglang": "",
                 "docker": "", "gpu": "rocm", "gpus": ["Radeon VII", "Radeon VII"], "kv_pairs": ["f16-f16", "q8_0-q8_0"]}
    argv, script = fake.calls[-1]
    assert argv[0] == "ssh" and "BatchMode=yes" in argv and argv[argv.index("--") + 1] == "root@mx-llama"
    assert "/app/llama-server" in script and "echo \"argos=ok\"" in script
    fake.replies = [(0, FACTS_CUDA, "")]
    e = sclient.post(f"/api/servers/{a['id']}/check").get_json()["server"]["engines"]
    assert e["gpu"] == "cuda" and e["gpus"] == ["NVIDIA GeForce RTX 3090"] and e["vllm"] == "python module" and e["docker"] == "/usr/bin/docker"


def test_check_reports_failures_plainly(sclient, fake):
    a = add(sclient)
    sclient.post(f"/api/servers/{a['id']}/key", json={})
    fake.replies = [(255, "", "ssh: connect to host mx-llama port 22: Connection refused\n")]
    s = sclient.post(f"/api/servers/{a['id']}/check").get_json()["server"]
    assert s["status"] == "failed" and "Connection refused" in s["last_error"] and s["engines"] == {}
    fake.replies = [subprocess.TimeoutExpired("ssh", 30)]
    assert "no answer" in sclient.post(f"/api/servers/{a['id']}/check").get_json()["server"]["last_error"]
    fake.replies = [FileNotFoundError("ssh")]
    assert "could not run ssh" in sclient.post(f"/api/servers/{a['id']}/check").get_json()["server"]["last_error"]
    events = [e for e in sclient.get("/api/events").get_json()["events"] if e["kind"] == "server"] if sclient.get("/api/events").status_code == 200 else []
    assert events == [] or all(e["level"] == "warn" for e in events if "added" not in e["message"])


def test_check_local_runs_without_ssh(sclient, fake):
    local = sclient.get("/api/servers").get_json()["servers"][0]
    fake.replies = [(0, FACTS_CUDA, "")]
    s = sclient.post(f"/api/servers/{local['id']}/check").get_json()["server"]
    assert s["status"] == "connected" and fake.calls[-1][0][0] == "sh"


def test_parse_facts_nothing_installed():
    assert parse_facts("argos=ok\nhostname=h\nllama_server=\n")["gpu"] == "none"


# ----- seeding from a file ----------------------------------------------------------------------
def test_seed_file_creates_updates_and_never_overwrites_ui_servers(tmp_path, fake, monkeypatch):
    monkeypatch.setattr(servers_mod.shutil, "which", lambda name: "/usr/bin/" + name)
    seed = tmp_path / "servers.yaml"
    seed.write_text("servers:\n  - name: mx\n    host: root@mx-llama\n    llama_server: /app/llama-server\n    default: true\n"
                    "  - name: cuda\n    host: root@llama-cuda\n    port: 2222\n    gpu: cuda\n    model_dirs: [/app/models]\n"
                    "  - name: broken\n    host: '-bad'\n")
    cfg = {"DB": str(tmp_path / "t.db"), "RUNS_DIR": str(tmp_path / "r"), "SSH_DIR": str(tmp_path / "ssh"), "TELEMETRY": False,
           "BACKGROUND": False, "SSH_RUN": fake, "SERVERS_FILE": str(seed)}
    c = create_app(cfg).test_client()
    rows = {s["name"]: s for s in c.get("/api/servers").get_json()["servers"]}
    assert set(rows) == {"Local", "mx", "cuda"}, "the broken entry is reported, not fatal"
    assert rows["mx"]["is_default"] and rows["cuda"]["port"] == 2222 and rows["mx"]["source"] == "file"
    # an edit in the app takes the server over from the file
    c.put(f"/api/servers/{rows['cuda']['id']}", json={"port": 2200})
    seed.write_text("servers:\n  - {name: mx, host: root@new-host}\n  - {name: cuda, host: root@other, port: 1}\n")
    c2 = create_app(cfg).test_client()
    rows = {s["name"]: s for s in c2.get("/api/servers").get_json()["servers"]}
    assert rows["mx"]["host"] == "root@new-host", "file-owned servers follow the file"
    assert rows["cuda"]["port"] == 2200 and rows["cuda"]["host"] == "root@llama-cuda", "UI-owned servers stay as edited"


def test_seed_from_environment_json(tmp_path, fake):
    c = create_app({"DB": str(tmp_path / "t.db"), "RUNS_DIR": str(tmp_path / "r"), "SSH_DIR": str(tmp_path / "ssh"),
                    "TELEMETRY": False, "BACKGROUND": False, "SSH_RUN": fake,
                    "SERVERS_JSON": json.dumps([{"name": "e", "host": "u@h"}])}).test_client()
    assert "e" in [s["name"] for s in c.get("/api/servers").get_json()["servers"]]


# ----- Odysseus ---------------------------------------------------------------------------------
def test_from_odysseus_maps_loosely_and_skips_local():
    items, notes = from_odysseus([
        {"name": "Local", "kind": "local"},
        {"name": "llamacpp", "host": "mx-llamacpp-ssh", "user": "root", "port": 22, "model_dirs": ["/app/models"]},
        {"label": "gpu2", "hostname": "me@gpu2", "venv": "~/venv"},
        {"name": "url-only", "url": "http://x:1"},
    ])
    assert [i["name"] for i in items] == ["llamacpp", "gpu2"]
    assert items[0]["host"] == "root@mx-llamacpp-ssh" and items[1]["host"] == "me@gpu2" and (items[1]["env_kind"], items[1]["env_value"]) == ("venv", "~/venv")
    assert len(notes) == 2


def test_import_from_odysseus_keeps_our_own_servers(sclient, monkeypatch):
    from src.routes import servers as routes

    class Fake:
        def cookbook(self):
            return {"presets": [], "servers": {"servers": [{"name": "llamacpp", "host": "root@mx-llamacpp-ssh"}, {"name": "Local"}]}}

    monkeypatch.setattr(routes, "client", lambda: Fake())
    mine = add(sclient, name="mine", host="root@mine")
    j = sclient.post("/api/servers/import/odysseus").get_json()
    assert j["created"] == ["llamacpp"] and j["skipped"] == []
    again = sclient.post("/api/servers/import/odysseus").get_json()
    assert again["created"] == [] and again["updated"] == ["llamacpp"]
    assert {s["name"] for s in j["servers"]} == {"Local", "mine", "llamacpp"} and mine["name"] == "mine"


def test_the_real_runner_sends_the_script_byte_for_byte(tmp_path):
    """Text mode on Windows turned the script's newlines into CRLF, which broke the remote shell."""
    import sys
    proc = servers_mod._run([sys.executable, "-c", "import sys; sys.stdout.buffer.write(sys.stdin.buffer.read())"], "a\nb\n", 10)
    assert proc.returncode == 0 and proc.stdout == "a\nb\n"


# ----- env, colour, and "does it see my models" ----------------------------------------------------
def test_env_kinds_become_a_prefix(sclient):
    a = add(sclient, env_kind="venv", env_value="~/venv")
    assert (a["env_kind"], a["env_value"], a["env_prefix"]) == ("venv", "~/venv", ". ~/venv/bin/activate")
    b = add(sclient, name="b", host="u@b", env_kind="conda", env_value="llm")
    assert b["env_prefix"] == 'eval "$(conda shell.bash hook 2>/dev/null)"; conda activate llm'
    c = add(sclient, name="c", host="u@c", env_prefix=". /opt/x/activate")  # a raw prefix from servers.yaml
    assert (c["env_kind"], c["env_value"], c["env_prefix"]) == ("custom", ". /opt/x/activate", ". /opt/x/activate")
    cleared = sclient.put(f"/api/servers/{a['id']}", json={"env_kind": "none"}).get_json()["server"]
    assert cleared["env_prefix"] == "" and cleared["env_value"] == ""
    assert sclient.post("/api/servers", json={"name": "d", "host": "u@d", "env_kind": "zsh"}).status_code == 400
    assert sclient.post("/api/servers", json={"name": "e", "host": "u@e", "color": "red; x"}).status_code == 400
    f = add(sclient, name="f", host="u@f", color="#50fa7b", download_dir="/app/models")
    assert f["color"] == "#50fa7b" and f["download_dir"] == "/app/models"


def test_check_says_whether_the_server_sees_the_same_models(tmp_path, fake, monkeypatch):
    monkeypatch.setattr(servers_mod.shutil, "which", lambda name: "/usr/bin/" + name)
    models = tmp_path / "models"
    (models / "sub").mkdir(parents=True)
    (models / "a-q4.gguf").write_bytes(b"x" * 10)
    (models / "sub" / "b-q8.gguf").write_bytes(b"y" * 20)
    c = create_app({"DB": str(tmp_path / "t.db"), "RUNS_DIR": str(tmp_path / "r"), "SSH_DIR": str(tmp_path / "ssh"),
                    "MODELS_DIR": str(models), "TELEMETRY": False, "BACKGROUND": False, "SSH_RUN": fake}).test_client()
    s = add(c, model_dirs=["/app/models", "~/other"])
    c.post(f"/api/servers/{s['id']}/key", json={})
    both = FACTS_ROCM + "gguf=10|/app/models/a-q4.gguf\ngguf=20|/app/models/deep/b-q8.gguf\ngguf=5|/app/models/only-there.gguf\n"
    fake.replies = [(0, both, "")]
    m = c.post(f"/api/servers/{s['id']}/check").get_json()["server"]["engines"]["models"]
    assert m["same"] is True and m["found"] == 3 and m["argos"] == 2 and m["only_here"] == ["only-there.gguf"]
    script = fake.calls[-1][1]
    assert "/app/models" in script and '"$HOME"/other' in script, "a leading ~ expands on the server"
    fake.replies = [(0, FACTS_ROCM + "gguf=10|/app/models/a-q4.gguf\ngguf=99|/x/b-q8.gguf\ndir_missing=/nope\n", "")]
    m = c.post(f"/api/servers/{s['id']}/check").get_json()["server"]["engines"]["models"]
    assert m["same"] is False and m["missing_here"] == ["b-q8.gguf"] and m["differ_in_size"] == ["b-q8.gguf"]
    assert m["dirs_missing"] == ["/nope"]


def test_odysseus_import_keeps_env_colour_and_skips_urls():
    items, notes = from_odysseus([
        {"name": "llamacpp", "host": "root@mx", "env": "conda", "envPath": "llm", "downloadDir": "/app/models", "color": "#ff79c6",
         "modelDirs": ["/app/models"]},
        {"name": "engine", "host": "http://192.168.20.5:8642/v1"},
    ])
    assert len(items) == 1 and items[0]["env_kind"] == "conda" and items[0]["env_value"] == "llm"
    assert items[0]["color"] == "#ff79c6" and items[0]["download_dir"] == "/app/models" and any("URL" in n for n in notes)


def test_check_sees_models_inside_a_huggingface_cache(tmp_path):
    """An HF-cache model is a symlink into blobs/, and the probe must follow it.

    Regression: the probe used `find -type f`, which skips symlinks, so every
    HF-cache model was reported missing while Argos's own index (os.walk with
    followlinks) listed it -- a Check said "missing N of the M Argos sees" about
    models sitting in the very folder it had just scanned. Real layout: the
    weights are in blobs/ and snapshots/<sha>/<file> is a relative symlink into
    them.
    """
    repo = tmp_path / "HFCache" / "hub" / "models--org--repo"
    (repo / "blobs").mkdir(parents=True)
    (repo / "snapshots" / "sha1").mkdir(parents=True)
    (repo / "blobs" / "deadbeef").write_bytes(b"x" * 4000)
    (repo / "snapshots" / "sha1" / "model.gguf").symlink_to("../../blobs/deadbeef")
    (tmp_path / "loose.gguf").write_bytes(b"y" * 4000)

    script = servers_mod.detection_script({"model_dirs": [{"path": str(tmp_path)}]})
    out = subprocess.run(["sh", "-c", script], capture_output=True, text=True, timeout=30)

    remote = parse_facts(out.stdout)["model_files"]
    assert sorted(f["name"] for f in remote) == ["loose.gguf", "model.gguf"], \
        "the HF-cache symlink must be found by the Check probe"

    local = servers_mod.gguf_index([str(tmp_path)])
    cmp = servers_mod.compare_models(remote, local)
    assert cmp["common"] == 2 and cmp["same"] is True, cmp


# ----- what setup.sh set up --------------------------------------------------------------------
def _setup_app(tmp_path, fake, env):
    return create_app({
        "DB": str(tmp_path / "t.db"), "RUNS_DIR": str(tmp_path / "runs"), "SSH_DIR": str(tmp_path / "ssh"),
        "MODELS_DIR": str(tmp_path / "models"), "TELEMETRY": False, "BACKGROUND": False, "SSH_RUN": fake,
        "SETUP_ENV": env,
    })


def test_Servers_setup_mode1_and_mode2_add_a_server_per_backend(tmp_path, fake):
    plan = servers_mod.setup_plan
    one = plan({"ARGOS_SETUP_MODE": "1", "ARGOS_SETUP_BACKENDS": "cuda", "CUDA_IMAGE": "ghcr.io/ggml-org/llama.cpp:server-cuda",
                "CUDA_BIN": "/app/llama-server"})["servers"]
    assert [(s["name"], s["kind"], s["llama_server"], s["gpu"]) for s in one] == [("llama.cpp CUDA", "local", "/app/llama-server", "cuda")]
    two = plan({"ARGOS_SETUP_MODE": "2", "ARGOS_SETUP_BACKENDS": "both", "ROCM_IMAGE": "x", "CUDA_IMAGE": "y"})["servers"]
    assert [s["llama_server"] for s in two] == ["/app/bin/docker-llama rocm", "/app/bin/docker-llama cuda"]
    assert plan({}) == {"mode": "", "needs_key": False, "servers": []}

    env = {"ARGOS_SETUP_MODE": "2", "ARGOS_SETUP_BACKENDS": "rocm", "ROCM_IMAGE": "x"}
    app = _setup_app(tmp_path, fake, env)
    rows = {s["name"]: s for s in app.extensions["pb"].store.list_servers()}
    rocm = rows["llama.cpp ROCm"]
    assert (rocm["llama_server"], rocm["gpu"], rocm["source"], rocm["is_default"]) == ("/app/bin/docker-llama rocm", "rocm", "setup", True)
    assert rows["Local"]["llama_server"] == "" and rows["Local"]["source"] == "builtin", "Local stays llama-server on PATH"
    # an edit in the app wins over setup from then on, and a setup server can be removed
    c = app.test_client()
    c.put(f"/api/servers/{rocm['id']}", json={"llama_server": "/opt/mine/llama-server"})
    again = _setup_app(tmp_path, fake, env)
    assert again.extensions["pb"].store.server_by_name("llama.cpp ROCm")["llama_server"] == "/opt/mine/llama-server"
    assert again.test_client().delete(f"/api/servers/{rocm['id']}").status_code == 200
    assert again.test_client().delete(f"/api/servers/{rows['Local']['id']}").status_code == 400


def test_Servers_setup_mode3_adds_containers_once_the_key_is_mounted(tmp_path, fake):
    env = {"ARGOS_SETUP_MODE": "3", "ARGOS_SETUP_BACKENDS": "both", "ROCM_SSH_HOST": "llama-rocm", "ROCM_SSH_PORT": "2222",
           "ROCM_SSH_USER": "root", "ROCM_SSH_BIN": "/app/llama-server", "CUDA_SSH_HOST": "llama-cuda",
           "REMOTE_SSH_KEY": str(tmp_path / "secret")}
    app = _setup_app(tmp_path, fake, env)
    assert [s["name"] for s in app.extensions["pb"].store.list_servers()] == ["Local"], "no key yet: nothing added"

    (tmp_path / "secret").write_text("PRIVATE KEY\n")
    fake.replies = [(0, "ssh-ed25519 AAAADERIVED argos\n", "")]  # ssh-keygen -y, no .pub beside the key
    app = _setup_app(tmp_path, fake, env)
    pb = app.extensions["pb"]
    rows = {s["name"]: s for s in pb.store.list_servers()}
    rocm = rows["llama.cpp ROCm"]
    assert (rocm["host"], rocm["port"], rocm["gpu"], rocm["source"]) == ("root@llama-rocm", 2222, "rocm", "setup")
    assert rows["llama.cpp CUDA"]["host"] == "root@llama-cuda" and rocm["model_dirs"] == [{"path": "/app/models", "download": False}]
    assert rocm["is_default"], "Profiles launch on the first container, not on a Local with no llama.cpp"
    assert pb.servers.has_key(rocm["id"]) and pb.servers.public_key(rocm["id"]) == "ssh-ed25519 AAAADERIVED argos"
    assert open(pb.servers.key_path(rocm["id"])).read() == "PRIVATE KEY\n"
    assert pb.servers.default_parts()["env"]["ROCM_SRV%d_SSH_PORT" % rocm["id"]] == "2222"


def test_check_lists_the_kv_cache_pairs_the_build_compiled(tmp_path):
    """The probe reads the FlashAttention decode kernels out of the backend library.

    llama.cpp compiles one ggml_cuda_flash_attn_ext_vec_case<D, K, V> per K/V pair,
    and a pair it lacks still runs, converting the cache to f16 every step. The
    mangled names carry the two ggml_type numbers.
    """
    app = tmp_path / "app"
    app.mkdir()
    server = app / "llama-server"
    server.write_text("#!/bin/sh\n")
    server.chmod(0o755)
    names = [b"_Z33ggml_cuda_flash_attn_ext_vec_caseILi128EL9ggml_type8ELS0_8EEvR25ggml_backend_cuda_contextP11ggml_tensor",
             b"_Z33ggml_cuda_flash_attn_ext_vec_caseILi256EL9ggml_type8ELS0_8EEvR25ggml_backend_cuda_contextP11ggml_tensor",
             b"_Z33ggml_cuda_flash_attn_ext_vec_caseILi128EL9ggml_type43ELS0_43EEvR25ggml_backend_cuda_contextP11ggml_tensor",
             b"_Z33ggml_cuda_flash_attn_ext_vec_caseILi128EL9ggml_type8ELS0_6EEvR25ggml_backend_cuda_contextP11ggml_tensor"]
    (app / "libggml-hip.so").write_bytes(b"\x7fELF\0" + b"\0".join(names) + b"\0")

    script = servers_mod.detection_script({"llama_server": str(server)})
    out = subprocess.run(["sh", "-c", script], capture_output=True, text=True, timeout=30)
    assert parse_facts(out.stdout)["kv_pairs"] == ["q8_0-q5_0", "q8_0-q8_0", "turbo3-turbo3"]


def test_parse_facts_kv_pairs_unknown_build():
    assert parse_facts("argos=ok\nllama_server=/app/llama-server\nkv_probe=1\n")["kv_pairs"] == []
    assert parse_facts("argos=ok\nkv_pair=8-8\nkv_pair=99-8\nkv_pair=x\n")["kv_pairs"] == ["q8_0-q8_0"]


def test_Servers_a_changed_host_key_says_so_and_can_be_forgotten(sclient, sapp, fake, tmp_path, monkeypatch):
    s = add(sclient, port=2222)
    sclient.post(f"/api/servers/{s['id']}/key")
    fake.replies = [(255, "", "@@@ WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED! @@@\nHost key verification failed.\n")]
    r = sclient.post(f"/api/servers/{s['id']}/check").get_json()["server"]
    assert r["status"] == "failed" and "Forget host key" in r["last_error"]

    servers = sapp.extensions["pb"].servers
    os.makedirs(servers.ssh_dir, exist_ok=True)
    open(servers.known_hosts(), "w").write("[mx-llama]:2222 ssh-ed25519 OLD\n")
    launch = tmp_path / "ssh_known_hosts"
    launch.write_text("[mx-llama]:2222 ssh-ed25519 OLD\n")
    monkeypatch.setattr(servers, "LAUNCH_KNOWN_HOSTS", str(launch))
    fake.replies = [(0, "# Host [mx-llama]:2222 found: line 1\n", f"{servers.known_hosts()} updated.\n"), (0, "", ""),
                    (0, "", f"{launch} updated.\n"), (0, "", ""),
                    (0, FACTS_ROCM, "")]  # then Check connects
    out = sclient.post(f"/api/servers/{s['id']}/forget-host-key").get_json()
    assert out["removed"] == [servers.known_hosts(), str(launch)], "Check's file and the launches' file"
    removals = [c[0] for c in fake.calls if c[0][:2] == ["ssh-keygen", "-R"]]
    assert removals[0][2] == "[mx-llama]:2222", "a port other than 22 is saved as [host]:port"
    assert out["server"]["status"] == "connected"
