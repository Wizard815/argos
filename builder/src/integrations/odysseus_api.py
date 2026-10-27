"""Talk to a running Odysseus through its scoped API (``/api/codex/*``).

Odysseus issues API tokens (``ody_…``) with scopes. This client uses:

  notes     todos:read / todos:write          (Odysseus' Notes)
  brain     memory:read / memory:write        (its Brain / memories)
  calendar  calendar:read / calendar:write
  library   documents:read / documents:write  (its document Library)
  cookbook  cookbook:read
  gallery   gallery:read                      (needs the Odysseus update that adds it)
  tasks     tasks:read / tasks:run            (needs the Odysseus update)
  tools     skills:read                       (needs the Odysseus update)

Every call raises OdysseusError with a message meant for a person: missing scope,
wrong token, not reachable, or "this Odysseus does not have that yet".
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any, Optional


class OdysseusError(Exception):
    def __init__(self, message: str, status: int = 0):
        super().__init__(message)
        self.status = status


AREAS = ("notes", "brain", "calendar", "library", "cookbook", "gallery", "tasks", "tools")


class Odysseus:
    def __init__(self, url: str, token: str, timeout: float = 20.0):
        if not url:
            raise OdysseusError("set the Odysseus URL under Integrations")
        if not token:
            raise OdysseusError("create an API token in Odysseus (Settings → API tokens) and paste it under Integrations")
        self.url, self.token, self.timeout = url.rstrip("/"), token, timeout

    def _call(self, method: str, path: str, body: Optional[dict] = None, query: Optional[dict] = None) -> Any:
        url = self.url + path + ("?" + urllib.parse.urlencode({k: v for k, v in (query or {}).items() if v not in (None, "")}) if query else "")
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers={
            "Authorization": f"Bearer {self.token}", "Content-Type": "application/json", "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:  # noqa: S310
                text = r.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:300]
            try:
                detail = json.loads(detail).get("detail") or json.loads(detail).get("error") or detail
            except (ValueError, AttributeError):
                pass
            if exc.code == 401:
                raise OdysseusError("Odysseus did not accept the API token", 401) from exc
            if exc.code == 403:
                raise OdysseusError(f"the API token is not allowed to do this: {detail}", 403) from exc
            if exc.code == 404:
                raise OdysseusError("this Odysseus does not have that yet (update Odysseus)", 404) from exc
            raise OdysseusError(f"Odysseus returned HTTP {exc.code}: {detail}", exc.code) from exc
        except (urllib.error.URLError, OSError) as exc:
            raise OdysseusError(f"Odysseus not reachable at {self.url}: {getattr(exc, 'reason', exc)}") from exc
        try:
            return json.loads(text) if text else {}
        except ValueError:
            return {"text": text}

    # ----- what the token may do ----------------------------------------------------------
    def capabilities(self) -> dict:
        return self._call("GET", "/api/codex/capabilities")

    # ----- notes (Odysseus calls them todos in this API) ----------------------------------
    def notes(self, archived: bool = False) -> Any:
        return self._call("GET", "/api/codex/todos", query={"archived": str(archived).lower()})

    def add_note(self, title: str, content: str = "", items: Optional[list[str]] = None) -> Any:
        body: dict = {"action": "add", "title": title, "content": content}
        if items:
            body["items"] = items
        return self._call("POST", "/api/codex/todos", body)

    # ----- brain ---------------------------------------------------------------------------
    def memories(self) -> Any:
        return self._call("GET", "/api/codex/memory")

    def remember(self, text: str, category: str = "fact") -> Any:
        return self._call("POST", "/api/codex/memory", {"text": text, "category": category, "source": "user"})

    # ----- calendar ------------------------------------------------------------------------
    def events(self, days_back: int = 7, days_ahead: int = 30) -> Any:
        now = datetime.now(timezone.utc)
        return self._call("GET", "/api/codex/calendar/events", query={
            "start": (now - timedelta(days=days_back)).isoformat(), "end": (now + timedelta(days=days_ahead)).isoformat()})

    def add_event(self, summary: str, start: str, end: str = "", description: str = "") -> Any:
        body = {"summary": summary, "dtstart": start, "description": description}
        if end:
            body["dtend"] = end
        return self._call("POST", "/api/codex/calendar/events", body)

    # ----- library -------------------------------------------------------------------------
    def documents(self, search: str = "") -> Any:
        return self._call("GET", "/api/codex/documents", query={"search": search})

    def document(self, doc_id: str) -> Any:
        return self._call("GET", f"/api/codex/documents/{urllib.parse.quote(doc_id, safe='')}")

    def add_document(self, title: str, content: str, language: str = "markdown") -> Any:
        return self._call("POST", "/api/codex/documents", {"title": title, "content": content, "language": language})

    # ----- cookbook ------------------------------------------------------------------------
    def cookbook(self) -> Any:
        return {"presets": self._call("GET", "/api/codex/cookbook/presets").get("presets", []),
                "servers": self._call("GET", "/api/codex/cookbook/servers")}

    def delete_preset(self, name: str, cmd: str) -> Any:
        """Delete the saved presets with this command (and name, when given). Needs the cookbook:launch scope
        and the Odysseus update that adds the endpoint; an older Odysseus answers 404/405."""
        return self._call("POST", "/api/codex/cookbook/presets/delete", {"name": name, "cmd": cmd})

    # ----- added by the Odysseus update -----------------------------------------------------
    def gallery(self, search: str = "", limit: int = 48) -> Any:
        return self._call("GET", "/api/codex/gallery", query={"search": search, "limit": limit})

    def tasks(self) -> Any:
        return self._call("GET", "/api/codex/tasks")

    def run_task(self, task_id: str) -> Any:
        return self._call("POST", f"/api/codex/tasks/{urllib.parse.quote(task_id, safe='')}/run", {})

    def tools(self) -> Any:
        return self._call("GET", "/api/codex/skills")


def as_list(data: Any, *keys: str) -> list:
    """Odysseus answers with a list, or a dict holding one under some key. Find it."""
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for k in keys + ("items", "results", "data"):
            if isinstance(data.get(k), list):
                return data[k]
    return []


__all__ = ["Odysseus", "OdysseusError", "AREAS", "as_list"]
