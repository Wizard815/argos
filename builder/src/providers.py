"""Cloud model providers served next to the local models (Settings -> Providers).

A provider is an OpenAI-compatible server (OpenRouter, OpenAI, Groq, another Argos...) with its API key. You pick
which of its models Argos offers; they appear in /v1/models as ``<provider>/<model>`` (``openrouter/tencent/hy4-flash``)
and a request for one goes to that provider under the model's own name, with the provider's key. Everything a key
rule, a limit, usage per key, the cache and MCP tools in chat do for local models, they do for these. They take no
VRAM, so the scheduler never sees them.
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from typing import Optional

KEY = "_providers"
NAME = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,39}$")
_catalog_cache: dict = {}


def clean(d: dict, old: Optional[dict] = None) -> dict:
    name = str(d.get("name") or "").strip().lower()
    if not NAME.match(name):
        raise ValueError("the name is 1-40 lowercase letters, digits, . - or _, e.g. openrouter: it starts each model's name")
    url = str(d.get("url") or "").strip().rstrip("/")
    if not url.startswith(("http://", "https://")):
        raise ValueError("the URL must start with http:// or https://, e.g. https://openrouter.ai/api/v1")
    models = d.get("models") or []
    if isinstance(models, str):
        models = models.replace("\n", ",").split(",")
    key = str(d.get("api_key") or "") or (old or {}).get("api_key", "")
    return {"name": name, "url": url, "api_key": key, "enabled": d.get("enabled", True) is not False,
            "models": sorted({str(m).strip() for m in models if str(m).strip()}),
            "label": str(d.get("label") or "").strip()[:60]}


def listed(store) -> list[dict]:
    out = []
    for p in store.get_settings().get(KEY) or []:
        try:
            out.append(clean(p))
        except ValueError:
            continue
    return out


def public(p: dict) -> dict:
    return {**{k: v for k, v in p.items() if k != "api_key"}, "has_key": bool(p.get("api_key"))}


def resolve(model: str, providers: list[dict]) -> Optional[tuple[dict, str]]:
    """(provider, its model name) for ``<provider>/<model>``, when that model is one you picked."""
    name, sep, rest = model.partition("/")
    if not sep:
        return None
    for p in providers:
        if p["enabled"] and p["name"] == name and rest in p["models"]:
            return p, rest
    return None


def model_entries(providers: list[dict]) -> list[dict]:
    """The /v1/models rows for every picked model."""
    out = []
    for p in providers:
        if not p["enabled"]:
            continue
        for m in p["models"]:
            out.append({"id": f"{p['name']}/{m}", "object": "model", "created": 0, "owned_by": p["name"],
                        "meta": {"argos": {"provider": p["name"], "cloud": True}}})
    return out


def catalog(p: dict, fresh: bool = False) -> list[dict]:
    """Every model the provider offers (GET <url>/models), cached for ten minutes: [{id, name, context, prompt_price,
    completion_price, tools}]. Prices are per million tokens when the provider says (OpenRouter does)."""
    hit = _catalog_cache.get(p["url"])
    if hit and not fresh and time.time() - hit[0] < 600:
        return hit[1]
    headers = {"Accept": "application/json"}
    if p.get("api_key"):
        headers["Authorization"] = f"Bearer {p['api_key']}"
    req = urllib.request.Request(p["url"] + "/models", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 - a URL the user set
            data = json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        raise ValueError(f"{p['name']} answered {exc.code}: {exc.read()[:200].decode('utf-8', 'replace')}") from exc
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise ValueError(f"could not reach {p['url']}: {getattr(exc, 'reason', exc)}") from exc
    rows = []
    for m in data.get("data") or data.get("models") or []:
        if not isinstance(m, dict) or not m.get("id"):
            continue
        price = m.get("pricing") or {}

        def per_m(v):
            try:
                return round(float(v) * 1e6, 4)
            except (TypeError, ValueError):
                return None
        rows.append({"id": m["id"], "name": m.get("name") or m["id"],
                     "context": m.get("context_length") or (m.get("top_provider") or {}).get("context_length"),
                     "prompt_price": per_m(price.get("prompt")), "completion_price": per_m(price.get("completion")),
                     "tools": "tools" in (m.get("supported_parameters") or [])})
    rows.sort(key=lambda r: r["id"])
    _catalog_cache[p["url"]] = (time.time(), rows)
    return rows
