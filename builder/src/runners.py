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

import functools
import io
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
import zipfile
from typing import Any, Callable, Optional

WINDOWS = sys.platform == "win32"
EXE = ".exe" if WINDOWS else ""
DEVICES = ("cpu", "cuda", "rocm")
RUNNERS_KEY = "_runners"
FRAGMENT = "runners.generated.yaml"

# task -> the runner type that serves it
TASK_RUNNER = {"speech-to-text": "whisper", "text-to-speech": "kokoro", "image": "sdcpp"}

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
        "about": "Speech to text. Serves every whisper model file (ggml-*.bin) in the models folder Argos uses, the same one as the chat models.",
        "alias": "whisper-1",
    },
    "kokoro": {
        "label": "Kokoro", "task": "text-to-speech", "endpoint": "/v1/audio/speech",
        "repo": "https://github.com/remsky/Kokoro-FastAPI",
        "about": "Text to speech with 70+ voices. Small enough to run on the CPU.",
        "alias": "tts-1",
    },
    "sdcpp": {
        "label": "stable-diffusion.cpp", "task": "image", "endpoint": "/v1/images/generations",
        "repo": "https://github.com/leejet/stable-diffusion.cpp",
        "about": "Image generation. Serves every single-file image model (.gguf, .safetensors, .ckpt) in the models folder; "
                 "models that need a separate VAE or text encoder take those as extra arguments on their row.",
        "alias": "dall-e-3",
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
    if kind == "sdcpp":
        exe = _find(folder, ("sd-server" + EXE,))
        if not exe:
            return {"installed": False, "program": "", "detail": "no sd-server found"}
        names = {f.lower() for f in os.listdir(os.path.dirname(exe))}
        gpu = "cuda" if any("cuda" in n or "cublas" in n for n in names) else "rocm" if any("hip" in n or "rocm" in n for n in names) else "cpu"
        return {"installed": True, "program": exe, "detail": f"sd-server ({gpu} build)", "build": gpu}
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
        torch = torch_source(opts.get("torch_index") or "") if device == "rocm" else None
        if torch:
            # the custom build first, in the Python it was built for; then Kokoro without its own PyTorch extra,
            # so the stock ROCm wheel never replaces it
            steps += [{"run": f"uv venv --python {torch['python']} .venv"}, torch["step"],
                      {"run": 'uv pip install --python .venv -e .'}]
        else:
            steps += [{"run": "uv venv --python 3.11 .venv"}, {"run": f'uv pip install --python .venv -e ".[{extra}]"'}]
        steps.append({"run": f'"{os.path.join(".venv", "Scripts" if WINDOWS else "bin", "python" + EXE)}" docker/scripts/download_model.py --output api/src/models/v1_0'})
        return steps
    if kind == "whisper":
        if WINDOWS and device in WHISPER_ZIPS and not opts.get("repo"):
            return [{"download": WHISPER_ZIPS[device], "unzip": os.path.join(folder, "bin")}]
        # the architecture(s) the HIP code is compiled for, e.g. gfx906 (MI50/MI60) - a chip family, not one card
        wanted = str(opts.get("gpu_target") or "").strip() or ",".join(rocm_archs()) if device == "rocm" else ""
        targets = ";".join(t.strip().lower() for t in (wanted or "gfx906").replace(";", ",").split(",") if t.strip())
        flags = {"cpu": "", "cuda": "-DGGML_CUDA=1", "rocm": f'-DGGML_HIP=1 "-DAMDGPU_TARGETS={targets}"'}[device]
        steps = [{"needs": build_needs(device)}]
        steps += [] if os.path.isdir(os.path.join(folder, ".git")) else [{"run": f'git clone --depth 1 "{repo}" "{folder}"', "cwd": os.path.dirname(folder)}]
        # ROCm: point CMake at ROCm's own clang, as whisper.cpp's HIP build expects (an SDK image sets these already)
        hip = ('if command -v hipconfig >/dev/null 2>&1; then export HIPCXX="$(hipconfig -l)/clang" HIP_PATH="$(hipconfig -R)"; fi; '
               if device == "rocm" and not WINDOWS else "")
        return steps + [{"run": f"{hip}cmake -B build {flags} -DCMAKE_BUILD_TYPE=Release".strip()},
                        {"run": "cmake --build build -j --config Release --target whisper-server"}]
    if kind == "sdcpp":
        if WINDOWS and not opts.get("repo"):
            asset = {"cpu": r"bin-win-cpu-x64\.zip$", "cuda": r"bin-win-cuda12-x64\.zip$", "rocm": r"bin-win-rocm-[\d.]+-x64\.zip$"}[device]
            steps = [{"github_asset": "leejet/stable-diffusion.cpp", "match": asset, "unzip": os.path.join(folder, "bin")}]
            if device == "cuda":  # the CUDA 12 runtime it needs: reused from another install when one has it
                steps.append({"cuda_runtime": os.path.join(folder, "bin")})
            return steps
        steps = [{"needs": build_needs(device)}]
        steps += [] if os.path.isdir(os.path.join(folder, ".git")) else [{"run": f'git clone --depth 1 --recursive "{repo}" "{folder}"', "cwd": os.path.dirname(folder)}]
        if device == "rocm":
            wanted = str(opts.get("gpu_target") or "").strip() or ",".join(rocm_archs())
            targets = ";".join(t.strip().lower() for t in (wanted or "gfx906").replace(";", ",").split(",") if t.strip())
            rocm = "${ROCM_PATH:-/opt/rocm}"
            flags = (f'-DSD_HIPBLAS=ON "-DGPU_TARGETS={targets}" "-DAMDGPU_TARGETS={targets}" '
                     f"-DCMAKE_C_COMPILER={rocm}/llvm/bin/clang -DCMAKE_CXX_COMPILER={rocm}/llvm/bin/clang++")
        else:
            flags = {"cpu": "", "cuda": "-DSD_CUDA=ON"}[device]
        return steps + [{"run": f"cmake -B build {flags} -DCMAKE_BUILD_TYPE=Release".replace("  ", " ")},
                        {"run": "cmake --build build -j --config Release --target sd-server"}]
    if kind == "ffmpeg":
        if WINDOWS:
            return [{"download": FFMPEG_ZIP, "unzip": folder}]
        return [{"run": "apt-get install -y --no-install-recommends ffmpeg || echo 'install ffmpeg with your package manager'"}]
    if kind == "custom":
        steps = [] if not repo or os.path.isdir(os.path.join(folder, ".git")) else [{"run": f'git clone --depth 1 "{repo}" "{folder}"', "cwd": os.path.dirname(folder)}]
        return steps + [{"run": line} for line in (opts.get("install") or "").splitlines() if line.strip()]
    return []


def build_needs(device: str) -> list[str]:
    """What a source build needs on this machine: the build tools, and the GPU compiler for the device."""
    return ["git", "cmake", "c++"] + (["hipconfig"] if device == "rocm" else ["nvcc"] if device == "cuda" else [])


APT = {"git": "git", "cmake": "cmake", "c++": "build-essential"}


def missing_tools(needs: list[str]) -> list[str]:
    from . import gputools
    path = gputools.env().get("PATH")
    found = {"c++": shutil.which("c++", path=path) or shutil.which("g++", path=path) or shutil.which("clang++", path=path)}
    return [n for n in needs if not (found.get(n) if n in found else shutil.which(n, path=path))]


def torch_source(value: str) -> Optional[dict]:
    """How to install a custom PyTorch build: {"python", "step", "kind"}, from what the field holds.

    - an index.txt that lists wheel files (how ML-gfx906 publishes its gfx906 builds): every wheel in it
    - a pip index URL (ends in / or has /whl/): torch, torchvision and torchaudio from that index
    - wheel files or URLs ending in .whl, separated by spaces or commas: exactly those
    The Python version comes from the wheel names (cp312 -> 3.12) when they say; 3.12 otherwise for index.txt.
    """
    v = (value or "").strip()
    if not v:
        return None
    parts = [x for x in re.split(r"[\s,]+", v) if x]
    if v.lower().endswith("index.txt"):
        return {"kind": "index.txt", "python": "3.12", "step": {"wheels_index": v}}
    if all(x.lower().endswith(".whl") for x in parts):
        py = next((f"3.{m.group(1)}" for x in parts if (m := re.search(r"-cp3(\d+)-", x))), "3.12")
        return {"kind": "wheels", "python": py, "step": {"run": "uv pip install --python .venv " + " ".join(f'"{x}"' for x in parts)}}
    return {"kind": "index", "python": "3.11",
            "step": {"run": f'uv pip install --python .venv torch torchvision torchaudio --index-url "{v}"'}}


TORCH_BUILDS_README = "https://raw.githubusercontent.com/mixa3607/ML-gfx906/HEAD/pytorch/README.md"
_torch_cache: dict = {}


def torch_builds() -> list[dict]:
    """Known custom PyTorch builds for the picker: ML-gfx906's wheel table, read from its README (kept an hour)."""
    if _torch_cache.get("at", 0) > time.time() - 3600:
        return _torch_cache["builds"]
    builds = []
    try:
        with urllib.request.urlopen(urllib.request.Request(TORCH_BUILDS_README, headers={"User-Agent": "argos"}), timeout=15) as r:  # noqa: S310
            text = r.read().decode("utf-8", "replace")
        for m in re.finditer(r"^\|\s*([\d.]+)\s*\|\s*([\d.]+)\s*\|\s*(https://\S+?/index\.txt)\s*\|", text, re.M):
            builds.append({"source": "ML-gfx906", "gpu": "gfx906 (MI50, MI60, Radeon VII)", "torch": m.group(1), "rocm": m.group(2),
                           "python": "3.12", "url": m.group(3)})
    except Exception:  # noqa: BLE001 - offline: the field still takes a pasted link
        pass
    _torch_cache.update(at=time.time(), builds=builds)
    return builds


ROCM_ARCH = re.compile(r"\bgfx[0-9a-f]{3,4}\b")


def _url(u: str) -> str:
    """A URL with "+" and spaces in its path encoded: S3 answers 403 to a raw "+" (torch-2.14.1+gfx906/...)."""
    parts = urllib.parse.urlsplit(u)
    return urllib.parse.urlunsplit(parts._replace(path=urllib.parse.quote(urllib.parse.unquote(parts.path), safe="/")))


@functools.lru_cache(maxsize=1)
def rocm_archs() -> tuple[str, ...]:
    """The GPU architectures on this machine (rocminfo, else rocm-smi). Empty when neither answers."""
    for argv in (["rocminfo"], ["rocm-smi", "--showproductname"]):
        try:
            out = subprocess.run(argv, capture_output=True, text=True, timeout=20).stdout
        except (OSError, subprocess.SubprocessError):
            continue
        found = sorted(set(ROCM_ARCH.findall(out)) - {"gfx000"})
        if found:
            return tuple(found)
    return ()


class Installer:
    """Runs one install at a time per runner, in the background, logging every line."""

    def __init__(self):
        self.jobs: dict[str, dict] = {}
        self._lock = threading.Lock()

    def status(self, rid: str) -> dict:
        return dict(self.jobs.get(rid) or {"state": "idle", "log": ""})

    def start(self, rid: str, folder: str, steps: list[dict], done: Callable[[bool, str], None], device: str = "") -> dict:
        with self._lock:
            if (self.jobs.get(rid) or {}).get("state") == "running":
                raise RuntimeError("already installing")
            job = {"state": "running", "log": "", "step": 0, "steps": len(steps), "started": time.time()}
            self.jobs[rid] = job
        os.makedirs(folder, exist_ok=True)
        threading.Thread(target=self._run, args=(job, folder, steps, done, device), daemon=True, name=f"install-{rid}").start()
        return dict(job)

    @staticmethod
    def _say(job: dict, text: str) -> None:
        job["log"] = (job["log"] + text)[-200_000:]

    def _run(self, job: dict, folder: str, steps: list[dict], done, device: str = "") -> None:
        from . import gputools
        ok, err = True, ""
        # the configured ROCm / CUDA (Settings -> GPU tools) in front, so a build finds that compiler and those libraries
        env = {**gputools.env(device=device), "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
        for i, step in enumerate(steps):
            job["step"] = i + 1
            try:
                if "needs" in step:
                    missing = missing_tools(step["needs"])
                    self._say(job, "$ check tools: " + ", ".join(step["needs"]) + (f" (missing: {', '.join(missing)})" if missing else " (all here)") + "\n")
                    apt = [APT[n] for n in missing if n in APT]
                    if apt:
                        if not shutil.which("apt-get") or (hasattr(os, "geteuid") and os.geteuid() != 0):
                            raise RuntimeError(f"{', '.join(n for n in missing if n in APT)} not installed, and this machine cannot "
                                               "apt-get them: install them in the Argos image")
                        step = {"run": "apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends "
                                       + " ".join(sorted(set(apt)))}
                    gpu = [n for n in missing if n not in APT]
                    if gpu:
                        what = "ROCm compiler (hipconfig, hipcc, ROCm's clang)" if "hipconfig" in gpu else "CUDA compiler (nvcc)"
                        raise RuntimeError(f"no {what} here. Building for this GPU needs the toolkit's SDK, not only its runtime: set "
                                           "Settings -> GPU tools to an SDK mounted into Argos, or base the Argos image on an SDK image")
                    if "run" not in step:
                        step = {}
                if "wheels_index" in step:
                    url = step["wheels_index"]
                    self._say(job, f"$ read {url}\n")
                    with urllib.request.urlopen(_url(url), timeout=60) as r:  # noqa: S310 - the address the user gave
                        names = [n.strip() for n in r.read().decode("utf-8", "replace").splitlines() if n.strip().endswith(".whl")]
                    if not names:
                        raise RuntimeError("the index lists no .whl files")
                    base = url.rsplit("/", 1)[0]
                    wheels = " ".join(f'"{_url(base + "/" + n)}"' for n in names)
                    self._say(job, f"  {len(names)} wheels: {', '.join(names)}\n")
                    step = {"run": f"uv pip install --python .venv {wheels}"}
                if "github_asset" in step:
                    api = f"https://api.github.com/repos/{step['github_asset']}/releases/latest"
                    with urllib.request.urlopen(urllib.request.Request(api, headers={"User-Agent": "argos"}), timeout=60) as r:  # noqa: S310
                        assets = json.loads(r.read().decode("utf-8")).get("assets") or []
                    pick = next((a for a in assets if re.search(step["match"], a.get("name", ""))), None)
                    if not pick:
                        raise RuntimeError(f"the latest release has no file matching {step['match']}")
                    self._say(job, f"  latest release file: {pick['name']} ({pick.get('size', 0) / 2 ** 20:.0f} MiB)\n")
                    step = {"download": pick["browser_download_url"], "unzip": step["unzip"]}
                if "cuda_runtime" in step:
                    dest = step["cuda_runtime"]
                    need = ("cudart64_12.dll", "cublas64_12.dll", "cublasLt64_12.dll")
                    have = [n for n in need if os.path.exists(os.path.join(dest, n))]
                    if len(have) < len(need):
                        root = os.path.dirname(folder)
                        found = {}
                        for dirpath, _, files in os.walk(root):
                            for n in need:
                                if n in files and n not in found:
                                    found[n] = os.path.join(dirpath, n)
                        missing = [n for n in need if n not in found and n not in have]
                        if missing:
                            raise RuntimeError("the CUDA 12 runtime (" + ", ".join(missing) + ") is not in another runner here; "
                                               "install whisper.cpp for CUDA first, or copy those DLLs next to sd-server.exe")
                        for n, src in found.items():
                            if n not in have:
                                shutil.copy2(src, os.path.join(dest, n))
                                self._say(job, f"  copied {n} from {os.path.relpath(src, root)}\n")
                    step = {}
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
                if "run" in step:
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

    def found(self, r: dict) -> dict:
        """What is installed for this runner. A program you point at (one you built yourself) is used as it is."""
        own = str((r.get("opts") or {}).get("program") or "").strip()
        if own and r["kind"] in ("whisper", "sdcpp"):
            if os.path.isfile(own):
                return {"installed": True, "program": own, "detail": "your own build", "build": r.get("device", "cpu")}
            return {"installed": False, "program": "", "detail": f"not found: {own}"}
        return detect(r["kind"], self.folder(r))

    def view(self) -> dict:
        """Every runner and add-on with what is installed and which models it serves."""
        out = []
        models = self.served()
        for r in self.records().values():
            d = self.found(r) if r["kind"] != "custom" else {
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
            if auto == "sdcpp" and m.get("format") not in ("gguf", "safetensors", "ckpt"):
                auto = None  # sd-server loads single files; a diffusers folder is ComfyUI's
            runner = overrides.get(m["id"], auto) or ""
            if runner == "kokoro":
                runner = ""  # Kokoro serves its own model, not files from the folder
            if runner and runner in rec and rec[runner].get("enabled", True):
                out.append({"id": m["id"], "name": m["name"], "runner": runner, "task": m.get("task"), "path": m["path"],
                            "size": m.get("size") or 0, "auto": runner == auto})
        for r in rec.values():  # a custom runner with a model-file pattern serves one model per matching file
            o = r.get("opts") or {}
            if r["kind"] != "custom" or not o.get("models_glob") or "${MODEL_PATH}" not in (o.get("start") or "") or not r.get("enabled", True):
                continue
            import glob
            for base in {self.models_dir(), self.folder(r)}:
                if not base or not os.path.isdir(base):
                    continue
                for path in sorted(glob.glob(os.path.join(base, o["models_glob"]), recursive=True)):
                    stem = os.path.splitext(os.path.basename(path))[0]
                    mid = f"{r['id']}-" + re.sub(r"[^a-z0-9._-]+", "-", stem.lower()).strip("-")
                    if mid not in seen and os.path.isfile(path):
                        seen.add(mid)
                        out.append({"id": mid, "name": stem, "runner": r["id"], "task": o.get("task", ""), "path": path,
                                    "size": os.path.getsize(path), "auto": True})
        k = rec.get("kokoro")
        if k and k.get("enabled", True) and detect("kokoro", self.folder(k))["installed"]:
            out.append({"id": "kokoro", "name": "Kokoro voices", "runner": "kokoro", "task": "text-to-speech", "path": "", "size": 0, "auto": True})
        for r in rec.values():
            o = r.get("opts") or {}
            if r["kind"] == "custom" and o.get("start") and r.get("enabled", True) and "${MODEL_PATH}" not in o["start"]:
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
                d = self.found(r)
                if not d["installed"]:
                    continue
                args = [d["program"], "--host", "127.0.0.1", "--port", "${PORT}", "-m", m["path"],
                        "--inference-path", "/v1/audio/transcriptions"] + (["-ng"] if device == "cpu" else [])
                extra = str((r.get("opts") or {}).get("args") or "").strip()
                e = {"cmd": _join(args) + (f" {extra}" if extra else ""), "cwd": os.path.dirname(d["program"]), "checkEndpoint": "/",
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
            elif r["kind"] == "sdcpp":
                d = self.found(r)
                if not d["installed"]:
                    continue
                extra = ((r.get("opts") or {}).get("model_args") or {}).get(m["id"], "")
                e = {"cmd": _join([d["program"], "--listen-ip", "127.0.0.1", "--listen-port", "${PORT}", "-m", m["path"]]) + (f" {extra}" if extra else ""),
                     "cwd": os.path.dirname(d["program"]), "checkEndpoint": "/",
                     "vram": 0.1 if device == "cpu" else round(m["size"] / 2 ** 30 * 1.25 + 1.0, 2),
                     "capabilities": {"in": ["text"], "out": ["image"]}}
                if m["id"] == (r.get("opts") or {}).get("default_model") or not any(
                        x.get("aliases") for x in out.values() if (x.get("metadata") or {}).get("argos", {}).get("runner") == "sdcpp"):
                    e["aliases"] = ["dall-e-3"]
            elif r["kind"] == "custom":
                o = r.get("opts") or {}
                if not o.get("start"):
                    continue
                cmd = o["start"].replace("${MODEL_PATH}", _join([m["path"]]) if m.get("path") else "")
                e = {"cmd": cmd, "cwd": folder, "checkEndpoint": o.get("check") or "/",
                     "vram": float(o.get("vram") or 0.1) if not m.get("size") or device == "cpu" else max(float(o.get("vram") or 0), round(m["size"] / 2 ** 30 * 1.3 + 0.3, 2))}
                if o.get("alias"):
                    e["aliases"] = [a.strip() for a in str(o["alias"]).split(",") if a.strip()]
            else:
                continue
            from . import gputools
            tool_env = gputools.run_env(device)
            if tool_env:  # a custom ROCm / CUDA: the server finds its libraries there
                e["env"] = [*e.get("env", []), *tool_env]
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
