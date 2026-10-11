"""Read launch commands out of Odysseus' cookbook state.

Odysseus saves its cookbook in ``cookbook_state.json``::

    {
      "tasks":   [{"name": "...", "sessionId": "cookbook-...",
                   "payload": {"_cmd": "llama-server --model ...", "repo_id": "org/repo"}}],
      "presets": [{"name": "fast", "label": "fast", "model": "org/repo",
                   "cmd": "llama-server --model ... -ngl 99"}]
    }

Both live tasks and saved presets become importable commands. Mounting the
file is the recommended route: Odysseus gates its HTTP API behind admin +
loopback middleware that a separate container cannot satisfy.
"""
from __future__ import annotations

import json
import os
import urllib.request
from typing import Any


def _read(path_or_url: str) -> Any:
    if path_or_url.startswith(("http://", "https://")):
        with urllib.request.urlopen(path_or_url, timeout=5) as resp:  # noqa: S310
            return json.loads(resp.read().decode("utf-8"))
    with open(path_or_url, encoding="utf-8") as f:
        return json.load(f)


def load_cookbook_commands(path_or_url: str) -> list[dict]:
    """Return ``[{source, kind, name, base_model, command}]``. Raises on unreadable input."""
    data = _read(path_or_url)
    if not isinstance(data, dict):
        raise ValueError("cookbook state is not a JSON object")
    out: list[dict] = []
    for t in data.get("tasks") or []:
        payload = (t or {}).get("payload") or {}
        cmd = payload.get("_cmd") or ""
        if cmd.strip():
            repo = payload.get("repo_id") or ""
            out.append({"source": "odysseus", "kind": "task",
                        "name": t.get("name") or repo or "task",
                        "base_model": repo.split("/")[-1], "command": cmd.strip()})
    for p in data.get("presets") or []:
        cmd = (p or {}).get("cmd") or (p or {}).get("_cmd") or ""
        if cmd.strip():
            repo = p.get("model") or ""
            out.append({"source": "odysseus", "kind": "preset",
                        "name": p.get("label") or p.get("name") or "preset",
                        "base_model": repo.split("/")[-1], "command": cmd.strip()})
    return out


def describe(path_or_url: str) -> dict:
    """Status block for the Integrations view."""
    if not path_or_url:
        return {"configured": False}
    info: dict[str, Any] = {"configured": True, "location": path_or_url}
    if not path_or_url.startswith(("http://", "https://")) and not os.path.exists(path_or_url):
        return {**info, "ok": False, "error": "file not found"}
    try:
        info["commands"] = load_cookbook_commands(path_or_url)
        info["ok"] = True
    except Exception as exc:  # noqa: BLE001 - surfaced in the UI
        info.update(ok=False, error=str(exc))
    return info


__all__ = ["load_cookbook_commands", "describe"]
