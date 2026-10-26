"""Which GPUs a model may use: picked as card numbers in the Builder, written as the runtime's own variable.

The Builder picks cards; this decides which variable they become, so the command shows
what really runs (``HIP_VISIBLE_DEVICES=0,1`` on AMD, ``CUDA_VISIBLE_DEVICES=0`` on NVIDIA):

- a remote launcher names its backend (``/app/bin/remote-llama rocm``): that one
- the picked cards' own names, from the GPU telemetry: AMD / Radeon / Instinct is HIP,
  NVIDIA / GeForce / RTX is CUDA
- else what this machine has: ROCm (``/dev/kfd`` or ``rocm-smi``), NVIDIA
  (``/dev/nvidiactl`` or ``nvidia-smi``), any card name the telemetry saw, and with
  nothing to go on both (each runtime ignores the other's variable)

Profiles saved while the pick was stored as ``ARGOS_GPUS=0,1`` are turned into the
real variable when the Builder opens them and when they launch; ``ARGOS_GPUS`` never
reaches the process.
"""
from __future__ import annotations

import os
import re
import shutil
from typing import Iterable, Optional

from .command import LaunchCommand, parse_command

PICK_VAR = "ARGOS_GPUS"  # the old way of saving the pick; still read
# any of these on a command means it already picked its cards one way or another
SELECTORS = ("HIP_VISIBLE_DEVICES", "ROCR_VISIBLE_DEVICES", "CUDA_VISIBLE_DEVICES", "GPU_DEVICE_ORDINAL",
             "GGML_VK_VISIBLE_DEVICES")
VARS_FOR = {"rocm": "HIP_VISIBLE_DEVICES", "cuda": "CUDA_VISIBLE_DEVICES", "vulkan": "GGML_VK_VISIBLE_DEVICES"}

AMD = re.compile(r"(?i)\b(amd|radeon|instinct|gfx\d)")
NVIDIA = re.compile(r"(?i)\b(nvidia|geforce|rtx|quadro|tesla)\b")

_local: Optional[tuple[str, ...]] = None
_cards: dict[str, str] = {}  # card number -> name, from the GPU telemetry


def note_cards(rows: Iterable) -> None:
    """The cards the GPU telemetry saw: rows with ``gpu`` and ``name``."""
    global _cards, _local
    seen = {str(r.get("gpu")): str(r.get("name") or "") for r in rows or () if isinstance(r, dict) and r.get("gpu") is not None}
    if seen and seen != _cards:
        _cards, _local = seen, None


def _vendor(name: str) -> Optional[str]:
    if AMD.search(name):
        return "rocm"
    if NVIDIA.search(name):
        return "cuda"
    return None


def local_backends() -> tuple[str, ...]:
    """The GPU runtimes this machine has: ("rocm",), ("cuda",), both, or both when nothing says."""
    global _local
    if _local is None:
        found = []
        if os.path.exists("/dev/kfd") or shutil.which("rocm-smi"):
            found.append("rocm")
        if os.path.exists("/dev/nvidiactl") or shutil.which("nvidia-smi"):
            found.append("cuda")
        if not found:
            # llama.cpp in another container: no device files here, but the telemetry knows the cards
            found = [b for b in ("rocm", "cuda") if any(_vendor(n) == b for n in _cards.values())]
        _local = tuple(found) or ("rocm", "cuda")
    return _local


def backends_for(cmd: LaunchCommand, cards: Optional[list[str]] = None) -> tuple[str, ...]:
    """Which runtime reads the pick: the remote backend, else the picked cards' makers, else this machine's."""
    if os.path.basename(cmd.program) == "remote-llama" and cmd.args and not cmd.args[0].flag:
        word = (cmd.args[0].value or "").strip().lower()
        if word in VARS_FOR:
            return (word,)
    makers = [_vendor(_cards.get(c, "")) for c in cards or ()]
    if makers and all(makers):
        return tuple(b for b in ("rocm", "cuda") if b in makers)
    return local_backends()


def vars_for(cmd: LaunchCommand, cards: Optional[list[str]] = None) -> list[str]:
    """The variables a pick of ``cards`` becomes for this command."""
    return [VARS_FOR[b] for b in backends_for(cmd, cards)]


def picked(cmd: LaunchCommand) -> list[str]:
    """The cards this command is pinned to, however it says so."""
    for var in (PICK_VAR, *SELECTORS):
        if var in cmd.env:
            return [c for c in cmd.env[var].replace(" ", "").split(",") if c]
    return []


def apply_pick(cmd: LaunchCommand, cards: Optional[list]) -> LaunchCommand:
    """Pin ``cmd`` to ``cards`` with the right variable, replacing any pin it had. Empty = no pin."""
    ids = [str(c).strip() for c in cards or () if str(c).strip()]
    for var in (PICK_VAR, *SELECTORS):
        cmd.env.pop(var, None)
    for var in vars_for(cmd, ids) if ids else ():
        cmd.env[var] = ",".join(ids)
    return cmd


def resolve(cmd: LaunchCommand) -> LaunchCommand:
    """Turn an old ``ARGOS_GPUS`` into the runtime's own variable, in place. A variable set by hand wins."""
    pick = (cmd.env.pop(PICK_VAR, None) or "").replace(" ", "").strip(",")
    if pick and not any(v in cmd.env for v in SELECTORS):
        for var in vars_for(cmd, pick.split(",")):
            cmd.env[var] = pick
    return cmd


def resolve_command(command: str) -> str:
    """``resolve`` for a command string; anything unparseable is returned as it was."""
    if PICK_VAR not in command:
        return command
    try:
        return resolve(parse_command(command)).to_string()
    except ValueError:
        return command


__all__ = ["PICK_VAR", "SELECTORS", "VARS_FOR", "apply_pick", "backends_for", "local_backends", "note_cards", "picked",
           "resolve", "resolve_command", "vars_for"]
