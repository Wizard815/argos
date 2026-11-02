"""setup/setup.sh end to end: each mode and backend, then Argos started with the environment the generated
compose files give its container. Each mode has to show up in Servers as the llama.cpp it set up."""
import os
import re
import shutil
import subprocess

import pytest

from src.app import create_app

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SETUP = os.path.join(ROOT, "setup", "setup.sh")
OFFICIAL = {"rocm": "ghcr.io/ggml-org/llama.cpp:server-rocm", "cuda": "ghcr.io/ggml-org/llama.cpp:server-cuda"}

pytestmark = pytest.mark.skipif(os.name == "nt" or not shutil.which("bash"), reason="setup.sh needs bash")


def answers(mode: str, backend: str, key: str = "", images: dict = None, bins: dict = None, hosts: dict = None) -> list:
    """The replies to setup.sh's questions, in the order it asks them (blank = its default)."""
    images, bins, hosts = images or {}, bins or {}, hosts or {}
    out = [backend, mode]
    wanted = ["rocm", "cuda"] if backend == "both" else [backend]
    if backend != "cuda" and mode != "3":
        out.append("")  # video GID
    if mode in ("1", "2"):
        if mode == "1" and backend == "both":
            out += [images.get("rocm", ""), bins.get("rocm", ""), bins.get("cuda", "")]
        else:
            for b in wanted:
                out += [images.get(b, ""), bins.get(b, "")]
    else:
        out.append(key)
        if shutil.which("ssh-keygen"):
            out.append("y")  # no key there yet: create one
        out.append("root")
        for b in wanted:
            out += [hosts.get(b, ""), "2222" if b == "rocm" else "", "/app/llama-server"]
    out += ["n",                # custom build
            "pool", "32,32",    # scheduler, cards
            "./models", "", "",  # models, state, config fragments
            "",                 # extra volume
            "", "",             # networks, bind IP
            ""]                 # port
    return out


def run_setup(folder, replies: list) -> str:
    shutil.copy(os.path.join(ROOT, "docker-compose.yml"), folder)
    proc = subprocess.run(["bash", SETUP], cwd=folder, input="\n".join(replies) + "\n", capture_output=True, text=True,
                          timeout=60)
    assert proc.returncode == 0, proc.stdout[-2000:] + proc.stderr[-2000:]
    return proc.stdout


def dotenv(folder) -> dict:
    out = {}
    for line in open(os.path.join(folder, ".env"), encoding="utf-8"):
        if "=" in line and not line.startswith("#"):
            k, _, v = line.rstrip("\n").partition("=")
            out[k] = v
    return out


def container_env(folder) -> dict:
    """What the argos container's environment would be: the override's `- KEY=${VAR:-default}` lines,
    resolved from .env the way compose does, plus the key mount Mode 3 adds."""
    env, have = {}, dotenv(folder)
    override = open(os.path.join(folder, "docker-compose.override.yaml"), encoding="utf-8").read()
    for k, value in re.findall(r"^\s+- ([A-Z_][A-Z0-9_]*)=(.*)$", override, re.M):
        env[k] = re.sub(r"\$\{(\w+)(?::-([^}]*))?\}", lambda m: have.get(m.group(1)) or m.group(2) or "", value)
    if "${REMOTE_SSH_KEY_FILE}:/run/secrets/argos_ssh_key:ro" in override:
        env["REMOTE_SSH_KEY"] = have["REMOTE_SSH_KEY_FILE"]  # where the mount puts it in the container
    return env


def argos_servers(tmp_path, env) -> dict:
    app = create_app({"DB": str(tmp_path / "argos.db"), "RUNS_DIR": str(tmp_path / "runs"), "SSH_DIR": str(tmp_path / "ssh"),
                      "MODELS_DIR": str(tmp_path / "models"), "TELEMETRY": False, "BACKGROUND": False, "SETUP_ENV": env})
    rows = app.test_client().get("/api/servers").get_json()["servers"]
    return app, {s["name"]: s for s in rows}


@pytest.mark.parametrize("backend", ["rocm", "cuda", "both"])
def test_SetupScript_mode1_builds_on_the_official_image_and_adds_each_backend(tmp_path, backend):
    work = tmp_path / "repo"
    work.mkdir()
    bins = {"rocm": "/opt/rocm-build/llama-server", "cuda": "/opt/cuda-build/llama-server"} if backend == "both" else {}
    run_setup(work, answers("1", backend, bins=bins))
    env_file = dotenv(work)
    first = "cuda" if backend == "cuda" else "rocm"
    assert (env_file["BASE_IMAGE"], env_file["BASE_TAG"]) == ("ghcr.io/ggml-org/llama.cpp", f"server-{first}")
    config = open(work / "config.yaml", encoding="utf-8").read()

    _, servers = argos_servers(tmp_path, container_env(work))
    wanted = ["rocm", "cuda"] if backend == "both" else [backend]
    assert set(servers) == {"Local"} | {f"llama.cpp {'ROCm' if b == 'rocm' else 'CUDA'}" for b in wanted}
    for b in wanted:
        s = servers[f"llama.cpp {'ROCm' if b == 'rocm' else 'CUDA'}"]
        path = bins.get(b, "/app/llama-server")
        assert (s["kind"], s["gpu"], s["llama_server"], s["source"]) == ("local", b, path, "setup")
        assert f"bk-{b}: {path}" in config, "the engine's groups and the Servers entry run the same binary"
    assert servers[f"llama.cpp {'CUDA' if backend == 'cuda' else 'ROCm'}"]["is_default"], "Profiles launch there"


@pytest.mark.parametrize("backend", ["rocm", "cuda", "both"])
def test_SetupScript_mode2_runs_a_container_per_model_beside_argos(tmp_path, backend):
    work = tmp_path / "repo"
    work.mkdir()
    run_setup(work, answers("2", backend, images={"cuda": "registry.local:5000/me/llama:cuda"} if backend == "both" else {}))
    env = container_env(work)
    wanted = ["rocm", "cuda"] if backend == "both" else [backend]
    for b in wanted:
        assert env[f"{b.upper()}_IMAGE"] == ("registry.local:5000/me/llama:cuda" if backend == "both" and b == "cuda" else OFFICIAL[b])
        assert env[f"{b.upper()}_BIN"] == "/app/llama-server"
    assert env["HOST_MODELS_DIR"] == "./models" and dotenv(work)["BASE_IMAGE"] == "ubuntu"
    assert "/var/run/docker.sock:/var/run/docker.sock" in open(work / "docker-compose.override.yaml").read()

    app, servers = argos_servers(tmp_path, env)
    assert set(servers) == {"Local"} | {f"llama.cpp {'ROCm' if b == 'rocm' else 'CUDA'}" for b in wanted}
    for b in wanted:
        s = servers[f"llama.cpp {'ROCm' if b == 'rocm' else 'CUDA'}"]
        assert (s["kind"], s["gpu"], s["llama_server"]) == ("local", b, f"/app/bin/docker-llama {b}")
    assert app.extensions["pb"].servers.default_parts()["program"] == ["/app/bin/docker-llama", "cuda" if backend == "cuda" else "rocm"]


@pytest.mark.skipif(not shutil.which("ssh-keygen"), reason="Mode 3 makes its key with ssh-keygen")
@pytest.mark.parametrize("backend", ["rocm", "cuda", "both"])
def test_SetupScript_mode3_adds_the_containers_with_the_key_it_made(tmp_path, backend):
    work = tmp_path / "repo"
    work.mkdir()
    key = str(tmp_path / "keys" / "argos_ed25519")
    out = run_setup(work, answers("3", backend, key=key, hosts={"rocm": "llama-rocm", "cuda": "10.0.0.7"}))
    assert os.path.exists(key) and os.path.exists(key + ".pub")
    pub = open(key + ".pub").read().strip()
    assert pub in out, "the checklist prints the key to authorize"

    app, servers = argos_servers(tmp_path, container_env(work))
    wanted = ["rocm", "cuda"] if backend == "both" else [backend]
    assert set(servers) == {"Local"} | {f"llama.cpp {'ROCm' if b == 'rocm' else 'CUDA'}" for b in wanted}
    sv = app.extensions["pb"].servers
    for b in wanted:
        s = servers[f"llama.cpp {'ROCm' if b == 'rocm' else 'CUDA'}"]
        host = "root@llama-rocm" if b == "rocm" else "root@10.0.0.7"
        port = 2222 if b == "rocm" else 22
        assert (s["kind"], s["host"], s["port"], s["gpu"], s["llama_server"]) == ("ssh", host, port, b, "/app/llama-server")
        assert s["has_key"] and sv.public_key(s["id"]) == pub
    parts = sv.default_parts()
    assert parts["program"][0] == "/app/bin/remote-llama" and parts["env"]["REMOTE_SSH_KEY"].endswith(f"server-{parts['program'][1].split('srv')[1]}")


def test_SetupScript_mode3_waits_for_the_key_then_adds_the_servers(tmp_path):
    work = tmp_path / "repo"
    work.mkdir()
    key = str(tmp_path / "later" / "id")
    replies = answers("3", "rocm", key=key)
    if shutil.which("ssh-keygen"):
        replies[replies.index("y")] = "n"  # do not create it now
    run_setup(work, replies)
    env = container_env(work)
    _, servers = argos_servers(tmp_path, env)
    assert set(servers) == {"Local"}, "no key mounted: nothing to log in with yet"
    os.makedirs(os.path.dirname(key))
    open(key, "w").write("PRIVATE\n")
    open(key + ".pub", "w").write("ssh-ed25519 AAAALATER argos\n")
    _, servers = argos_servers(tmp_path, env)  # the next start, with the key in place
    assert servers["llama.cpp ROCm"]["has_key"] and servers["llama.cpp ROCm"]["host"] == "root@llama-rocm"


def test_SetupScript_a_rerun_with_fewer_backends_drops_the_old_server(tmp_path):
    work = tmp_path / "repo"
    work.mkdir()
    run_setup(work, answers("2", "both"))
    _, servers = argos_servers(tmp_path, container_env(work))
    assert {"llama.cpp ROCm", "llama.cpp CUDA"} <= set(servers)
    os.remove(work / ".env")
    run_setup(work, answers("2", "rocm"))
    _, servers = argos_servers(tmp_path, container_env(work))
    assert set(servers) == {"Local", "llama.cpp ROCm"}
