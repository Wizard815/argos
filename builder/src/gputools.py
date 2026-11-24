"""Where the GPU tools and toolkits are: Settings -> GPU tools.

Everything here falls back to what is on PATH, which is all most setups need. A machine with a custom
ROCm (a TheRock build for gfx906, say) or a second CUDA sets its folder once and every runner build and
every runner that starts uses it: its bin on PATH, its libraries on the loader path, ROCM_PATH / HIP_PATH
or CUDA_PATH set. The folder has to be visible inside the container, so a ROCm on the host is mounted
into Argos (a volume in docker-compose) and the mounted path goes here.
"""
from __future__ import annotations

import glob
import os
import re
import shutil
import sys

_cfg: dict[str, str] = {"rocm_path": "", "cuda_path": "", "rocm_smi": "", "nvidia_smi": ""}


def configure(values: dict) -> None:
    for k in _cfg:
        _cfg[k] = str((values or {}).get(k) or "").strip()


def tool(name: str) -> str:
    """The configured path for nvidia-smi / rocm-smi, else the one on PATH (with the toolkit's bin first), else ""."""
    key = {"nvidia-smi": "nvidia_smi", "rocm-smi": "rocm_smi"}.get(name)
    if key and _cfg[key] and os.path.exists(_cfg[key]):
        return _cfg[key]
    return shutil.which(name, path=env().get("PATH")) or ""


def env(base: dict | None = None, device: str = "") -> dict:
    """os.environ (or base) with the configured ROCm / CUDA in front. device limits it to that one toolkit."""
    out = dict(os.environ if base is None else base)
    sep = ";" if sys.platform == "win32" else ":"

    def prepend(var: str, *paths: str) -> None:
        have = [p for p in paths if p and os.path.isdir(p)]
        if have:
            out[var] = sep.join(have + ([out[var]] if out.get(var) else []))

    rocm, cuda = _cfg["rocm_path"], _cfg["cuda_path"]
    if rocm and device in ("", "rocm"):
        out["ROCM_PATH"] = out["HIP_PATH"] = rocm
        prepend("PATH", os.path.join(rocm, "bin"), os.path.join(rocm, "llvm", "bin"), os.path.join(rocm, "lib", "llvm", "bin"))
        prepend("LD_LIBRARY_PATH", os.path.join(rocm, "lib"), os.path.join(rocm, "lib64"))
    if cuda and device in ("", "cuda"):
        out["CUDA_PATH"] = out["CUDA_HOME"] = cuda
        prepend("PATH", os.path.join(cuda, "bin"))
        prepend("LD_LIBRARY_PATH", os.path.join(cuda, "lib64"), os.path.join(cuda, "lib"))
    return out


def run_env(device: str) -> list[str]:
    """The KEY=VALUE lines a runner's engine entry needs so it finds the configured toolkit's libraries."""
    if device not in ("rocm", "cuda") or not _cfg["rocm_path" if device == "rocm" else "cuda_path"]:
        return []
    # not PATH: the engine adds these to the container's own environment, and a PATH here would replace that
    e = env(None, device)
    return [f"{k}={e[k]}" for k in ("ROCM_PATH", "HIP_PATH", "CUDA_PATH", "CUDA_HOME", "LD_LIBRARY_PATH") if e.get(k)]


def status() -> dict:
    """What the GPU tools section shows: each path, whether it exists, and what would be used."""
    def where(p: str) -> dict:
        return {"path": p, "exists": bool(p) and os.path.exists(p)}
    return {**{k: where(v) for k, v in _cfg.items()},
            "using": {"rocm-smi": tool("rocm-smi"), "nvidia-smi": tool("nvidia-smi"),
                      "hipconfig": rocm_compiler(),
                      "nvcc": shutil.which("nvcc", path=env().get("PATH")) or "",
                      "cmake": shutil.which("cmake") or "", "git": shutil.which("git") or "",
                      "c++": shutil.which("c++") or shutil.which("g++") or shutil.which("clang++") or ""}}


# ----- finding a ROCm, and what it can do -----------------------------------------------------------------------
# A ROCm is a folder with bin/ and lib/. Where its compiler is depends on how it was made: AMD's packages put clang in
# llvm/bin, TheRock builds (the gfx906 ones) in lib/llvm/bin. A runtime-only ROCm (what an image that only runs
# llama.cpp needs) has no compiler at all: it can run a build, not make one.
ROCM_GLOBS = ("/opt/rocm*", "/opt/*rocm*", "/opt/therock*", "/usr/local/rocm*", "/app/rocm*", "/data/rocm*",
              "/mnt/*/rocm*", "/host/opt/rocm*")


def rocm_clang(root: str) -> str:
    """ROCm's own clang under root, "" when it has none."""
    for rel in ("llvm/bin/clang", "lib/llvm/bin/clang", "bin/amdclang"):
        p = os.path.join(root, rel)
        if os.path.isfile(p) and os.access(p, os.X_OK):
            return p
    return ""


def rocm_compiler() -> str:
    """The ROCm compiler a build would use: hipconfig / hipcc on PATH (with the configured ROCm first), else the
    configured ROCm's clang. "" when there is none."""
    path = env().get("PATH")
    for name in ("hipconfig", "hipcc"):
        found = shutil.which(name, path=path)
        if found:
            return found
    return rocm_clang(_cfg["rocm_path"]) if _cfg["rocm_path"] else ""


def describe_rocm(root: str) -> dict:
    """What one ROCm folder has: its version, a compiler, rocm-smi / amd-smi, and the GPU architectures its math
    libraries were built for (rocBLAS's Tensile files name them)."""
    def has(rel: str) -> str:
        p = os.path.join(root, rel)
        return p if os.path.exists(p) else ""
    version = ""
    for rel in (".info/version", ".info/version-dev", "share/doc/rocm/version"):
        try:
            version = open(os.path.join(root, rel), encoding="utf-8").read().strip().splitlines()[0]
            break
        except (OSError, IndexError):
            continue
    archs = sorted({m for f in glob.glob(os.path.join(root, "lib", "rocblas", "library", "*gfx*"))
                    for m in re.findall(r"gfx\d+[a-z]?", os.path.basename(f))})
    clang = rocm_clang(root)
    hip = has("bin/hipconfig") or has("bin/hipcc")
    return {"path": root, "version": version, "compiler": clang or hip, "hipconfig": has("bin/hipconfig"),
            "hipcc": has("bin/hipcc"), "clang": clang, "rocm_smi": has("bin/rocm-smi"), "amd_smi": has("bin/amd-smi"),
            "archs": archs, "can_build": bool(clang or hip), "configured": os.path.realpath(root) == os.path.realpath(_cfg["rocm_path"] or "/nonexistent")}


def find_rocm(extra: tuple[str, ...] = ()) -> list[dict]:
    """Every ROCm folder Argos can see: the usual places, ROCM_PATH / HIP_PATH, what hipconfig reports, and the
    configured one. Each described by describe_rocm, the ones that can build first."""
    seen, out = set(), []
    candidates = [p for g in ROCM_GLOBS + tuple(extra) for p in sorted(glob.glob(g))]
    candidates += [os.environ.get("ROCM_PATH", ""), os.environ.get("HIP_PATH", ""), _cfg["rocm_path"]]
    for root in candidates:
        if not root or not os.path.isdir(root):
            continue
        real = os.path.realpath(root)
        if real in seen or not (os.path.isdir(os.path.join(root, "bin")) and os.path.isdir(os.path.join(root, "lib"))):
            continue
        seen.add(real)
        out.append(describe_rocm(root))
    return sorted(out, key=lambda r: (not r["can_build"], not r["configured"], r["path"]))
