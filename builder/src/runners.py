"""Local runners: servers that run inside Argos itself (Mode 1), beside the llama.cpp ones.

Argos never runs a model itself: the engine runs a start command and talks HTTP to the port it opens.
A runner is what that command starts for models llama-server cannot serve - whisper.cpp for speech to
text, Kokoro for text to speech, or anything installed from a git repo. It lives in its own folder under
the runners directory (``/data/runners`` in the container), so a rebuilt image keeps it.

The user never picks a runner per model. The model's task picks it (a speech-to-text file goes to the
whisper runner, the Kokoro runner serves its own voices), and an override per model only fixes a wrong
guess. From the installed runners and the models folder this writes one engine file,
``runners.generated.yaml``, beside the schedule's, which the engine watches like any other.

Add-ons are tools the runners or the proxy use rather than servers: ffmpeg converts uploaded audio into
the WAV whisper reads.
"""
from __future__ import annotations

import io
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
import zipfile
from typing import Any, Callable, Optional

WINDOWS = sys.platform == "win32"
EXE = ".exe" if WINDOWS else ""
DEVICES = ("cpu", "cuda", "rocm")
RUNNERS_KEY = "_runners"
FRAGMENT = "runners.generated.yaml"

# task -> the runner type that serves it
TASK_RUNNER = {"speech-to-text": "whisper", "text-to-speech": "kokoro"}

WHISPER_RELEASE = "b5454"
WHISPER_ZIPS = {  # prebuilt Windows builds; elsewhere whisper.cpp is built from source
    "cpu": f"https://github.com/ggml-org/whisper.cpp/releases/download/{WHISPER_RELEASE}/whisper-bin-x64.zip",
    "cuda": f"https://github.com/ggml-org/whisper.cpp/releases/download/{WHISPER_RELEASE}/whisper-bin-win-cuda-12.4.0-x64.zip",
}
FFMPEG_ZIP = "https://github.com/GyanD/codexffmpeg/releases/download/9.0.2/ffmpeg-9.0.2-essentials_build.zip"


# ----- the runner types ------------------------------------------------------------------
TYPES: dict[str, dict] = {
    "whisper": {
        "label": "whisper.cpp", "task": "speech-to-text", "endpoint": "/v1/audio/transcriptions",
        "repo": "https://github.com/ggml-org/whisper.cpp",
        "about": "Speech to text. Serves every whisper model file (ggml-*.bin) in the models folder and in its own models folder.",
        "alias": "whisper-1",
    },
    "kokoro": {
        "label": "Kokoro", "task": "text-to-speech", "endpoint": "/v1/audio/speech",
        "repo": "https://github.com/remsky/Kokoro-FastAPI",
        "about": "Text to speech with 70+ voices. Small enough to run on the CPU.",
        "alias": "tts-1",
    },
    "custom": {
        "label": "Custom", "task": "", "endpoint": "",
        "about": "Any server from a git repo. Scan reads its README and build files for the install and start commands.",
    },
}
ADDONS: dict[str, dict] = {
    "ffmpeg": {"label": "ffmpeg", "about": "Converts uploaded audio (m4a, mp3, ogg, webm, ...) to the 16 kHz WAV whisper reads, "
                                          "before Argos passes it on. CPU is all audio needs; GPU builds only help video."},
}


def _find(root: str, names: tuple[str, ...], limit: int = 4000) -> str:
    """The first file under root with one of these names (shallowest wins)."""
    if not root or not os.path.isdir(root):
        return ""
    best, seen = "", 0
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if not d.startswith(".") and d not in ("node_modules", "__pycache__")]
        for f in files:
            seen += 1
            if f.lower() in names and (not best or dirpath.count(os.sep) < best.count(os.sep)):
                best = os.path.join(dirpath, f)
        if seen > limit:
            break
    return best


def _venv_python(folder: str) -> str:
    p = os.path.join(folder, ".venv", "Scripts" if WINDOWS else "bin", "python" + EXE)
    return p if os.path.exists(p) else ""


def detect(kind: str, folder: str) -> dict:
    """What is installed in a runner's folder: {"installed", "program", "detail"}."""
    if kind == "whisper":
        exe = _find(folder, ("whisper-server" + EXE,))
        if not exe:
            return {"installed": False, "program": "", "detail": "no whisper-server found"}
        gpu = "cuda" if os.path.exists(os.path.join(os.path.dirname(exe), "ggml-cuda.dll")) or os.path.exists(os.path.join(os.path.dirname(exe), "libggml-cuda.so")) else \
              "rocm" if os.path.exists(os.path.join(os.path.dirname(exe), "libggml-hip.so")) else "cpu"
        return {"installed": True, "program": exe, "detail": f"whisper-server ({gpu} build)", "build": gpu}
    if kind == "kokoro":
        py = _venv_python(folder)
        model = os.path.exists(os.path.join(folder, "api", "src", "models", "v1_0", "kokoro-v1_0.pth"))
        if not py:
            return {"installed": False, "program": "", "detail": "no Python environment yet"}
        voices = len([f for f in os.listdir(os.path.join(folder, "api", "src", "voices", "v1_0"))]) if os.path.isdir(os.path.join(folder, "api", "src", "voices", "v1_0")) else 0
        return {"installed": model, "program": py, "detail": f"{voices} voices" + ("" if model else ", model not downloaded")}
    if kind == "ffmpeg":
        exe = _find(folder, ("ffmpeg" + EXE,)) or shutil.which("ffmpeg") or ""
        return {"installed": bool(exe), "program": exe, "detail": "on PATH" if exe and not exe.startswith(folder) else ""}
    return {"installed": False, "program": "", "detail": ""}


# ----- install steps ---------------------------------------------------------------------
def install_steps(kind: str, folder: str, device: str, opts: dict) -> list[dict]:
    """The steps Install runs, each {"run": shell command} or {"download": url, "unzip": dest}. Shown before running."""
    repo = (opts.get("repo") or TYPES.get(kind, {}).get("repo") or "").strip()
    if kind == "kokoro":
        extra = {"cpu": "cpu", "cuda": "gpu", "rocm": "rocm"}[device]
        steps = [] if os.path.isdir(os.path.join(folder, ".git")) else [{"run": f'git clone --depth 1 "{repo}" "{folder}"', "cwd": os.path.dirname(folder)}]
        steps += [{"run": "uv venv --python 3.11 .venv"}, {"run": f'uv pip install --python .venv -e ".[{extra}]"'}]
        if opts.get("torch_index"):  # e.g. a gfx906 PyTorch build
            steps.append({"run": f'uv pip install --python .venv --reinstall torch --index-url "{opts["torch_index"]}"'})
        steps.append({"run": f'"{os.path.join(".venv", "Scripts" if WINDOWS else "bin", "python" + EXE)}" docker/scripts/download_model.py --output api/src/models/v1_0'})
        return steps
    if kind == "whisper":
        if WINDOWS and device in WHISPER_ZIPS and not opts.get("repo"):
            return [{"download": WHISPER_ZIPS[device], "unzip": os.path.join(folder, "bin")}]
        # the architecture(s) the HIP code is compiled for, e.g. gfx906 (MI50/MI60) - a chip family, not one card
        targets = ";".join(t.strip() for t in str(opts.get("gpu_target") or "gfx906").replace(";", ",").split(",") if t.strip())
        flags = {"cpu": "", "cuda": "-DGGML_CUDA=1", "rocm": f'-DGGML_HIP=1 "-DAMDGPU_TARGETS={targets}"'}[device]
        steps = [] if os.path.isdir(os.path.join(folder, ".git")) else [{"run": f'git clone --depth 1 "{repo}" "{folder}"', "cwd": os.path.dirname(folder)}]
        return steps + [{"run": f"cmake -B build {flags} -DCMAKE_BUILD_TYPE=Release".strip()},
                        {"run": "cmake --build build -j --config Release --target whisper-server"}]
    if kind == "ffmpeg":
        if WINDOWS:
            return [{"download": FFMPEG_ZIP, "unzip": folder}]
        return [{"run": "apt-get install -y --no-install-recommends ffmpeg || echo 'install ffmpeg with your package manager'"}]
    if kind == "custom":
        steps = [] if not repo or os.path.isdir(os.path.join(folder, ".git")) else [{"run": f'git clone --depth 1 "{repo}" "{folder}"', "cwd": os.path.dirname(folder)}]
        return steps + [{"run": line} for line in (opts.get("install") or "").splitlines() if line.strip()]
    return []


class Installer:
    """Runs one install at a time per runner, in the background, logging every line."""

    def __init__(self):
        self.jobs: dict[str, dict] = {}
        self._lock = threading.Lock()

    def status(self, rid: str) -> dict:
        return dict(self.jobs.get(rid) or {"state": "idle", "log": ""})

    def start(self, rid: str, folder: str, steps: list[dict], done: Callable[[bool, str], None]) -> dict:
        with self._lock:
            if (self.jobs.get(rid) or {}).get("state") == "running":
                raise RuntimeError("already installing")
            job = {"state": "running", "log": "", "step": 0, "steps": len(steps), "started": time.time()}
            self.jobs[rid] = job
        os.makedirs(folder, exist_ok=True)
        threading.Thread(target=self._run, args=(job, folder, steps, done), daemon=True, name=f"install-{rid}").start()
        return dict(job)

    @staticmethod
    def _say(job: dict, text: str) -> None:
        job["log"] = (job["log"] + text)[-200_000:]

    def _run(self, job: dict, folder: str, steps: list[dict], done) -> None:
        ok, err = True, ""
        env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
        for i, step in enumerate(steps):
            job["step"] = i + 1
            try:
                if "download" in step:
                    self._say(job, f"$ download {step['download']}\n")
                    with urllib.request.urlopen(step["download"], timeout=60) as r:  # noqa: S310 - a fixed release URL
                        data, total, got = io.BytesIO(), int(r.headers.get("Content-Length") or 0), 0
                        while chunk := r.read(1 << 20):
                            data.write(chunk)
                            got += len(chunk)
                            job["progress"] = round(got / total * 100) if total else None
                    self._say(job, f"  {got / 2 ** 20:.0f} MiB, unpacking to {step['unzip']}\n")
                    with zipfile.ZipFile(data) as z:
                        z.extractall(step["unzip"])
                else:
                    cwd = step.get("cwd") or folder
                    os.makedirs(cwd, exist_ok=True)
                    self._say(job, f"$ {step['run']}\n")
                    p = subprocess.Popen(step["run"], shell=True, cwd=cwd, env=env, stdout=subprocess.PIPE,
                                         stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
                    for line in p.stdout:  # type: ignore[union-attr]
                        self._say(job, line)
                    if p.wait() != 0:
                        raise RuntimeError(f"exited with {p.returncode}")
            except Exception as exc:  # noqa: BLE001 - reported in the log and the status
                ok, err = False, f"step {i + 1} failed: {exc}"
                self._say(job, f"\n{err}\n")
                break
        job["state"] = "done" if ok else "failed"
        job["error"] = err
        job["ended"] = time.time()
        done(ok, err)


# ----- scanning a repo for its install and start commands ---------------------------------
FENCE = re.compile(r"```(?:bash|sh|shell|console|powershell|ps1)?\s*\n(.*?)```", re.S | re.I)
INSTALL_HINT = re.compile(r"^\s*(?:[$>]\s*)?((?:cmake|make|pip3?|uv|poetry|npm|pnpm|cargo|go|meson|ninja)\b[^\n]*)", re.M | re.I)
START_HINT = re.compile(r"^\s*(?:[$>]\s*)?((?:\./|python3?\s|uv run|uvicorn\s|node\s|npm start)[^\n]*)", re.M | re.I)
INLINE = re.compile(r"(?im)^\s*>?\s*\**(build|install|run|start)\**\s*:\s*`([^`]+)`")


def scan(folder: str) -> dict:
    """Guess the install and start commands from what a cloned repo contains. Nothing is run."""
    found: dict[str, list[dict]] = {"install": [], "start": []}

    def add(kind: str, cmd: str, source: str) -> None:
        cmd = cmd.strip().rstrip("\\").strip()
        if cmd and not any(c["cmd"] == cmd for c in found[kind]):
            found[kind].append({"cmd": cmd, "source": source})

    readme = next((os.path.join(folder, f) for f in ("README.md", "readme.md", "README.rst", "README") if os.path.exists(os.path.join(folder, f))), "")
    if readme:
        text = open(readme, encoding="utf-8", errors="replace").read()
        for m in INLINE.finditer(text):
            line = text[:m.start()].count("\n") + 1
            add("start" if m.group(1).lower() in ("run", "start") else "install", m.group(2), f"README line {line}")
        for block in FENCE.finditer(text):
            line = text[:block.start()].count("\n") + 1
            for m in INSTALL_HINT.finditer(block.group(1)):
                add("install", m.group(1), f"README line {line}")
            for m in START_HINT.finditer(block.group(1)):
                if "--port" in m.group(1) or "server" in m.group(1) or "main" in m.group(1):
                    add("start", m.group(1), f"README line {line}")
    for name, cmd in (("requirements.txt", "uv venv --python 3.11 .venv && uv pip install --python .venv -r requirements.txt"),
                      ("pyproject.toml", "uv venv --python 3.11 .venv && uv pip install --python .venv -e ."),
                      ("CMakeLists.txt", "cmake -B build -DCMAKE_BUILD_TYPE=Release && cmake --build build -j --config Release"),
                      ("package.json", "npm install")):
        if os.path.exists(os.path.join(folder, name)):
            add("install", cmd, name)
    for script in ("install.sh", "build.sh", "setup.sh", "start.sh", "start-cpu.sh", "run.sh"):
        if os.path.exists(os.path.join(folder, script)):
            add("start" if script.startswith(("start", "run")) else "install", f"bash {script}", script)
    if os.path.exists(os.path.join(folder, "main.py")):
        add("start", "python main.py --listen 127.0.0.1 --port ${PORT}", "main.py")
    dockerfile = _find(folder, ("dockerfile",))
    if dockerfile:
        rel = os.path.relpath(dockerfile, folder)
        for line in open(dockerfile, encoding="utf-8", errors="replace"):
            m = re.match(r"\s*(CMD|ENTRYPOINT)\s+(.*)", line)
            if m:
                cmd = m.group(2).strip()
                try:
                    cmd = " ".join(json.loads(cmd)) if cmd.startswith("[") else cmd
                except ValueError:
                    pass
                add("start", cmd, rel)
    text = (open(readme, encoding="utf-8", errors="replace").read().lower() if readme else "") + " " + folder.lower()
    task = "speech-to-text" if re.search(r"whisper|transcri|speech[- ]to[- ]text|asr", text) else \
           "text-to-speech" if re.search(r"text[- ]to[- ]speech|\btts\b|kokoro|voice", text) else \
           "image" if re.search(r"comfyui|diffusion|image generation", text) else ""
    return {"install": found["install"][:8], "start": found["start"][:8], "task": task}


# ----- the registry and the engine file ---------------------------------------------------
class Runners:
    def __init__(self, store, settings, on_event: Callable[..., None], models_dir: Callable[[], str], scan_media):
        self.store = store
        self.settings = settings
        self.event = on_event
        self.models_dir = models_dir
        self.scan_media = scan_media
        self.installer = Installer()

    def root(self) -> str:
        return self.settings.get("paths").get("runners_dir") or ""

    def records(self) -> dict[str, dict]:
        rec = self.store.get_settings().get(RUNNERS_KEY) or {}
        changed = False
        for kind in (*[k for k in TYPES if k != "custom"], *ADDONS):  # the built-in ones always exist
            if kind not in rec:
                # an existing install keeps the device it was built for (a CUDA whisper build starts as cuda)
                found = detect(kind, os.path.join(self.root(), kind)).get("build") or "cpu"
                rec[kind] = {"id": kind, "kind": kind, "device": found, "enabled": True, "addon": kind in ADDONS, "opts": {}}
                changed = True
        if changed:
            self.store.set_setting(RUNNERS_KEY, rec)
        return rec

    def save(self, rec: dict) -> None:
        self.store.set_setting(RUNNERS_KEY, rec)

    def folder(self, r: dict) -> str:
        return r.get("folder") or os.path.join(self.root(), r["id"])

    def view(self) -> dict:
        """Every runner and add-on with what is installed and which models it serves."""
        out = []
        models = self.served()
        for r in self.records().values():
            d = detect(r["kind"], self.folder(r)) if r["kind"] != "custom" else {
                "installed": bool((r.get("opts") or {}).get("start")), "program": "", "detail": (r.get("opts") or {}).get("task") or ""}
            t = TYPES.get(r["kind"]) or ADDONS.get(r["kind"]) or {}
            out.append({**r, **d, "label": r.get("label") or t.get("label") or r["id"], "about": t.get("about", ""),
                        "task": t.get("task") or (r.get("opts") or {}).get("task", ""), "endpoint": t.get("endpoint", ""),
                        "folder": self.folder(r), "job": self.installer.status(r["id"]),
                        "models": [m for m in models if m["runner"] == r["id"]],
                        "steps": install_steps(r["kind"], self.folder(r), r.get("device", "cpu"), r.get("opts") or {})})
        out.sort(key=lambda x: (x.get("addon", False), x["kind"] == "custom", x["id"]))
        return {"root": self.root(), "runners": out, "engine_file": self.engine_path(), "devices": DEVICES,
                "types": {k: {"label": v["label"], "about": v["about"]} for k, v in TYPES.items()}}

    def ffmpeg(self) -> str:
        r = self.records().get("ffmpeg") or {}
        custom = ((r.get("opts") or {}).get("program") or "").strip()
        if custom and os.path.exists(custom):
            return custom
        return detect("ffmpeg", self.folder(r)).get("program", "") if r.get("enabled", True) else ""

    # ----- models -----
    def served(self) -> list[dict]:
        """Every model a runner serves: {"id", "name", "runner", "kind", "task", "path", "size", "auto"}."""
        rec = self.records()
        overrides = (self.store.get_settings().get("_runner_overrides") or {})
        out = []
        media = []
        try:
            media = self.scan_media(self.models_dir())
        except Exception:  # noqa: BLE001 - a missing models folder is not this page's error
            pass
        w = rec.get("whisper") or {}
        own = os.path.join(self.folder(w), "models") if w else ""
        if own and os.path.isdir(own):
            for f in sorted(os.listdir(own)):
                if f.startswith("ggml-") and f.endswith(".bin"):
                    media.append({"id": re.sub(r"[^a-z0-9._-]+", "-", f[:-4].lower()), "name": f[:-4], "path": os.path.join(own, f),
                                  "size": os.path.getsize(os.path.join(own, f)), "task": "speech-to-text", "format": "ggml"})
        seen = set()
        for m in media:
            if m["id"] in seen:
                continue
            seen.add(m["id"])
            auto = TASK_RUNNER.get(m.get("task") or "")
            if auto == "whisper" and m.get("format") != "ggml":
                auto = None  # whisper.cpp reads ggml files only
            runner = overrides.get(m["id"], auto) or ""
            if runner == "kokoro":
                runner = ""  # Kokoro serves its own model, not files from the folder
            if runner and runner in rec and rec[runner].get("enabled", True):
                out.append({"id": m["id"], "name": m["name"], "runner": runner, "task": m.get("task"), "path": m["path"],
                            "size": m.get("size") or 0, "auto": runner == auto})
        k = rec.get("kokoro")
        if k and k.get("enabled", True) and detect("kokoro", self.folder(k))["installed"]:
            out.append({"id": "kokoro", "name": "Kokoro voices", "runner": "kokoro", "task": "text-to-speech", "path": "", "size": 0, "auto": True})
        for r in rec.values():
            o = r.get("opts") or {}
            if r["kind"] == "custom" and o.get("start") and r.get("enabled", True):
                out.append({"id": r["id"], "name": r.get("label") or r["id"], "runner": r["id"], "task": o.get("task", ""), "path": "",
                            "size": 0, "auto": True})
        return out

    def entries(self) -> dict[str, dict]:
        """model id -> engine model config for every served model whose runner is installed."""
        rec = self.records()
        out: dict[str, dict] = {}
        default_stt = (rec.get("whisper") or {}).get("opts", {}).get("default_model")
        stt = [m for m in self.served() if m["runner"] == "whisper"]
        if stt and default_stt not in {m["id"] for m in stt}:
            default_stt = sorted(stt, key=lambda m: m["size"])[0]["id"]
        for m in self.served():
            r = rec[m["runner"]]
            device = r.get("device", "cpu")
            folder = self.folder(r)
            if r["kind"] == "whisper":
                d = detect("whisper", folder)
                if not d["installed"]:
                    continue
                args = [d["program"], "--host", "127.0.0.1", "--port", "${PORT}", "-m", m["path"],
                        "--inference-path", "/v1/audio/transcriptions"] + (["-ng"] if device == "cpu" else [])
                e = {"cmd": _join(args), "cwd": os.path.dirname(d["program"]), "checkEndpoint": "/",
                     "vram": 0.1 if device == "cpu" else round(m["size"] / 2 ** 30 * 1.3 + 0.3, 2),
                     "capabilities": {"in": ["audio"], "out": ["text"]}}
                if m["id"] == default_stt:
                    e["aliases"] = ["whisper-1"]
            elif r["kind"] == "kokoro":
                d = detect("kokoro", folder)
                sep = ";" if WINDOWS else ":"
                e = {"cmd": _join([d["program"], "-m", "uvicorn", "api.src.main:app", "--host", "127.0.0.1", "--port", "${PORT}"]),
                     "cwd": folder, "checkEndpoint": "/health", "aliases": ["tts-1", "tts-1-hd"],
                     "env": [f"USE_GPU={'false' if device == 'cpu' else 'true'}", "PYTHONUTF8=1", f"PROJECT_ROOT={folder}",
                             f"PYTHONPATH={folder}{sep}{os.path.join(folder, 'api')}", "MODEL_DIR=src/models",
                             "VOICES_DIR=src/voices/v1_0", f"WEB_PLAYER_PATH={os.path.join(folder, 'web')}"],
                     "vram": 0.1 if device == "cpu" else 1.5,
                     "capabilities": {"in": ["text"], "out": ["audio"]}}
            elif r["kind"] == "custom":
                o = r.get("opts") or {}
                if not o.get("start"):
                    continue
                e = {"cmd": o["start"], "cwd": folder, "checkEndpoint": o.get("check") or "/",
                     "vram": float(o.get("vram") or 0.1)}
                if o.get("alias"):
                    e["aliases"] = [a.strip() for a in str(o["alias"]).split(",") if a.strip()]
            else:
                continue
            e["metadata"] = {"argos": {"runner": r["kind"], "runner_id": r["id"], "task": m.get("task") or "", "device": device}}
            out[m["id"]] = e
        return out

    def engine_path(self) -> str:
        from .scheduler import engine_file
        f = engine_file()
        return os.path.join(os.path.dirname(os.path.abspath(f)), FRAGMENT) if f else ""

    def apply(self) -> bool:
        """Write the engine file. True when it changed."""
        path = self.engine_path()
        if not path:
            return False
        import yaml
        from .scheduler import write_fragment
        text = ("# Written by Argos from Servers -> Local. Edits are overwritten.\n"
                + yaml.safe_dump({"models": self.entries()}, sort_keys=True, default_flow_style=False, allow_unicode=True))
        changed = write_fragment(path, text)
        if changed:
            self.event("runners", f"updated the engine's runner models ({len(self.entries())})", data={"file": path})
        return changed


def _join(args: list[str]) -> str:
    """A command line the engine splits back into the same words (quoted only where needed)."""
    out = []
    for a in args:
        a = a.replace("\\", "/") if WINDOWS else a
        out.append(f'"{a}"' if (" " in a or not a) else a)
    return " ".join(out)
