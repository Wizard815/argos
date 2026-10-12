"""Read llama-swap's config and talk to a running llama-swap.

Config: ``config.yaml`` (or a directory of ``*.yaml`` files, merged) with a
``models:`` map of ``name -> {cmd, env, aliases, ...}`` and optional
``macros:``. Macros are expanded into each command; ``${PORT}`` is left for
llama-swap (the playground swaps in a free port when launching).

HTTP: ``/health`` and ``/running`` on the llama-swap server.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import urllib.parse
import urllib.request
from typing import Any

_MACRO = re.compile(r"\$\{([A-Za-z0-9_-]+)\}")


def _read_yaml(path: str) -> dict:
    import yaml  # PyYAML; listed in requirements.txt

    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path}: top level is not a mapping")
    return data


def load_config(path: str) -> dict:
    if os.path.isdir(path):
        merged: dict = {}
        for fname in sorted(os.listdir(path)):
            if fname.endswith((".yaml", ".yml")):
                chunk = _read_yaml(os.path.join(path, fname))
                for key in ("models", "macros"):
                    if isinstance(chunk.get(key), dict):
                        merged.setdefault(key, {}).update(chunk[key])
        return merged
    return _read_yaml(path)


def expand_macros(cmd: str, macros: dict) -> str:
    def sub(m: re.Match) -> str:
        key = m.group(1)
        return str(macros[key]) if key in macros and key != "PORT" else m.group(0)
    # macros can reference other macros; a few passes covers realistic nesting
    for _ in range(5):
        new = _MACRO.sub(sub, cmd)
        if new == cmd:
            break
        cmd = new
    return cmd


def load_model_commands(path: str) -> list[dict]:
    """Return ``[{source, kind, name, base_model, command}]`` for every model."""
    cfg = load_config(path)
    macros = cfg.get("macros") or {}
    out = []
    for name, spec in (cfg.get("models") or {}).items():
        if not isinstance(spec, dict) or not spec.get("cmd"):
            continue
        cmd = expand_macros(str(spec["cmd"]), macros).strip()
        env = [e.partition("=") for e in spec.get("env") or [] if isinstance(e, str) and "=" in e]
        if env:
            cmd = " ".join(f"{k}={shlex.quote(v)}" for k, _, v in env) + " " + cmd
        out.append({"source": "llama-swap", "kind": "model", "name": str(name),
                    "base_model": str(name), "command": cmd})
    return out


def _get(url: str, timeout: float) -> Any:
    with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310
        body = resp.read().decode("utf-8", errors="replace")
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        return body


def server_status(base_url: str, timeout: float = 1.5) -> dict:
    base = base_url.rstrip("/")
    try:
        _get(base + "/health", timeout)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}
    try:
        running = _get(base + "/running", timeout)
        running = running.get("running", []) if isinstance(running, dict) else []
    except Exception:  # noqa: BLE001 - /running is optional on old versions
        running = []
    return {"ok": True, "running": running}


def running(base_url: str, timeout: float = 3.0) -> list[dict]:
    """llama-swap's ``/running``: every model that is not stopped, with its state and command. Raises when unreachable."""
    data = _get(base_url.rstrip("/") + "/running", timeout)
    if not isinstance(data, dict) or not isinstance(data.get("running"), list):
        raise ValueError("llama-swap did not return a running list")
    return [m for m in data["running"] if isinstance(m, dict) and m.get("model")]


def hardware(base_url: str, timeout: float = 5.0) -> dict:
    """llama-swap-rescan's ``/api/hardware`` snapshot (GPUs, CPU, memory, environment)."""
    data = _get(base_url.rstrip("/") + "/api/hardware", timeout)
    if not isinstance(data, dict):
        raise ValueError("llama-swap did not return JSON from /api/hardware")
    return data


def version(base_url: str, timeout: float = 3.0) -> dict:
    data = _get(base_url.rstrip("/") + "/api/version", timeout)
    return data if isinstance(data, dict) else {}


def _post(url: str, timeout: float) -> Any:
    req = urllib.request.Request(url, data=b"", method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
        text = resp.read().decode("utf-8", errors="replace")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def odysseus_refresh(base_url: str, timeout: float = 30.0) -> Any:
    """Ask llama-swap-rescan to re-read the profiles now instead of at its next refresh."""
    return _post(base_url.rstrip("/") + "/api/odysseus/refresh", timeout)


def load_model(base_url: str, model_id: str, timeout: float = 900.0) -> None:
    """Make llama-swap load one model (its /upstream route starts it); other models are not touched."""
    _get(base_url.rstrip("/") + f"/upstream/{urllib.parse.quote(model_id, safe='')}/health", timeout)


def pool_state(base_url: str, timeout: float = 3.0) -> dict:
    """llama-swap-rescan's ``GET /api/pool``: cards, placement, queue and loading models.

    ``{"enabled": false}`` means llama-swap is running but not with the pool
    router. Raises on a connection or JSON error.
    """
    state = _get(base_url.rstrip("/") + "/api/pool", timeout)
    if not isinstance(state, dict):
        raise ValueError("llama-swap did not return JSON from /api/pool (is this llama-swap-rescan?)")
    return state


def model_ids(base_url: str, timeout: float = 3.0) -> set[str]:
    """Ids llama-swap lists at ``/v1/models`` (variants included, unlisted models not)."""
    data = _get(base_url.rstrip("/") + "/v1/models", timeout)
    return {str(m["id"]) for m in (data.get("data") if isinstance(data, dict) else None) or [] if "id" in m}


def describe(config_path: str, url: str) -> dict:
    info: dict[str, Any] = {"configured": bool(config_path or url),
                            "config": config_path, "url": url}
    if config_path:
        try:
            info["commands"] = load_model_commands(config_path)
            info["config_ok"] = True
        except Exception as exc:  # noqa: BLE001 - surfaced in the UI
            info.update(config_ok=False, config_error=str(exc))
    if url:
        info["server"] = server_status(url)
    return info


__all__ = ["load_config", "load_model_commands", "expand_macros", "server_status", "describe",
           "pool_state", "model_ids", "running", "hardware", "version", "odysseus_refresh", "load_model"]
