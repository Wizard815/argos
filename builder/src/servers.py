"""Servers: the machines models run on. Local is this container; the rest are reached over SSH.

A server records how to reach a machine (``user@host``, port, an optional env prefix such as
``. ~/venv/bin/activate``), where its models live, and what Check found on it: the llama-server
path, whether vLLM / SGLang / Docker are installed, and which GPU stack it has. Each SSH server
gets its own Argos key pair, installed on the machine once with the command the UI shows.

Check only reads: it runs a short detection script and changes nothing on the server.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import time
from typing import Any, Callable, Optional

import yaml

from .db import Store

LOCAL = "local"
GPU_KINDS = ("auto", "rocm", "cuda", "none")
ENV_KINDS = ("none", "venv", "conda", "custom")
_COLOR = re.compile(r"^(#[0-9a-fA-F]{3,8}|[a-z]{3,20})?$")
_HOST = re.compile(r"^[A-Za-z0-9._@:\[\]%-]{1,255}$")
_NAME = re.compile(r"^[A-Za-z0-9 ._-]{1,60}$")
# where llama-server usually lives when it is not on PATH
LLAMA_SERVER_PATHS = ("/app/bin/llama-server", "/app/llama-server", "/usr/local/bin/llama-server",
                      "/opt/llama.cpp/build/bin/llama-server")

RunFn = Callable[[list, str, float], "subprocess.CompletedProcess"]


class ServerError(Exception):
    pass


# ----- validation ------------------------------------------------------------------------------
def _model_dirs(value: Any) -> list[dict]:
    out = []
    for item in value or []:
        path = (item.get("path") if isinstance(item, dict) else item) or ""
        path = str(path).strip()
        if path and not any(d["path"] == path for d in out):
            out.append({"path": path, "download": bool(item.get("download")) if isinstance(item, dict) else False})
    return out


def launcher_for(server: dict) -> str:
    """The command prefix that starts a model on this server, or "" for this machine.

    The image ships the wrapper that SSHes to the host and forwards the model's port,
    so a server record only has to name its backend word. The local machine needs no
    prefix: its llama-server is a program on PATH.
    """
    if not server or server.get("kind") == LOCAL or not server.get("host"):
        return ""
    gpu = str(server.get("gpu") or "auto").strip().lower()
    if gpu not in ("rocm", "cuda", "vulkan"):
        return ""
    return f"/app/bin/remote-llama {gpu}"


def launch_parts(server: Optional[dict], key: str = "") -> Optional[dict]:
    """How a command runs on this server: ``{"program": [...], "env": {...}, "name": ...}``, or None for
    this machine.

    remote-llama finds its host from ``<NAME>_SSH_HOST`` and friends for whatever backend NAME it is
    given, so each server gets a name of its own (``rocm_srv3``) and its host, port, user, llama-server
    path and key ride along as env. A launch then reaches exactly this server, not just "the ROCm host",
    and the name still says which GPU runtime it has (``rocm_`` / ``cuda_``) for the GPU pick.
    """
    if not server or server.get("kind") == LOCAL or not server.get("host"):
        return None
    user, _, host = str(server["host"]).rpartition("@")
    engines = server.get("engines") if isinstance(server.get("engines"), dict) else {}
    gpu = str(server.get("gpu") or "auto").lower()
    if gpu not in ("rocm", "cuda"):
        gpu = str(engines.get("gpu") or "").lower()
    word = f"{gpu}_srv{server['id']}" if gpu in ("rocm", "cuda") else f"srv{server['id']}"
    name = word.upper()
    env = {f"{name}_SSH_HOST": host, f"{name}_SSH_PORT": str(server.get("port") or 22)}
    if user:
        env[f"{name}_SSH_USER"] = user
    binary = server.get("llama_server") or engines.get("llama_server") or ""
    if binary:
        env[f"{name}_SSH_BIN"] = binary
    if key:
        env["REMOTE_SSH_KEY"] = key
    return {"program": ["/app/bin/remote-llama", word], "env": env, "name": server.get("name") or host}


def on_server(cmd, parts: Optional[dict]):
    """Point a plain ``llama-server`` command at a server (in place). A command that names its own
    program, or no server (this machine), is left as it is. Returns ``cmd``."""
    if not parts or cmd.program != "llama-server":
        return cmd
    from .command import Arg
    cmd.program = parts["program"][0]
    cmd.args = [Arg("", w) for w in parts["program"][1:]] + cmd.args
    for k, v in parts["env"].items():
        cmd.env.setdefault(k, v)
    return cmd


def env_prefix_for(kind: str, value: str) -> str:
    """The shell snippet that activates a venv / conda env before anything runs on a server."""
    value = (value or "").strip()
    if kind == "venv" and value:
        path = value if value.endswith("/activate") else value.rstrip("/") + "/bin/activate"
        return f". {path}" if path.startswith("~") else f". {shlex.quote(path)}"
    if kind == "conda" and value:
        return f'eval "$(conda shell.bash hook 2>/dev/null)"; conda activate {shlex.quote(value)}'
    if kind == "custom":
        return value
    return ""


def clean_server(d: dict, partial: bool = False) -> dict:
    """Validate and normalise user input. ``partial`` skips the required fields (for updates)."""
    out: dict[str, Any] = {}
    if "name" in d or not partial:
        name = str(d.get("name") or "").strip()
        if not _NAME.match(name):
            raise ServerError("give the server a name (letters, digits, space, . _ -)")
        out["name"] = name
    kind = str(d.get("kind") or ("" if partial else "ssh")).strip()
    if kind:
        if kind not in (LOCAL, "ssh"):
            raise ServerError("kind must be local or ssh")
        out["kind"] = kind
    if "host" in d or (not partial and out.get("kind", "ssh") == "ssh"):
        host = str(d.get("host") or "").strip()
        if (host or out.get("kind", "ssh") == "ssh") and (not _HOST.match(host) or host.startswith("-")):
            raise ServerError("enter the server as user@host (letters, digits and . - _ only)")
        out["host"] = host
    if "port" in d or not partial:
        try:
            port = int(d.get("port") or 22)
        except (TypeError, ValueError):
            raise ServerError("port must be a number") from None
        if not 1 <= port <= 65535:
            raise ServerError("port must be between 1 and 65535")
        out["port"] = port
    if any(k in d for k in ("env_kind", "env_value", "env_prefix")) or not partial:
        kind, value = str(d.get("env_kind") or "").strip(), str(d.get("env_value") or "").strip()[:500]
        if not kind and d.get("env_prefix"):  # a raw prefix (servers.yaml) is kept as a custom one
            kind, value = "custom", str(d["env_prefix"]).strip()[:500]
        kind = kind or "none"
        if kind not in ENV_KINDS:
            raise ServerError(f"env must be one of {', '.join(ENV_KINDS)}")
        out.update(env_kind=kind, env_value=value if kind != "none" else "", env_prefix=env_prefix_for(kind, value))
    if "llama_server" in d or not partial:
        out["llama_server"] = str(d.get("llama_server") or "").strip()[:500]
    if "color" in d or not partial:
        color = str(d.get("color") or "").strip()
        if not _COLOR.match(color):
            raise ServerError("color must be a name or a #hex value")
        out["color"] = color
    if "download_dir" in d or not partial:
        out["download_dir"] = str(d.get("download_dir") or "").strip()[:500]
    if "gpu" in d or not partial:
        gpu = str(d.get("gpu") or "auto").strip()
        if gpu not in GPU_KINDS:
            raise ServerError(f"gpu must be one of {', '.join(GPU_KINDS)}")
        out["gpu"] = gpu
    if "model_dirs" in d or not partial:
        out["model_dirs"] = _model_dirs(d.get("model_dirs"))
    return out


# ----- the SSH side -----------------------------------------------------------------------------
def _run(argv: list, stdin: str, timeout: float) -> "subprocess.CompletedProcess":
    # bytes in and out: text mode would turn the script's "\n" into "\r\n" on Windows and break the remote shell
    proc = subprocess.run(argv, input=stdin.encode(), capture_output=True, timeout=timeout)  # noqa: S603
    return subprocess.CompletedProcess(proc.args, proc.returncode, proc.stdout.decode("utf-8", "replace"),
                                       proc.stderr.decode("utf-8", "replace"))


def detection_script(server: dict) -> str:
    """POSIX-sh lines printing ``key=value`` facts about the machine. Reads only."""
    want = server.get("llama_server") or ""
    candidates = " ".join(shlex.quote(p) for p in ([want] if want else []) + list(LLAMA_SERVER_PATHS))
    dirs = " ".join(_remote_path(d["path"]) for d in server.get("model_dirs") or [])
    return f"""{server.get('env_prefix') or ':'}
echo "argos=ok"
echo "hostname=$(hostname 2>/dev/null)"
ls_found=""
for p in {candidates}; do
  if [ -x "$p" ]; then ls_found="$p"; break; fi
done
[ -n "$ls_found" ] || ls_found="$(command -v llama-server 2>/dev/null)"
echo "llama_server=$ls_found"
echo "vllm=$(command -v vllm 2>/dev/null)"
echo "sglang=$(command -v sglang 2>/dev/null)"
echo "docker=$(command -v docker 2>/dev/null)"
command -v python3 >/dev/null 2>&1 && python3 - <<'PY' 2>/dev/null
import importlib.util as u
for m in ("vllm", "sglang"):
    if u.find_spec(m):
        print(m + "_module=1")
PY
command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | sed 's/^/gpu_cuda=/'
command -v rocm-smi >/dev/null 2>&1 && echo "rocm_smi=1"
[ -e /dev/kfd ] && echo "kfd=1"
for d in {dirs}; do
  [ -d "$d" ] || {{ echo "dir_missing=$d"; continue; }}
  # -L makes find follow symlinks, which is the whole difference for a
  # HuggingFace cache: the weights live in blobs/ and snapshots/<sha>/<file> is
  # a symlink into it, so a plain `find -type f` reported none of them and a
  # Check called every HF-cache model missing. Argos's own index follows them
  # (os.walk(followlinks=True)), so the two sides have to agree.
  find -L "$d" -type f -name '*.gguf' 2>/dev/null | head -3000 | while IFS= read -r f; do
    echo "gguf=$(wc -c < "$f" 2>/dev/null | tr -d ' ')|$f"
  done
done
if command -v rocm-smi >/dev/null 2>&1; then
  rocm-smi --showproductname 2>/dev/null | grep -i 'card series' | sed 's/.*: *//; s/^/gpu_rocm=/'
fi
exit 0
"""


def _remote_path(path: str) -> str:
    """Quote a folder for the remote shell but let a leading ~ expand there."""
    if path == "~":
        return '"$HOME"'
    if path.startswith("~/"):
        return '"$HOME"' + shlex.quote(path[1:])
    return shlex.quote(path)


def parse_facts(text: str) -> dict:
    """Turn the detection script's output into the engines dict stored on the server."""
    facts: dict[str, Any] = {}
    cuda, rocm, files, missing = [], [], [], []
    for line in text.splitlines():
        key, sep, val = line.partition("=")
        if not sep:
            continue
        val = val.strip()
        if key == "gguf":
            size, _, fpath = val.partition("|")
            if fpath:
                files.append({"path": fpath, "name": os.path.basename(fpath), "size": int(size) if size.isdigit() else 0})
        elif key == "dir_missing":
            missing.append(val)
        elif key == "gpu_cuda" and val:
            cuda.append(val)
        elif key == "gpu_rocm" and val:
            rocm.append(val)
        else:
            facts[key] = val
    has_rocm = bool(rocm or facts.get("rocm_smi") or facts.get("kfd"))
    return {
        "hostname": facts.get("hostname", ""),
        "llama_server": facts.get("llama_server") or "",
        "vllm": facts.get("vllm") or ("python module" if facts.get("vllm_module") else ""),
        "sglang": facts.get("sglang") or ("python module" if facts.get("sglang_module") else ""),
        "docker": facts.get("docker") or "",
        "gpu": "cuda" if cuda else ("rocm" if has_rocm else "none"),
        "gpus": cuda or rocm,
        "model_files": files,
        "dirs_missing": missing,
    }


def gguf_index(dirs: list) -> list:
    """[(file name, size)] of every .gguf under these folders: what Argos itself sees."""
    out = []
    for top in dirs:
        if not top or not os.path.isdir(top):
            continue
        for root, subdirs, files in os.walk(top, followlinks=True):
            subdirs[:] = [d for d in subdirs if not d.startswith(".")]
            for f in files:
                if f.lower().endswith(".gguf"):
                    try:
                        out.append((f, os.path.getsize(os.path.join(root, f))))
                    except OSError:
                        pass
    return out[:3000]


def compare_models(remote: list, local: list) -> dict:
    """Does the server see the model files Argos sees? Files match on name and size; the folder may differ."""
    mine = {(n, sz) for n, sz in local}
    theirs = {(f["name"], f["size"]) for f in remote}
    return {"found": len(theirs), "argos": len(mine), "same": mine <= theirs, "common": len(mine & theirs),
            "missing_here": sorted(n for n, _ in mine - theirs)[:30], "only_here": sorted(n for n, _ in theirs - mine)[:30],
            "differ_in_size": sorted({n for n, _ in mine - theirs} & {n for n, _ in theirs - mine})[:30]}


class Servers:
    """Local plus SSH servers: keys, Check, seeding from a file or the environment."""

    def __init__(self, store: Store, ssh_dir: str, run: Optional[RunFn] = None,
                 local_models: Optional[Callable[[], list]] = None):
        self.store = store
        self.ssh_dir = ssh_dir
        self.run = run or _run
        self.local_models = local_models or (lambda: [])  # [(file name, size)] that Argos itself sees

    # ----- local ----------------------------------------------------------
    def ensure_local(self) -> dict:
        row = next((s for s in self.store.list_servers() if s["kind"] == LOCAL), None)
        if row:
            return row
        row = self.store.save_server({"name": "Local", "kind": LOCAL, "host": "", "port": 22, "gpu": "auto",
                                      "env_prefix": "", "llama_server": "", "model_dirs": [], "source": "builtin"})
        if not any(s["is_default"] for s in self.store.list_servers()):
            self.store.set_default_server(row["id"])
        return self.store.get_server(row["id"])  # type: ignore[return-value]

    # ----- keys -----------------------------------------------------------
    def default_launcher(self) -> str:
        """What a prefab with no program of its own starts: the default server's."""
        for server in self.store.list_servers():
            if server.get("is_default"):
                return launcher_for(server)
        return ""

    def default_parts(self) -> Optional[dict]:
        """``launch_parts`` for the default server (the one Profiles launches on), or None for this machine."""
        server = next((s for s in self.store.list_servers() if s.get("is_default")), None)
        if not server or server.get("kind") == LOCAL:
            return None
        return launch_parts(server, self.key_path(server["id"]) if self.has_key(server["id"]) else "")

    def key_path(self, server_id: int) -> str:
        return os.path.join(self.ssh_dir, f"server-{int(server_id)}")

    def known_hosts(self) -> str:
        return os.path.join(self.ssh_dir, "known_hosts")

    def has_key(self, server_id: int) -> bool:
        return os.path.exists(self.key_path(server_id)) and os.path.exists(self.key_path(server_id) + ".pub")

    def make_key(self, server: dict, force: bool = False) -> None:
        if server["kind"] == LOCAL:
            raise ServerError("Local needs no key")
        if self.has_key(server["id"]) and not force:
            return
        if not shutil.which("ssh-keygen"):
            raise ServerError("ssh-keygen is not installed in this container (add openssh-client to the image)")
        os.makedirs(self.ssh_dir, mode=0o700, exist_ok=True)
        path = self.key_path(server["id"])
        for p in (path, path + ".pub"):
            if os.path.exists(p):
                os.remove(p)
        proc = self.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", f"argos-{server['name']}", "-f", path], "", 30)
        if proc.returncode != 0:
            raise ServerError(f"ssh-keygen failed: {(proc.stderr or proc.stdout).strip()[:200]}")

    def public_key(self, server_id: int) -> str:
        try:
            with open(self.key_path(server_id) + ".pub", encoding="utf-8") as f:
                return f.read().strip()
        except OSError:
            return ""

    def install_command(self, server: dict) -> str:
        """The one-liner to run once in a terminal: puts Argos' public key in the server's authorized_keys."""
        pub = self.public_key(server["id"])
        if not pub:
            return ""
        remote = ("KEY=" + shlex.quote(pub) + " && mkdir -p ~/.ssh && chmod 700 ~/.ssh && touch ~/.ssh/authorized_keys && "
                  '(grep -qxF "$KEY" ~/.ssh/authorized_keys || printf "%s\\n" "$KEY" >> ~/.ssh/authorized_keys) && '
                  "chmod 600 ~/.ssh/authorized_keys")
        return f"ssh -o StrictHostKeyChecking=accept-new -p {int(server['port'])} -- {shlex.quote(server['host'])} {shlex.quote(remote)}"

    # ----- check ----------------------------------------------------------
    def _argv(self, server: dict) -> list[str]:
        remote = "if command -v bash >/dev/null 2>&1; then exec bash -l -s; else exec sh -s; fi"
        if server["kind"] == LOCAL:
            return ["sh", "-c", remote]
        return ["ssh", "-i", self.key_path(server["id"]), "-p", str(server["port"]),
                "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new",
                "-o", f"UserKnownHostsFile={self.known_hosts()}", "-o", "ConnectTimeout=8",
                "--", server["host"], remote]

    def check(self, server_id: int, timeout: float = 30) -> dict:
        server = self.store.get_server(server_id)
        if not server:
            raise ServerError("no such server")
        if server["kind"] != LOCAL and not self.has_key(server["id"]):
            self.store.set_server_check(server_id, "no-key", None, "No key yet. Click Key, then run the command once.", {})
            return self.store.get_server(server_id)  # type: ignore[return-value]
        if server["kind"] != LOCAL:
            os.makedirs(self.ssh_dir, mode=0o700, exist_ok=True)
        started = time.time()
        try:
            proc = self.run(self._argv(server), detection_script(server), timeout)
        except subprocess.TimeoutExpired:
            self.store.set_server_check(server_id, "failed", None, f"no answer after {int(timeout)}s", {})
            return self.store.get_server(server_id)  # type: ignore[return-value]
        except OSError as exc:
            self.store.set_server_check(server_id, "failed", None, f"could not run ssh: {exc}", {})
            return self.store.get_server(server_id)  # type: ignore[return-value]
        latency = round((time.time() - started) * 1000, 1)
        if proc.returncode != 0 or "argos=ok" not in (proc.stdout or ""):
            msg = ((proc.stderr or "").strip().splitlines() or ["connection failed"])[-1]
            self.store.set_server_check(server_id, "failed", None, msg, {})
        else:
            engines = parse_facts(proc.stdout)
            files = engines.pop("model_files")
            engines["models"] = {**compare_models(files, self.local_models()), "folders": len(server["model_dirs"]),
                                 "dirs_missing": engines.pop("dirs_missing")}
            self.store.set_server_check(server_id, "connected", latency, "", engines)
        return self.store.get_server(server_id)  # type: ignore[return-value]

    # ----- seeding ----------------------------------------------------------
    def seed(self, items: list, source: str = "file") -> dict:
        """Create or refresh servers from a config file / environment. UI-made servers are never overwritten."""
        created, updated, skipped, errors = [], [], [], []
        want_default = None
        for raw in items or []:
            if not isinstance(raw, dict):
                continue
            try:
                d = clean_server({**raw, "host": raw.get("host") or ""})
            except ServerError as exc:
                errors.append(f"{raw.get('name') or '?'}: {exc}")
                continue
            d["source"] = source
            have = self.store.server_by_name(d["name"])
            if have is None:
                row = self.store.save_server(d)
                created.append(d["name"])
            elif have["source"] == source:
                row = self.store.save_server({k: v for k, v in d.items() if k != "name"}, have["id"])
                updated.append(d["name"])
            else:
                skipped.append(d["name"])
                continue
            if raw.get("default"):
                want_default = row["id"]
        if want_default:
            self.store.set_default_server(want_default)
        return {"created": created, "updated": updated, "skipped": skipped, "errors": errors}


def load_seed(path: str = "", env_json: str = "") -> list:
    """Server definitions from a YAML/JSON file (``servers.yaml``) and/or a JSON env value."""
    items: list = []
    if path and os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or []
        items += data.get("servers", []) if isinstance(data, dict) else data
    if env_json.strip():
        data = json.loads(env_json)
        items += data.get("servers", []) if isinstance(data, dict) else data
    return items


def from_odysseus(raw: list) -> tuple[list[dict], list[str]]:
    """Map the servers Odysseus' Cookbook lists to Argos servers. Field names are matched loosely:
    whatever has no host (Local, usually) is skipped."""
    out, notes = [], []
    for s in raw or []:
        if not isinstance(s, dict):
            continue
        name = str(s.get("name") or s.get("label") or s.get("id") or "").strip()
        host = str(s.get("host") or s.get("hostname") or s.get("address") or s.get("remote_host") or s.get("ssh") or "").strip()
        if host.lower().startswith(("http://", "https://")):
            notes.append(f"{name or host}: a URL, not an SSH host, skipped")
            continue
        if not host or str(s.get("kind") or s.get("type") or "").lower() == "local" or name.lower() == "local":
            notes.append(f"{name or '(unnamed)'}: no SSH host, skipped")
            continue
        user = str(s.get("user") or s.get("username") or "").strip()
        if "@" not in host and user:
            host = f"{user}@{host}"
        dirs = s.get("model_dirs") or s.get("modelDirs") or s.get("model_directories") or []
        env = str(s.get("env") or "").lower()
        if env not in ("venv", "conda"):
            env = "venv" if s.get("venv") else ("conda" if s.get("conda_env") else "none")
        color = str(s.get("color") or "")
        out.append({"name": name or host, "host": host, "port": s.get("port") or 22, "gpu": "auto",
                    "env_kind": env if env in ("venv", "conda") else "none",
                    "env_value": str(s.get("envPath") or s.get("env_path") or s.get("venv") or s.get("conda_env") or ""),
                    "model_dirs": dirs if isinstance(dirs, list) else [], "download_dir": str(s.get("downloadDir") or ""),
                    "color": color if re.match(r"^#[0-9a-fA-F]{3,8}$", color) else ""})
    return out, notes


__all__ = ["Servers", "ServerError", "clean_server", "launch_parts", "on_server", "compare_models", "env_prefix_for", "gguf_index", "load_seed", "from_odysseus", "parse_facts", "detection_script", "LOCAL"]
