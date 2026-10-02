#!/usr/bin/env python3
"""mm_toggles.py — per-model toggle state for the detail-view buttons.

Mirrors the odysseus choices file: one JSON file, per-model flags, atomic write,
survives restarts. The llama-swap UI writes it through an API endpoint; the
launch path / proxy reads it.
"""
from __future__ import annotations

import json
import os
import tempfile

DEFAULT_PATH = "/app/config.d/model_toggles.json"


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


def get(state: dict[str, dict], model: str) -> dict:
    return {"single_gpu": False, "multi_model": False, **state.get(model, {})}


def set_flag(path: str, model: str, single_gpu: bool | None = None,
             multi_model: bool | None = None) -> dict:
    state = load(path)
    cur = get(state, model)
    if single_gpu is not None:
        cur["single_gpu"] = bool(single_gpu)
    if multi_model is not None:
        cur["multi_model"] = bool(multi_model)
    state[model] = cur
    save(path, state)
    return cur
