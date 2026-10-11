"""Client + reader for an existing llama-swap instance.

Two integration points:

1. The YAML config file (``config.yaml`` or ``*.yaml`` under the config dir).
   We read ``models``, ``profiles`` and ``selectors`` so we can turn each model's
   launch command into a copyable/testable profile.

2. The running server over HTTP (/api/profiles, /running, /logs, /metrics,
   /activity). Used for live launches, log streaming, and pulling real
   performance samples already recorded by llama-swap.

llama-swap records activity rows with fields mirroring the activity schema::

    tokens_per_second, prompt_per_second, cache_tokens, draft_tokens,
    input_tokens, output_tokens, duration_ms, metadata_json
"""
from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class LlamaSwapModel:
    model_id: str
    cmd: str = ""
    port: Optional[int] = None
    ttl: int = -1
    env: list[str] = field(default_factory=list)
    aliases: list[str] = field(default_factory=lambda: [""])
    capabilities: dict = field(default_factory=dict)

    @property
    def has_mtp(self) -> bool:
        return any("mtp" in e.lower() or "--mtp" in a for e in self.env for _ in [0]) or \
               any(a.startswith("--mtp") for a in self.cmd.split())


class LlamaSwapClient:
    """Thin HTTP client for a running llama-swap server."""

    def __init__(self, base_url: str = "http://localhost:8080"):
        self.base_url = base_url.rstrip("/")

    def _get(self, path: str, params: Optional[dict] = None):
        url = self.base_url + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        with urllib.request.urlopen(url, timeout=5) as resp:  # noqa: S310
            raw = resp.read().decode("utf-8", errors="ignore")
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return raw

    def _post(self, path: str, body=None):
        data = json.dumps(body).encode("utf-8") if body is not None else b""
        req = urllib.request.Request(
            self.base_url + path, data=data, method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310
            raw = resp.read().decode("utf-8", errors="ignore")
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {"text": raw}

    def health(self, timeout: float = 1.5) -> bool:
        """True only if llama-swap answers /health within ``timeout`` seconds."""
        import urllib.request
        url = self.base_url + "/health"
        try:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
                body = resp.read().decode("utf-8", errors="ignore").strip()
            return body in ("OK", "") or body.lower().startswith("ok")
        except Exception:
            return False

    def list_profiles(self):
        return self._get("/api/profiles")

    def _safe_get(self, path: str, params=None):
        """GET with graceful degradation; returns [] on any connection error."""
        try:
            return self._get(path, params)
        except Exception:
            return []

    def running_models(self):
        return self._safe_get("/running")

    def logs(self):
        return self._safe_get("/logs")

    def activity(self, limit: int = 200):
        """Best-effort pull of recent activity rows."""
        try:
            return self._get("/activity", {"limit": limit})
        except Exception:
            return []


def load_llama_swap_config(path: str) -> dict:
    """Load llama-swap YAML config into a plain dict.

    Accepts either the main ``config.yaml`` file or a directory containing
    ``*.yaml``/``*.yml`` files (llama-swap merges these). Returns a dict with
    keys ``models``, ``profiles``, and ``selectors`` populated when present.
    """
    if os.path.isdir(path):
        files = [f for f in os.listdir(path)
                 if f.endswith((".yaml", ".yml"))]
        merged: dict = {}
        for f in sorted(files):
            chunk = _read_yaml(os.path.join(path, f))
            if isinstance(chunk, dict):
                merged.update(chunk)
        data = merged
    else:
        data = _read_yaml(path)
    result = {"models": {}, "profiles": {}, "selectors": {}}
    for key in ("models", "profiles", "selectors"):
        val = data.get(key)
        if isinstance(val, dict):
            result[key] = val
    return result


def _read_yaml(path: str) -> dict:
    try:
        import yaml
    except ImportError:
        # Fall back to a minimal reader that at least surfaces top-level models.
        return _minimal_yaml(path)
    with open(path, encoding="utf-8", errors="ignore") as f:
        loaded = yaml.safe_load(f.read())
    return loaded or {}


def _minimal_yaml(path: str) -> dict:
    """Very small YAML fallback (top-level ``key:`` lines only)."""
    out: dict = {}
    try:
        with open(path, encoding="utf-8", errors="ignore") as f:
            text = f.read()
    except OSError:
        return out
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if ":" in stripped and not stripped.startswith(" ") and not stripped[0].isspace():
            key, _, val = stripped.partition(":")
            out[key.strip()] = val.strip()
    return out


@dataclass
class ProfileSummary:
    name: str
    source_model: Optional[str] = None
    launch_command: str = ""
    env: list[str] = field(default_factory=list)
    tags: list[str] = field(default=lambda: [])
