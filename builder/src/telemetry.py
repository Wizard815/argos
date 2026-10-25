"""Long-term GPU and request history.

A background collector samples the GPUs every few seconds and stores them in
``gpu_samples``; finished hours are folded into ``gpu_hourly`` (kept forever)
and raw rows are dropped after the retention window. GPU readings come from,
in ``auto`` order:

1. Argos's ``GET /api/performance`` -- the container that owns the GPUs
   already samples them (Argos reads LACT, rocm-smi, sysfs or
   nvidia-smi), so this app needs no device access at all;
2. AMD sysfs under /sys/class/drm (gpu_busy_percent, mem_info_vram_*, hwmon);
3. ``nvidia-smi``.

With an Argos URL configured it also copies Argos's per-request
activity (``GET /api/metrics/activity``) into ``requests``, so speeds per
model survive Argos restarts and its own retention.
"""
from __future__ import annotations

import glob
import json
import os
import shutil

from . import gputools
import subprocess
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime
from typing import Callable, Optional

from .gpu_pick import note_cards
from .db import Store

DRM = "/sys/class/drm"


def _read(path: str) -> Optional[str]:
    try:
        with open(path, encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return None


def _num(v: Optional[str]) -> Optional[float]:
    try:
        return float(v) if v is not None else None
    except ValueError:
        return None


def read_amd_sysfs(root: str = DRM) -> list[dict]:
    out = []
    cards = sorted((c for c in glob.glob(os.path.join(root, "card*")) if os.path.basename(c)[4:].isdigit()),
                   key=lambda c: int(os.path.basename(c)[4:]))
    for card in cards:
        dev = os.path.join(card, "device")
        total = _num(_read(os.path.join(dev, "mem_info_vram_total")))
        if not total:
            continue
        hwmon = (glob.glob(os.path.join(dev, "hwmon", "hwmon*")) or [""])[0]
        temp = _num(_read(os.path.join(hwmon, "temp1_input"))) if hwmon else None
        power = (_num(_read(os.path.join(hwmon, "power1_average"))) or
                 _num(_read(os.path.join(hwmon, "power1_input")))) if hwmon else None
        out.append({
            "gpu": len(out),
            "name": _read(os.path.join(dev, "product_name")) or f"AMD GPU ({os.path.basename(card)})",
            "util": _num(_read(os.path.join(dev, "gpu_busy_percent"))),
            "mem_used_mb": round((_num(_read(os.path.join(dev, "mem_info_vram_used"))) or 0) / 2**20, 1),
            "mem_total_mb": round(total / 2**20, 1),
            "temp_c": temp / 1000 if temp is not None else None,
            "power_w": round(power / 1e6, 1) if power is not None else None,
            "source": "sysfs",
        })
    return out


NVIDIA_QUERY = "index,name,utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw"


def parse_nvidia_csv(text: str) -> list[dict]:
    out = []
    for line in text.strip().splitlines():
        p = [x.strip() for x in line.split(",")]
        if len(p) != 7:
            continue
        nums = [_num(x) for x in p[2:]]
        out.append({"gpu": int(_num(p[0]) or 0), "name": p[1], "util": nums[0], "mem_used_mb": nums[1],
                    "mem_total_mb": nums[2], "temp_c": nums[3], "power_w": nums[4], "source": "nvidia-smi"})
    return out


def read_nvidia_smi() -> list[dict]:
    exe = gputools.tool("nvidia-smi")
    if not exe:
        return []
    try:
        res = subprocess.run([exe, f"--query-gpu={NVIDIA_QUERY}", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return []
    return parse_nvidia_csv(res.stdout)


def _get_json(url: str, timeout: float = 5):
    with urllib.request.urlopen(url, timeout=timeout) as r:  # noqa: S310
        return json.loads(r.read().decode("utf-8"))


def _ts(value) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return time.time()


def read_engine(base_url: str) -> list[dict]:
    """Latest reading per GPU from Argos's /api/performance."""
    data = _get_json(base_url.rstrip("/") + "/api/performance")
    if not data.get("enabled"):
        return []
    latest: dict[int, dict] = {}
    for g in data.get("gpu_stats") or []:
        gid = int(g.get("id", 0))
        if gid not in latest or _ts(g.get("timestamp")) >= _ts(latest[gid].get("timestamp")):
            latest[gid] = g
    return [{"gpu": gid, "name": g.get("name") or f"GPU {gid}", "util": g.get("gpu_util_pct"),
             "mem_used_mb": g.get("mem_used_mb"), "mem_total_mb": g.get("mem_total_mb"),
             "temp_c": g.get("temp_c"), "power_w": g.get("power_draw_w"), "source": "engine"}
            for gid, g in sorted(latest.items())]


def fetch_activity(base_url: str, after_id: int, limit: int = 999, order: str = "asc") -> list[dict]:
    """Argos activity rows with id > after_id as ``requests`` rows (oldest first by default)."""
    qs = urllib.parse.urlencode({"min_id": after_id + 1, "limit": limit, "sort": "id", "order": order})
    page = _get_json(f"{base_url.rstrip('/')}/api/metrics/activity?{qs}")
    rows = []
    for e in page.get("data") or []:
        t = e.get("tokens") or {}
        rows.append({
            "source_id": e.get("id"), "ts": _ts(e.get("timestamp")), "model": e.get("model"),
            "src": e.get("src"), "path": e.get("req_path"), "status": e.get("resp_status_code"),
            "input_tokens": t.get("input_tokens"), "output_tokens": t.get("output_tokens"),
            "cache_tokens": t.get("cache_tokens"), "draft_tokens": t.get("draft_tokens"),
            "draft_acc_tokens": t.get("draft_acc_tokens"), "prompt_per_second": t.get("prompt_per_second"),
            "tokens_per_second": t.get("tokens_per_second"), "duration_ms": e.get("duration_ms"),
            "error": e.get("error_msg") or "",
        })
    return rows


class Collector:
    """Background loop: GPU samples, activity sync, rollups and periodic hooks."""

    def __init__(self, store: Store, settings: Callable[[], dict], on_event: Callable[..., None],
                 hooks: Optional[list[Callable[[], None]]] = None):
        self.store = store
        self.settings = settings  # -> {"telemetry": {...}, "integrations": {...}}
        self.on_event = on_event
        self.hooks = hooks or []
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last_rollup = 0.0
        self._reported: dict[str, float] = {}
        self.last_source = ""

    def start(self) -> None:
        if not self._thread:
            self._thread = threading.Thread(target=self._loop, daemon=True, name="telemetry")
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            t = self.settings()["telemetry"]
            interval = max(2, int(t.get("interval_s") or 15))
            if t.get("enabled") and t.get("source") != "off":
                self.tick()
            for hook in self.hooks:
                try:
                    hook()
                except Exception as exc:  # noqa: BLE001
                    self._report(f"background task failed: {exc}")
            self._stop.wait(interval)

    def sample(self) -> list[dict]:
        s = self.settings()
        source = s["telemetry"].get("source") or "auto"
        url = s["integrations"].get("engine_url") or ""
        readers: list[tuple[str, Callable[[], list[dict]]]] = []
        if source in ("auto", "engine") and url:
            readers.append(("engine", lambda: read_engine(url)))
        if source in ("auto", "local"):
            readers += [("sysfs", read_amd_sysfs), ("nvidia-smi", read_nvidia_smi)]
        errors = []
        for name, reader in readers:
            try:
                rows = reader()
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{name}: {exc}")
                continue
            if rows:
                self.last_source = name
                return rows
        if errors:
            self._report("GPU sampling: " + "; ".join(errors))
        return []

    def tick(self, now: Optional[float] = None) -> None:
        now = now or time.time()
        rows = self.sample()
        for r in rows:
            r["ts"] = now
        self.store.add_gpu_samples(rows)
        note_cards(r.get("name") for r in rows)  # AMD or NVIDIA, for the Builder's GPU pick
        self.sync_activity()
        if now - self._last_rollup > 600:
            days = self.settings()["telemetry"].get("raw_days") or 14
            self.store.rollup_gpu(now, keep_raw_s=days * 86400)
            self._last_rollup = now

    def sync_activity(self) -> int:
        url = self.settings()["integrations"].get("engine_url") or ""
        if not url:
            return 0
        cursor = int(self.store.get_settings().get("_activity_cursor") or 0)
        try:
            rows = fetch_activity(url, cursor)
            if not rows and cursor:
                # Argos's ids restart when its database is recreated: resync from 0
                latest = fetch_activity(url, 0, limit=1, order="desc")
                if latest and int(latest[0]["source_id"] or 0) < cursor:
                    rows = fetch_activity(url, 0)
                    self.store.set_setting("_activity_cursor", 0)
        except Exception as exc:  # noqa: BLE001
            self._report(f"Argos activity: {exc}")
            return 0
        if rows:
            self.store.add_requests(rows)
            self.store.set_setting("_activity_cursor", max(int(r["source_id"] or 0) for r in rows))
        return len(rows)

    def _report(self, message: str) -> None:
        """Log each distinct failure at most once an hour instead of every tick."""
        now = time.time()
        if now - self._reported.get(message, 0) > 3600:
            self._reported[message] = now
            self.on_event("telemetry", message, level="warn")


__all__ = ["Collector", "read_amd_sysfs", "read_nvidia_smi", "parse_nvidia_csv", "read_engine",
           "fetch_activity"]
