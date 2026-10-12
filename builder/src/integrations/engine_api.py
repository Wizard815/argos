"""Talk to the Argos engine running in this container: its loaded models, GPUs, pool and model list."""
from __future__ import annotations

import json
import urllib.parse
import urllib.request
from typing import Any


def _get(url: str, timeout: float) -> Any:
    with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310
        body = resp.read().decode("utf-8", errors="replace")
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        return body


def running(base_url: str, timeout: float = 3.0) -> list[dict]:
    """The engine's ``/running``: every model that is not stopped, with its state and command. Raises when unreachable."""
    data = _get(base_url.rstrip("/") + "/running", timeout)
    if not isinstance(data, dict) or not isinstance(data.get("running"), list):
        raise ValueError("the engine did not return a running list")
    return [m for m in data["running"] if isinstance(m, dict) and m.get("model")]


def hardware(base_url: str, timeout: float = 5.0) -> dict:
    """The engine's ``/api/hardware`` snapshot (GPUs, CPU, memory, environment)."""
    data = _get(base_url.rstrip("/") + "/api/hardware", timeout)
    if not isinstance(data, dict):
        raise ValueError("the engine did not return JSON from /api/hardware")
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
    """Ask the engine to re-read the profiles now instead of at its next refresh."""
    return _post(base_url.rstrip("/") + "/api/odysseus/refresh", timeout)


def load_model(base_url: str, model_id: str, timeout: float = 900.0) -> None:
    """Make the engine load one model (its /upstream route starts it); other models are not touched."""
    _get(base_url.rstrip("/") + f"/upstream/{urllib.parse.quote(model_id, safe='')}/health", timeout)


def unload_model(base_url: str, model_id: str, timeout: float = 60.0) -> None:
    """Make the engine stop one model; other models are not touched."""
    _post(base_url.rstrip("/") + f"/api/models/unload/{urllib.parse.quote(model_id, safe='')}", timeout)


def model_log(base_url: str, model_id: str, wait: float = 1.5, max_bytes: int = 200_000) -> str:
    """The recent output of one model's process. The engine streams it (history first, then live), so this reads
    for ``wait`` seconds and stops."""
    import socket
    import time as _time
    url = base_url.rstrip("/") + f"/logs/stream/{urllib.parse.quote(model_id, safe='')}"
    chunks, size, deadline = [], 0, _time.time() + wait
    try:
        with urllib.request.urlopen(url, timeout=wait) as resp:  # noqa: S310
            while _time.time() < deadline and size < max_bytes:
                try:
                    part = resp.read1(8192) if hasattr(resp, "read1") else resp.read(8192)
                except (socket.timeout, TimeoutError):
                    break
                if not part:
                    break
                chunks.append(part)
                size += len(part)
    except (socket.timeout, TimeoutError):
        pass
    return b"".join(chunks).decode("utf-8", errors="replace")[-max_bytes:]


def model_state(base_url: str, model_id: str, timeout: float = 1.5) -> str:
    """"ready", "starting", "stopping" or "stopped" for one model in the engine. Raises when unreachable."""
    for m in running(base_url, timeout):
        if m.get("model") == model_id:
            return str(m.get("state") or "ready")
    return "stopped"


def pool_state(base_url: str, timeout: float = 3.0) -> dict:
    """The engine's ``GET /api/pool``: cards, placement, queue and loading models.

    ``{"enabled": false}`` means the engine is running another router. Raises on a connection or JSON error.
    """
    state = _get(base_url.rstrip("/") + "/api/pool", timeout)
    if not isinstance(state, dict):
        raise ValueError("the engine did not return JSON from /api/pool")
    return state


def model_ids(base_url: str, timeout: float = 3.0) -> set[str]:
    """Ids the engine lists at ``/v1/models`` (variants included, unlisted models not)."""
    data = _get(base_url.rstrip("/") + "/v1/models", timeout)
    return {str(m["id"]) for m in (data.get("data") if isinstance(data, dict) else None) or [] if "id" in m}


__all__ = ["pool_state", "model_ids", "running", "hardware", "version", "odysseus_refresh", "load_model", "unload_model",
           "model_state", "model_log"]
