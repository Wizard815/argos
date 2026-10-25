"""Settings applied to every command: env vars and flags that every model needs.

Kept apart from the profiles, so changing one of them changes every launch and every
export at once. A profile's own value always wins: an always-set flag or env var is
only added when the command does not set it already.
"""
from __future__ import annotations

import copy

from .command import LaunchCommand, parse_command

# A command that names any of these has picked its own cards, and the GPU setting leaves it alone.
GPU_SELECT = ("HIP_VISIBLE_DEVICES", "ROCR_VISIBLE_DEVICES", "CUDA_VISIBLE_DEVICES", "GPU_DEVICE_ORDINAL",
              "GGML_VK_VISIBLE_DEVICES")
GPU_VARS = ("HIP_VISIBLE_DEVICES", "ROCR_VISIBLE_DEVICES", "CUDA_VISIBLE_DEVICES")


def always_for(settings) -> dict:
    """The "always" group with the GPU setting folded in, the way apply_always wants it."""
    return {**settings.get("always"), "gpu": settings.get("gpu")}


def gpu_env(gpu: dict) -> dict[str, str]:
    """The env vars that pin a model to the cards chosen in Settings -> GPUs; empty when none are chosen."""
    cards = [c.strip() for c in str((gpu or {}).get("visible") or "").replace(";", ",").split(",") if c.strip()]
    if not cards:
        return {}
    var = str((gpu or {}).get("visible_var") or "auto")
    names = (var,) if var in GPU_VARS else ("HIP_VISIBLE_DEVICES", "CUDA_VISIBLE_DEVICES")
    return {n: ",".join(cards) for n in names}


def parse_always(always: dict) -> tuple[dict[str, str], list]:
    """(env, flag args) from the Settings group. Bad lines are skipped, never fatal."""
    env: dict[str, str] = {}
    for line in str(always.get("env") or "").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            if k.strip():
                env[k.strip()] = v.strip().strip('"').strip("'")
    args = []
    flags = str(always.get("flags") or "").strip()
    if flags:
        try:
            args = parse_command("llama-server " + flags).args
        except ValueError:
            args = []
    return env, args


def apply_always(command: str, always: dict) -> tuple[str, list[str]]:
    """The command with the always-set env vars and flags it lacks, and what was added."""
    env, args = parse_always(always or {})
    if not env and not args and not gpu_env((always or {}).get("gpu") or {}):
        return command, []
    try:
        cmd: LaunchCommand = parse_command(command)
    except ValueError:
        return command, []
    added: list[str] = []
    for k, v in env.items():
        if k not in cmd.env:
            cmd.env[k] = v
            added.append(f"{k}={v}")
    if not any(k in cmd.env for k in GPU_SELECT):
        for k, v in gpu_env((always or {}).get("gpu") or {}).items():
            cmd.env[k] = v
            added.append(f"{k}={v}")
    for a in args:
        if a.name and not cmd.has(a.name):
            cmd.args.append(copy.copy(a))
            added.append(" ".join(a.tokens()))
    return (cmd.to_string() if added else command), added


__all__ = ["apply_always", "parse_always", "always_for", "gpu_env", "GPU_SELECT"]
