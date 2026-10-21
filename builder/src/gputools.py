"""Where the GPU tools and toolkits are: Settings -> GPU tools.

Everything here falls back to what is on PATH, which is all most setups need. A machine with a custom
ROCm (a TheRock build for gfx906, say) or a second CUDA sets its folder once and every runner build and
every runner that starts uses it: its bin on PATH, its libraries on the loader path, ROCM_PATH / HIP_PATH
or CUDA_PATH set. The folder has to be visible inside the container, so a ROCm on the host is mounted
into Argos (a volume in docker-compose) and the mounted path goes here.
"""
from __future__ import annotations

import os
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
        prepend("PATH", os.path.join(rocm, "bin"), os.path.join(rocm, "llvm", "bin"))
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
                      "hipconfig": shutil.which("hipconfig", path=env().get("PATH")) or "",
                      "nvcc": shutil.which("nvcc", path=env().get("PATH")) or "",
                      "cmake": shutil.which("cmake") or "", "git": shutil.which("git") or "",
                      "c++": shutil.which("c++") or shutil.which("g++") or shutil.which("clang++") or ""}}
