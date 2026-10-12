"""Serve saved profiles as an Odysseus ``cookbook_state.json``.

llama-swap-rescan's Odysseus integration (internal/odysseus) reads either
``GET <baseURL>/api/cookbook/state`` or a ``stateFile`` and expects::

    {"tasks":   [{"id", "name", "modelId", "status", "ts", "payload": {"repo_id", "_cmd"}}],
     "presets": [{"name", "model", "cmd", "remoteHost", "port", "label", "fields"}]}

Each preset becomes a llama-swap variant ``<model-id>--<label>``, where the
model ID comes from the ``--model`` .gguf file name. So every exported command
is put in Odysseus' shape with a real .gguf path, and labels are made unique
per model (two presets with one label would collide on that variant ID).
Unlike the Cookbook (five saves per model), every profile is exported.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
from typing import Any, Optional

from .always import apply_always
from .command import parse_command, swap_model_id, to_odysseus
from .integrations import odysseus


def _label_key(label: str) -> str:
    # llama-swap's SanitizeProfileLabel
    key = re.sub(r"[^a-zA-Z0-9._-]+", "-", label.strip().lower()).strip("-")
    return key[:40].strip("-")


def profile_presets(profiles: list[dict], taken: Optional[dict] = None,
                    always: Optional[dict] = None) -> tuple[list[dict], list[str]]:
    """Profiles -> Odysseus presets. ``taken`` maps model ID -> labels already used; ``always`` adds the global env and flags."""
    taken = {k: set(v) for k, v in (taken or {}).items()}
    presets, warnings = [], []
    for p in profiles:
        try:
            cmd = to_odysseus(parse_command(apply_always(p["launch_command"], always or {})[0]))
        except ValueError as exc:
            warnings.append(f"profile {p['name']!r}: {exc}")
            continue
        s = cmd.summary()
        model_path = s["model"]
        if not model_path.lower().endswith(".gguf"):
            warnings.append(f"profile {p['name']!r}: --model is not a .gguf path, llama-swap cannot map it")
            continue
        mid = swap_model_id(model_path)
        label, n = p["name"], 2
        while _label_key(label) in taken.setdefault(mid, set()) or not _label_key(label):
            label = f"{p['name']} {n}"
            n += 1
        taken[mid].add(_label_key(label))
        text = cmd.to_string(style="odysseus")
        presets.append({
            "name": p.get("base_model") or mid,
            "model": p.get("base_model") or mid,
            "cmd": text,
            "remoteHost": "",
            "port": str(s["port"] or 8080),
            "label": label,
            "favorite": bool(p.get("favorite")),
            "fields": {"_manual_cmd": text, "source": "argos", "profile_id": p["profile_id"]},
        })
    return presets, warnings


def variant_id(preset: dict) -> str:
    """The llama-swap model id a preset becomes: ``<model-id>--<label>``, or "" if it has no .gguf."""
    try:
        model = parse_command(preset.get("cmd") or "").summary()["model"]
    except ValueError:
        return ""
    if not model.lower().endswith(".gguf"):
        return ""
    label = _label_key(preset.get("label") or "")
    return f"{swap_model_id(model)}--{label}" if label else swap_model_id(model)


def launch_tasks(launches: list) -> list[dict]:
    tasks = []
    for r in launches:
        if not r.running:
            continue
        try:
            cmd = to_odysseus(parse_command(r.command))
        except ValueError:
            continue
        model = cmd.summary()["model"]
        tasks.append({
            "id": r.launch_id, "name": r.profile_name or r.base_model or swap_model_id(model),
            "modelId": swap_model_id(model), "status": "running", "ts": int(r.started_at * 1000),
            "payload": {"repo_id": r.base_model or "", "_cmd": cmd.to_string(style="odysseus")},
        })
    return tasks


def build_state(profiles: list[dict], launches: list, settings: dict, odysseus_source: str,
                always: Optional[dict] = None) -> dict:
    """The full state document. Odysseus' own presets/tasks pass through first."""
    state: dict[str, Any] = {"tasks": [], "presets": [], "warnings": []}
    taken: dict[str, set] = {}
    if settings.get("include_odysseus") and odysseus_source:
        try:
            raw = odysseus.load_state(odysseus_source)
            state["tasks"] += [t for t in raw.get("tasks") or [] if isinstance(t, dict)]
            for pr in raw.get("presets") or []:
                if not isinstance(pr, dict):
                    continue
                state["presets"].append(pr)
                try:
                    mid = swap_model_id(parse_command(pr.get("cmd") or "").summary()["model"])
                    taken.setdefault(mid, set()).add(_label_key(pr.get("label") or ""))
                except ValueError:
                    pass
        except Exception as exc:  # noqa: BLE001 - a broken source must not hide our profiles
            state["warnings"].append(f"odysseus passthrough: {exc}")
    # oldest first, so a profile keeps its label (and llama-swap its variant ID) when others are added
    presets, warnings = profile_presets(sorted(profiles, key=lambda p: p.get("created_at") or 0), taken, always)
    state["presets"] += presets
    state["warnings"] += warnings
    if settings.get("include_tasks"):
        state["tasks"] += launch_tasks(launches)
    return state


def write_state_file(path: str, state: dict) -> None:
    """Atomic write, so llama-swap never reads a half-written file."""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(os.path.abspath(path)), prefix=".cookbook-", suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump({k: v for k, v in state.items() if k != "warnings"}, f, indent=2)
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)


__all__ = ["build_state", "profile_presets", "variant_id", "launch_tasks", "write_state_file"]
