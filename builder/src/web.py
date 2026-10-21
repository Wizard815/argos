"""Shared plumbing for the HTTP layer: the app context and JSON helpers."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional

from flask import abort, current_app, jsonify, request

from .assistant import Assistant
from .db import Store
from .downloader import Downloads
from .runner import Runner
from .services import Analytics, Recorder
from .inventory import Inventory
from .measure import Measurer
from .memory import Memory
from .scheduler import Plans
from .servers import Servers
from .settings import Settings
from .telemetry import Collector

_ID = re.compile(r"^[A-Za-z0-9._-]{1,80}$")


@dataclass
class Ctx:
    store: Store
    settings: Settings
    runner: Runner
    recorder: Recorder
    analytics: Analytics
    downloads: Downloads
    collector: Collector
    assistant: Assistant
    plans: Plans
    measurer: Measurer
    inventory: Inventory
    memory: Memory
    servers: Servers
    runners: Any = None
    export_state: dict = field(default_factory=dict)
    schedule_state: dict = field(default_factory=dict)

    def event(self, kind: str, message: str, level: str = "info", data: Optional[dict] = None) -> None:
        self.store.add_event(kind, message, level=level, data=data)


def ctx() -> Ctx:
    return current_app.extensions["pb"]


def ok(**kw: Any):
    return jsonify({"ok": True, **kw})


def err(msg: str, code: int = 400):
    return jsonify({"ok": False, "error": msg}), code


def body() -> dict:
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


def check_id(value: str) -> str:
    if not isinstance(value, str) or not _ID.match(value):
        abort(404)
    return value


__all__ = ["Ctx", "ctx", "ok", "err", "body", "check_id"]
