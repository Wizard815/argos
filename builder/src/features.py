"""Settings -> General -> Features: whether each gateway feature works at all ("enabled") and whether the page shows
it ("features"). A feature that is off is skipped on every request, whatever its own settings say; turning it back
on brings those settings back as they were."""
from __future__ import annotations

GROUP = "enabled"
NAMES = ("temperature", "app_rules", "budgets", "mcp_tools", "retries", "formats", "traces")


def on(store, name: str) -> bool:
    """True unless ``name`` was turned off (no store, as in tests: on)."""
    if store is None:
        return True
    try:
        return (store.get_settings().get(GROUP) or {}).get(name, True) is not False
    except Exception:  # noqa: BLE001 - a settings read that fails never stops a request
        return True
