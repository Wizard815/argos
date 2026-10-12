"""Your own changes to the Flags page: flags and environment variables you add, edit or remove.

The reference lists (``flaglib.FLAG_LIBRARY`` and ``services.ENV_FLAG_LIBRARY``) stay as shipped. Edits are kept in
the settings table under one key, as ``{"flag": {name: entry}, "env": {name: entry}}``. An entry either changes a
row (any of its text fields), adds one (``custom``), or hides one (``removed``). Removing a row you added deletes
it; removing a built-in or seen row hides it, and Restore brings the original back.
"""
from __future__ import annotations

import re
from typing import Any, Optional

from .command import canonical
from .db import Store

KEY = "_flag_edits"
KINDS = ("flag", "env")
FIELDS = {"flag": ("category", "description", "notes", "spellings"), "env": ("category", "description", "recommended")}
FLAG_NAME = re.compile(r"^-{0,2}[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")


class FlagEditError(ValueError):
    pass


def name_for(kind: str, raw: str) -> str:
    """The stored name: a flag by its canonical spelling (``-ngl`` -> ``n-gpu-layers``), an env var as written."""
    raw = (raw or "").strip()
    if kind == "flag":
        if not FLAG_NAME.match(raw):
            raise FlagEditError("a flag looks like --name or -n (letters, digits and dashes)")
        return canonical(raw if raw.startswith("-") else ("-" if len(raw) <= 3 else "--") + raw)
    if kind == "env":
        if not ENV_NAME.match(raw):
            raise FlagEditError("an environment variable is letters, digits and _ and does not start with a digit")
        return raw
    raise FlagEditError("kind is flag or env")


def _clean(kind: str, d: dict) -> dict:
    out: dict[str, Any] = {}
    for k in FIELDS[kind]:
        if k not in d:
            continue
        if k == "spellings":
            v = d[k]
            parts = v.replace(",", " ").split() if isinstance(v, str) else [str(x) for x in (v or [])]
            parts = [p.strip() for p in parts if p.strip()]
            if any(not p.startswith("-") or not FLAG_NAME.match(p) for p in parts):
                raise FlagEditError("spellings are flags such as -c --ctx-size")
            out[k] = parts[:6]
        else:
            out[k] = str(d[k] or "").strip()[: 80 if k == "category" else 600]
    return out


class FlagEdits:
    def __init__(self, store: Store):
        self.store = store

    def all(self) -> dict:
        saved = self.store.get_settings().get(KEY)
        saved = saved if isinstance(saved, dict) else {}
        return {k: dict(saved.get(k) or {}) for k in KINDS}

    def _save(self, edits: dict) -> None:
        self.store.set_setting(KEY, edits)

    def add(self, kind: str, d: dict, known: set[str]) -> str:
        name = name_for(kind, str(d.get("name") or ""))
        edits = self.all()
        current = edits[kind].get(name)
        if (name in known and not (current or {}).get("removed")) or (current and not current.get("removed")):
            raise FlagEditError(f"{name} is already on the list; edit it instead")
        entry = {**_clean(kind, d), "custom": name not in known}
        if name in known:
            entry.pop("custom")  # adding back a removed built-in: show it again with these texts
        edits[kind][name] = entry
        self._save(edits)
        return name

    def update(self, kind: str, name: str, d: dict) -> None:
        edits = self.all()
        entry = edits[kind].get(name, {})
        entry.update(_clean(kind, d))
        entry.pop("removed", None)
        edits[kind][name] = entry
        self._save(edits)

    def remove(self, kind: str, name: str) -> str:
        """"deleted" for a row you added, "hidden" for a built-in or seen one."""
        edits = self.all()
        entry = edits[kind].get(name)
        if entry and entry.get("custom"):
            del edits[kind][name]
            self._save(edits)
            return "deleted"
        edits[kind][name] = {**(entry or {}), "removed": True}
        self._save(edits)
        return "hidden"

    def restore(self, kind: str, name: str) -> bool:
        edits = self.all()
        entry = edits[kind].get(name)
        if not entry or entry.get("custom"):
            return False
        del edits[kind][name]
        self._save(edits)
        return True

    def apply(self, kind: str, rows: list[dict], key: str = "flag", blank: Optional[dict] = None) -> list[dict]:
        """Merge the edits into a listing: changed texts, added rows, and removed rows marked ``removed``."""
        edits = self.all()[kind]
        out, seen = [], set()
        for r in rows:
            name = r[key]
            seen.add(name)
            e = edits.get(name)
            if not e:
                out.append({**r, "edited": False, "custom": False, "removed": False})
                continue
            changed = {k: v for k, v in e.items() if k in FIELDS[kind]}
            out.append({**r, **changed, "edited": bool(changed), "custom": False, "removed": bool(e.get("removed"))})
        for name, e in sorted(edits.items()):
            if name in seen or not e.get("custom"):
                continue
            row = {**(blank or {}), key: name, **{k: v for k, v in e.items() if k in FIELDS[kind]},
                   "edited": False, "custom": True, "removed": False}
            if kind == "flag" and not row.get("spellings"):
                row["spellings"] = [("-" if len(name) <= 2 else "--") + name]
            out.append(row)
        return out


__all__ = ["FlagEdits", "FlagEditError", "name_for", "KINDS"]
