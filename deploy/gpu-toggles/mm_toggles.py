#!/usr/bin/env python3
"""mm_toggles.py — per-model mode state for the detail-view switches.

Mirrors the odysseus choices file: one JSON file, per-model record, atomic
write, survives restarts. The llama-swap UI writes it through an API endpoint;
the launch path / proxy reads it.

A model is in exactly ONE mode:

  standard     — the opt-out. Use the effective launch exactly as written;
                 neither the single-GPU pick nor the multi-model scale applies.
                 This is the switch for a model that is not meant to use
                 either toggle.
  single_gpu   — dynamic GPU binding.
  multi_model  — instance scale-out.

Legacy records written as {"single_gpu": bool, "multi_model": bool} are still
read (see mm_engine.resolve_mode); new writes use {"mode": ...}.
"""
from __future__ import annotations

import json
import os
import tempfile

DEFAULT_PATH = "/app/config.d/model_toggles.json"
MODES = ("standard", "single_gpu", "multi_model")


def load(path: str = DEFAULT_PATH) -> dict[str, dict]:
    try:
        with open(path) as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            return {k: dict(v) for k, v in data.items() if isinstance(v, dict)}
    except (OSError, ValueError):
        pass
    return {}


def save(path: str, state: dict[str, dict]) -> None:
    d = os.path.dirname(path) or "."
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".toggles-")
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(state, fh, indent=2, sort_keys=True)
            fh.write("\n")
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _mode_of(entry: dict) -> str:
    mode = entry.get("mode")
    if mode in MODES:
        return mode
    # legacy booleans
    if entry.get("multi_model"):
        return "multi_model"
    if entry.get("single_gpu"):
        return "single_gpu"
    return "standard"


def get(state: dict[str, dict], model: str) -> dict:
    """Return the model's effective record: mode plus derived booleans."""
    mode = _mode_of(state.get(model, {}))
    return {
        "mode": mode,
        "single_gpu": mode == "single_gpu",
        "multi_model": mode == "multi_model",
    }


def set_mode(path: str, model: str, mode: str) -> dict:
    """Set the model's mode. `standard` is the opt-out (both toggles off)."""
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}, got {mode!r}")
    state = load(path)
    state[model] = {"mode": mode}
    save(path, state)
    return get(state, model)
