"""Read Odysseus' cookbook state to obtain per-model launch commands.

Odysseus saves its cookbook in ``cookbook_state.json`` with this shape::

    {
      "tasks": [
        {"name": "...", "sessionId": "cookbook-...", "payload": {
            "_cmd": "llama-server --model /models/... --port 8080 ...",
            "repo_id": "...", "port": 8080}}
      ],
      "presets": [
        {"name": "fast", "label": "fast", "model": "unsloth/Qwen...",
         "cmd": "llama-server --model /models/... --n-gpu-layers 35 ..."},
        ...
      ]
    }

Both live tasks ("_cmd") and saved presets are treated as launch recipes: each one
becomes a profile we can copy, test, and record performance for.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Optional

from .command_parser import LaunchCommand, parse_launch_command


@dataclass
class CookbookModel:
    model_id: str
    label: str
    cmd: str
    source: str = "task"          # 'task' or 'preset'
    repo_id: str = ""
    session_id: str = ""

    @property
    def command(self) -> LaunchCommand:
        return parse_launch_command(self.cmd)


@dataclass
class CookbookState:
    models: list[CookbookModel] = field(default_factory=list)

    @property
    def unique_base_models(self) -> list[str]:
        out, seen = [], set()
        for m in self.models:
            base = (m.repo_id or "").split("/")[-1] or m.model_id
            if base not in seen:
                seen.add(base)
                out.append(base)
        return out

    def commands_by_base(self, base_model: str) -> list[CookbookModel]:
        return [m for m in self.models if (m.repo_id or "").endswith(base_model)]


def load_cookbook_state(path_or_url):
    """Load cookbook state from a local JSON path or URL.

    Recommended route is the mounted file ``cookbook_state.json`` because Odysseus
    gates its API behind admin + loopback middleware a container cannot satisfy.
    """
    import urllib.request

    data = None
    src = "url" if isinstance(path_or_url, str) and ("http://" in path_or_url or "https://" in path_or_url) else "file"
    try:
        if src == "url":
            with urllib.request.urlopen(path_or_url, timeout=5) as resp:  # noqa: S310
                data = json.loads(resp.read().decode("utf-8"))
        else:
            p = path_or_url
            if not os.path.exists(p):
                return CookbookState(models=[])
            with open(p, encoding="utf-8", errors="ignore") as f:
                raw = f.read()
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                data = {}
    except Exception:
        data = {}
    return _build_state(data)


def _build_state(data: dict) -> CookbookState:
    state = CookbookState()
    tasks = data.get("tasks") or []
    for t in tasks:
        payload = t.get("payload") or {}
        cmd = payload.get("_cmd")
        if not cmd or "--model" not in cmd:
            continue
        state.models.append(CookbookModel(
            model_id=t.get("id") or t.get("sessionId") or payload.get("repo_id") or cmd[:40],
            label=t.get("name") or payload.get("repo_id") or "task",
            cmd=cmd,
            source="task",
            repo_id=payload.get("repo_id") or "",
            session_id=t.get("sessionId") or "",
        ))
    presets = data.get("presets") or []
    for p in presets:
        cmd = p.get("cmd") or p.get("_cmd")
        if not cmd:
            continue
        state.models.append(CookbookModel(
            model_id=p.get("name") or p.get("label") or cmd[:40],
            label=p.get("label") or p.get("name") or "preset",
            cmd=cmd,
            source="preset",
            repo_id=p.get("model") or "",
        ))
    return state


__all__ = ["CookbookState", "CookbookModel", "load_cookbook_state"]
