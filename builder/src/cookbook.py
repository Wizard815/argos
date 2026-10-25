"""Serve saved profiles as an Odysseus ``cookbook_state.json``.

The engine's Odysseus integration (internal/odysseus) reads ``GET <baseURL>/api/cookbook/state`` and expects::

    {"tasks":   [{"id", "name", "modelId", "status", "ts", "payload": {"repo_id", "_cmd"}}],
     "presets": [{"name", "model", "cmd", "remoteHost", "port", "label", "fields"}]}

Each preset becomes an engine variant ``<model-id>--<label>``, where the
model ID comes from the ``--model`` .gguf file name. So every exported command
is put in Odysseus' shape with a real .gguf path, and labels are made unique
per model (two presets with one label would collide on that variant ID).
Unlike the Cookbook (five saves per model), every profile is exported.
"""
from __future__ import annotations

import re
from typing import Any, Optional

from .always import apply_always
from .command import parse_command, swap_model_id, to_odysseus
from .gpu_pick import resolve as resolve_gpus


def _label_key(label: str) -> str:
    # Argos's SanitizeProfileLabel
    key = re.sub(r"[^a-zA-Z0-9._-]+", "-", label.strip().lower()).strip("-")
    return key[:40].strip("-")


def _numbered(name: str, n: int) -> str:
    """The n-th label to try for ``name`` when the plain one is taken: "name 2", "name 3", ...

    The key is cut at 40 characters, so for a long name "name 2" cuts back to the very key that was taken and
    the search never ended (every page that listed profiles spun one CPU forever). Then the number is kept and
    the name is cut instead; that label is already a key, so the engine derives the same variant id from it.
    """
    label = f"{name} {n}"
    if _label_key(label).endswith(f"-{n}"):
        return label
    suffix = f"-{n}"
    return (_label_key(name)[:40 - len(suffix)].strip("-") + suffix).lstrip("-")


def profile_presets(profiles: list[dict], taken: Optional[dict] = None,
                    always: Optional[dict] = None) -> tuple[list[dict], list[str]]:
    """Profiles -> Odysseus presets. ``taken`` maps model ID -> labels already used; ``always`` adds the global env and flags."""
    taken = {k: set(v) for k, v in (taken or {}).items()}
    presets, warnings = [], []
    for p in profiles:
        try:
            cmd = to_odysseus(resolve_gpus(parse_command(apply_always(p["launch_command"], always or {})[0])))
        except ValueError as exc:
            warnings.append(f"profile {p['name']!r}: {exc}")
            continue
        s = cmd.summary()
        model_path = s["model"]
        if not model_path.lower().endswith(".gguf"):
            warnings.append(f"profile {p['name']!r}: --model is not a .gguf path, Argos cannot map it")
            continue
        mid = swap_model_id(model_path)
        label, n = p["name"], 2
        while _label_key(label) in taken.setdefault(mid, set()) or not _label_key(label):
            label = _numbered(p["name"], n)
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
    """The Argos model id a preset becomes: ``<model-id>--<label>``, or "" if it has no .gguf."""
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


def normalize_preset(p: dict) -> dict:
    """An Odysseus preset in the shape the engine reads. Different Odysseus versions name the host and label differently."""
    out = dict(p)
    out["cmd"] = p.get("cmd") or p.get("_cmd") or ""
    out["remoteHost"] = p.get("remoteHost") or p.get("host") or ""
    out["label"] = p.get("label") or p.get("name") or ""
    out["port"] = str(p.get("port") or "")
    out.setdefault("name", out["label"])
    out.setdefault("model", p.get("modelId") or "")
    return out


def build_state(profiles: list[dict], launches: list, odysseus: Optional[dict] = None,
                always: Optional[dict] = None, include_tasks: bool = False) -> dict:
    """The full state document. Odysseus' own presets (``odysseus = {"presets": [...], "error": ""}``) pass through first."""
    state: dict[str, Any] = {"tasks": [], "presets": [], "warnings": []}
    taken: dict[str, set] = {}
    if odysseus:
        if odysseus.get("error"):
            state["warnings"].append(f"odysseus presets: {odysseus['error']}")
        for pr in odysseus.get("presets") or []:
            if not isinstance(pr, dict):
                continue
            pr = normalize_preset(pr)
            if not pr["cmd"].strip():
                continue
            state["presets"].append(pr)
            try:
                mid = swap_model_id(parse_command(pr["cmd"]).summary()["model"])
                taken.setdefault(mid, set()).add(_label_key(pr["label"]))
            except ValueError:
                pass
    # oldest first, so a profile keeps its label (and the engine its variant ID) when others are added
    presets, warnings = profile_presets(sorted(profiles, key=lambda p: p.get("created_at") or 0), taken, always)
    state["presets"] += presets
    state["warnings"] += warnings
    if include_tasks:
        state["tasks"] += launch_tasks(launches)
    return state


__all__ = ["build_state", "normalize_preset", "profile_presets", "variant_id", "launch_tasks"]
