"""Settings applied to every command: env vars and flags that every model needs.

Kept apart from the profiles, so changing one of them changes every launch and every
export at once. A profile's own value always wins: an always-set flag or env var is
only added when the command does not set it already.

Env presets are the same idea for some models only: a named set of env vars and flags
("Tensor Environment") that a profile links to with ``ARGOS_PRESETS=<id>[,<id>]`` in its
environment. The link is replaced by the preset's values whenever the command runs, so
changing a preset changes every model that uses it. The profile's own values win over a
preset's, and a preset's over Always set.
"""
from __future__ import annotations

import copy
import os

from .command import LaunchCommand, parse_command


PRESET_VAR = "ARGOS_PRESETS"
_presets: dict[str, dict] = {}  # id -> {"name", "env", "flags"}: set from the database at start and on every change


def set_presets(presets: dict) -> None:
    global _presets
    _presets = {str(k): v for k, v in (presets or {}).items() if isinstance(v, dict)}


def presets() -> dict[str, dict]:
    return dict(_presets)


PRESETS_KEY = "_env_presets"
# offered once, on a database that has never had presets: the MI50 tensor-split set
STARTER_PRESETS = {"tensor-environment": {
    "name": "Tensor Environment", "flags": "",
    "env": "HIP_FORCE_DEV_KERNARG=1\nHSA_ENABLE_SDMA=0\nHSA_FORCE_FINE_GRAIN_PCIE=1\nGGML_CUDA_ALLREDUCE=internal"}}


def load_presets(store) -> dict:
    """The saved presets into use; a database that never had any starts with STARTER_PRESETS."""
    saved = store.get_settings()
    if PRESETS_KEY not in saved:
        store.set_setting(PRESETS_KEY, STARTER_PRESETS)
        saved = {PRESETS_KEY: STARTER_PRESETS}
    set_presets(saved.get(PRESETS_KEY) or {})
    return presets()


def save_presets(store, value: dict) -> None:
    store.set_setting(PRESETS_KEY, value)
    set_presets(value)


def linked_presets(cmd: LaunchCommand) -> list[str]:
    """The preset ids a command links to, in order (unknown ones included: the caller says they are gone)."""
    return [p.strip() for p in str(cmd.env.get(PRESET_VAR) or "").split(",") if p.strip()]


def expand_presets(cmd: LaunchCommand) -> tuple[list[str], list[str]]:
    """Replaces the link in ``cmd`` with its presets' env vars and flags (only those the command does not set
    itself). Returns (what was added, the presets used)."""
    ids = linked_presets(cmd)
    cmd.env.pop(PRESET_VAR, None)
    added, used = [], []
    for pid in ids:
        p = _presets.get(pid)
        if not p:
            continue
        used.append(p.get("name") or pid)
        env, args = parse_always(p)
        for k, v in env.items():
            if k not in cmd.env:
                cmd.env[k] = v
                added.append(f"{k}={v}")
        for a in args:
            if a.name and not cmd.has(a.name):
                cmd.args.append(copy.copy(a))
                added.append(" ".join(a.tokens()))
    return added, used


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


# ----- combinations that need a companion flag ------------------------------------------------------
UNQUANTISED = {"f16", "bf16", "f32"}


def _mtp_draft(cmd: LaunchCommand) -> bool:
    """Speculative decoding with an MTP head: --spec-type draft-mtp, or a draft file named like one."""
    return "mtp" in (cmd.get("spec-type") or "").lower() or "mtp" in os.path.basename(cmd.get("model-draft") or "").lower()


def _first_device(cmd: LaunchCommand) -> str:
    """The device the draft goes on: the first of the command's own --device list, else card 0 of its runtime."""
    listed = [d for d in (cmd.get("device") or "").replace(" ", "").split(",") if d and d.lower() != "none"]
    if listed:
        return listed[0]
    from .gpu_pick import backends_for
    return "CUDA0" if backends_for(cmd)[:1] == ("cuda",) else "ROCm0"


def combo_fixes(cmd: LaunchCommand) -> list[str]:
    """Companion flags a combination needs, added in place (only those the command does not set itself).

    --split-mode tensor with an MTP draft: by default the draft is split across the cards like the main model,
    and llama.cpp's flash attention has no kernel for some of the shapes that gives (Gemma 4's 512-wide heads
    with one KV head per card, a quantised cache, the draft's 2-4 token batches), so the server aborts in the
    middle of a request (fattn.cu "fatal error" from draft_mtp). So the draft goes whole on one card
    (--device-draft) and, when the main cache is quantised, keeps an f16 cache of its own: a fraction of a GiB."""
    if (cmd.get("split-mode") or "").strip().lower() != "tensor" or not _mtp_draft(cmd):
        return []
    added = []
    if not cmd.has("device-draft"):
        dev = _first_device(cmd)
        cmd.set("device-draft", dev)
        added.append(f"--device-draft {dev}")
    k = (cmd.get("cache-type-k") or "f16").strip().lower()
    v = (cmd.get("cache-type-v") or cmd.get("cache-type-k") or "f16").strip().lower()
    if (k not in UNQUANTISED or v not in UNQUANTISED) and not cmd.has("cache-type-k-draft") and not cmd.has("cache-type-v-draft"):
        cmd.set("cache-type-k-draft", "f16")
        cmd.set("cache-type-v-draft", "f16")
        added += ["--cache-type-k-draft f16", "--cache-type-v-draft f16"]
    return added


def apply_always(command: str, always: dict) -> tuple[str, list[str]]:
    """The command with its linked env presets and the always-set env vars and flags it lacks, and what was
    added. ``always`` empty (Always set is off here) still expands the presets: they are the profile's own.
    Companion flags a combination needs (``combo_fixes``) are added either way."""
    env, args = parse_always(always or {})
    try:
        cmd: LaunchCommand = parse_command(command)
    except ValueError:
        return command, []
    fixes = combo_fixes(cmd)
    if not env and not args and PRESET_VAR not in command:
        return (cmd.to_string(), fixes) if fixes else (command, [])
    linked = PRESET_VAR in cmd.env
    added, _used = expand_presets(cmd)
    for k, v in env.items():
        if k not in cmd.env:
            cmd.env[k] = v
            added.append(f"{k}={v}")
    for a in args:
        if a.name and not cmd.has(a.name):
            cmd.args.append(copy.copy(a))
            added.append(" ".join(a.tokens()))
    added += fixes
    return (cmd.to_string() if added or linked else command), added


__all__ = ["PRESETS_KEY", "PRESET_VAR", "load_presets", "save_presets", "apply_always", "combo_fixes", "expand_presets", "linked_presets", "parse_always", "presets", "set_presets"]
