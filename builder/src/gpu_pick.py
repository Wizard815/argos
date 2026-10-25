"""Which GPUs a model may use: picked as card numbers, turned into the right variables at launch.

The Builder stores the pick as ``ARGOS_GPUS=0,1`` on the command. Which variable the
GPU runtime reads depends on where the model runs, so it is only decided when the
command is launched or handed to the engine:

- a remote launcher names its backend (``/app/bin/remote-llama rocm``): that one
- this machine: ROCm when it is here (``/dev/kfd`` or ``rocm-smi``), NVIDIA when it
  is (``/dev/nvidiactl`` or ``nvidia-smi``), both when both are, both when neither
  is found (each runtime ignores the other's variable)

A variable the command sets itself is left alone, and ``ARGOS_GPUS`` never reaches
the process.
"""
from __future__ import annotations

import os
import shutil
from typing import Optional

from .command import LaunchCommand, parse_command

PICK_VAR = "ARGOS_GPUS"
# any of these on a command means it already picked its cards one way or another
SELECTORS = ("HIP_VISIBLE_DEVICES", "ROCR_VISIBLE_DEVICES", "CUDA_VISIBLE_DEVICES", "GPU_DEVICE_ORDINAL",
             "GGML_VK_VISIBLE_DEVICES")
VARS_FOR = {"rocm": "HIP_VISIBLE_DEVICES", "cuda": "CUDA_VISIBLE_DEVICES", "vulkan": "GGML_VK_VISIBLE_DEVICES"}

_local: Optional[tuple[str, ...]] = None


def local_backends() -> tuple[str, ...]:
    """The GPU runtimes this machine has: ("rocm",), ("cuda",), both, or both when none is found."""
    global _local
    if _local is None:
        found = []
        if os.path.exists("/dev/kfd") or shutil.which("rocm-smi"):
            found.append("rocm")
        if os.path.exists("/dev/nvidiactl") or shutil.which("nvidia-smi"):
            found.append("cuda")
        _local = tuple(found) or ("rocm", "cuda")
    return _local


def backends_for(cmd: LaunchCommand) -> tuple[str, ...]:
    """Where this command runs decides which runtime reads the pick."""
    if os.path.basename(cmd.program) == "remote-llama" and cmd.args and not cmd.args[0].flag:
        word = (cmd.args[0].value or "").strip().lower()
        if word in VARS_FOR:
            return (word,)
    return local_backends()


def vars_for(cmd: LaunchCommand) -> list[str]:
    """The variables the pick becomes for this command, for the Builder to show."""
    return [VARS_FOR[b] for b in backends_for(cmd)]


def resolve(cmd: LaunchCommand) -> LaunchCommand:
    """Turn ``ARGOS_GPUS`` into the runtime's own variables, in place. Returns ``cmd``."""
    pick = (cmd.env.pop(PICK_VAR, None) or "").replace(" ", "").strip(",")
    if pick:
        for var in vars_for(cmd):
            cmd.env.setdefault(var, pick)
    return cmd


def resolve_command(command: str) -> str:
    """``resolve`` for a command string; anything unparseable is returned as it was."""
    if PICK_VAR not in command:
        return command
    try:
        return resolve(parse_command(command)).to_string()
    except ValueError:
        return command


__all__ = ["PICK_VAR", "SELECTORS", "VARS_FOR", "backends_for", "local_backends", "resolve", "resolve_command", "vars_for"]
