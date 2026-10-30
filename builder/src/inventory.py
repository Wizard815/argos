"""Long-term inventory: what models exist, what ran, on which GPUs, with which settings.

Built to take in things it has never seen. Every launch flag, env var, quant, cache
type, split mode and speculative type is kept as text in a registry (``traits``) the
first time it appears, and a model's whole command is kept as a JSON blob, so a new
KV cache type, ``--split-mode row`` or an MTP draft model is logged as "new" and
never rejected, mis-parsed or dropped. Nothing here validates a value against a
fixed list; the lists below only decide whether a first sighting needs a human
look (``status = new``) or is already familiar (``status = known``).

Sources:
  - Argos's ``/running`` (every running model with its command) and the pool's
    ``/api/pool`` (which GPUs, the planned VRAM) drive *sessions*: one row each time a
    model is loaded, closed when it stops;
  - the model library, saved profiles and Argos's model list drive *models seen*;
  - Argos's ``/api/hardware`` and ``/api/version`` plus a few local facts are
    kept as *host snapshots* whenever they change.
"""
from __future__ import annotations

import copy
import difflib
import hashlib
import json
import os
import platform
import re
import socket
import threading
import time
from typing import Any, Callable, Optional

from .command import FLAGS, parse_command
from .db import Store
from .gguf import read_arch
from .library import quant_of
from .scheduler import CACHE_BYTES
from .services import ENV_FLAG_LIBRARY

# llama.cpp's LLAMA_FTYPE (general.file_type). An id missing here is recorded as "ftype-<n>".
FTYPE_NAMES = {0: "F32", 1: "F16", 2: "Q4_0", 3: "Q4_1", 7: "Q8_0", 8: "Q5_0", 9: "Q5_1", 10: "Q2_K", 11: "Q3_K_S",
               12: "Q3_K_M", 13: "Q3_K_L", 14: "Q4_K_S", 15: "Q4_K_M", 16: "Q5_K_S", 17: "Q5_K_M", 18: "Q6_K",
               19: "IQ2_XXS", 20: "IQ2_XS", 21: "Q2_K_S", 22: "IQ3_XS", 23: "IQ3_XXS", 24: "IQ1_S", 25: "IQ4_NL",
               26: "IQ3_S", 27: "IQ3_M", 28: "IQ2_S", 29: "IQ2_M", 30: "IQ4_XS", 31: "IQ1_M", 32: "BF16",
               36: "TQ1_0", 37: "TQ2_0", 38: "MXFP4_MOE"}

# What is already familiar. A first sighting outside these is status "new" and shows up for review.
# An explicit list, not a pattern: a quant that merely looks like one (Q9_Z, Q5_K_XXL) is exactly what should be flagged.
KNOWN_QUANT = set(FTYPE_NAMES.values()) | {"Q2_K", "Q3_K", "Q4_K", "Q5_K", "Q6_K", "Q8_K", "MXFP4", "Q2_K_XL", "Q3_K_XL",
                                           "Q4_K_XL", "Q5_K_XL", "Q6_K_XL", "Q8_K_XL", "IQ1_S", "IQ2_M", "IQ3_XXS"}
KNOWN_VALUES = {
    "cache_type": set(CACHE_BYTES),
    "split_mode": {"none", "layer", "row"},
    "spec_type": {"none", "draft", "draft-mtp", "ngram-simple", "ngram-map-k", "ngram-map-k4v", "ngram-mod", "ngram-cache"},
    "load_mode": {"dio", "mmap", "mlock"},
}
# llama-server flags worth no attention beyond FLAGS (sampling, chat, server housekeeping)
COMMON_FLAGS = {
    "temp", "top-k", "top-p", "min-p", "repeat-penalty", "repeat-last-n", "presence-penalty", "frequency-penalty",
    "seed", "chat-template", "chat-template-file", "cache-reuse", "ctx-checkpoints", "slots", "props", "timeout",
    "api-key", "api-key-file", "path", "threads-http", "slot-save-path", "log-file", "log-disable", "no-perf",
    "numa", "poll", "prio", "cpu-mask", "no-context-shift", "context-shift", "keep", "grp-attn-n", "grp-attn-w",
    "yarn-orig-ctx", "yarn-ext-factor", "yarn-attn-factor", "lora", "lora-scaled", "control-vector", "no-escape",
    "samplers", "mirostat", "dry-multiplier", "xtc-probability", "xtc-threshold", "top-n-sigma", "reasoning-format",
    "no-jinja", "special", "check-tensors", "override-kv", "hf-file", "hf-token", "offline", "mmproj-auto",
    "no-mmproj", "no-mmproj-offload", "no-op-offload", "ubatch-size", "parallel", "defrag-thold", "sleep-idle-seconds",
}
KNOWN_FLAGS = set(FLAGS) | COMMON_FLAGS
KNOWN_ENV = {e["flag"] for e in ENV_FLAG_LIBRARY}

KINDS = ("quant", "cache_type", "split_mode", "spec_type", "load_mode", "arch", "flag", "env", "model_kind")
STATUSES = ("new", "known", "ignored", "alias")
MAX_VALUE = 200


def is_known(kind: str, value: str) -> bool:
    v = value.strip()
    if kind == "quant":
        name = v.upper()
        return (name[3:] if name.startswith("UD-") else name) in KNOWN_QUANT
    if kind in KNOWN_VALUES:
        return v.lower() in KNOWN_VALUES[kind]
    if kind == "flag":
        return v in KNOWN_FLAGS
    if kind == "env":
        return v in KNOWN_ENV
    return True  # arch and model_kind are informational; they are logged but need no review


def norm_key(value: str) -> str:
    """``Turbo_V3``, ``turbo-v3`` and ``turbov3`` are the same thing for matching."""
    return re.sub(r"[^a-z0-9]", "", value.lower())


# ----- reading one command ----------------------------------------------------------------
def extract(command: str, model_path: str = "", arch: Optional[dict] = None, size_bytes: int = 0) -> dict:
    """Facts about one launch command. Never raises: an unreadable command yields ``{"unparsed": True}``."""
    facts: dict[str, Any] = {"flags": {}, "env": {}, "traits": [], "unparsed": False}
    try:
        cmd = parse_command(command or "")
    except ValueError:
        facts["unparsed"] = True
        return facts
    s = cmd.summary()
    path = model_path or s.get("model") or ""
    ftype = (arch or {}).get("ftype")
    quant = quant_of(path) or (FTYPE_NAMES.get(ftype, f"ftype-{ftype}") if ftype is not None else "")
    mtp_env = str(cmd.env.get("LLAMA_ENABLE_MTP_OPT", "")).lower() in ("1", "true", "on")
    spec = s.get("spec_type") or ("draft-mtp" if mtp_env else "")
    facts.update(
        quant=quant, ftype=ftype, arch=(arch or {}).get("arch") or "", ctx=s.get("ctx_size"),
        cache_k=s.get("cache_type_k") or "", cache_v=s.get("cache_type_v") or s.get("cache_type_k") or "",
        split_mode=s.get("split_mode") or "", spec_type=spec, load_mode=s.get("load_mode") or "",
        ngl=s.get("n_gpu_layers") or "", parallel=s.get("parallel"), mmproj=bool(s.get("mmproj")),
        model_path=path, env=dict(cmd.env))
    for a in cmd.args:
        if a.name and a.name not in ("model", "port", "host"):
            facts["flags"][a.name] = a.value if a.value is not None else True
    traits = [("quant", quant), ("arch", facts["arch"]), ("split_mode", facts["split_mode"]),
              ("spec_type", spec), ("load_mode", facts["load_mode"])]
    traits += [("cache_type", t) for t in dict.fromkeys(x for x in (facts["cache_k"], facts["cache_v"]) if x)]
    traits += [("flag", f) for f in facts["flags"]]
    traits += [("env", e) for e in cmd.env]
    facts["traits"] = [(k, v) for k, v in traits if v]
    return facts


SPEEDS_TTL_S = 300.0  # the 90-day speed statistics are the expensive part; they may be this old
SUMMARY_TTL_S = 30.0  # how old the model list may get before a background rebuild; a session change drops it at once


class Inventory:
    def __init__(self, store: Store, event: Callable[..., None]):
        self.store, self.event = store, event
        self._lock = threading.RLock()
        self._synced = False  # the first sync after start sees models that were already running
        self._arch_cache: dict[tuple, Optional[dict]] = {}
        self._summary: tuple[float, list[dict]] = (0.0, [])
        self._rebuilding = False
        self._speeds: tuple[float, dict] = (0.0, {})
        self._speeds_busy = False

    # ----- traits ---------------------------------------------------------------------
    def note(self, kind: str, value: Any, example: str = "") -> Optional[dict]:
        """Count one sighting. A first sighting is logged; one that is not familiar is flagged new."""
        value = str(value).strip()[:MAX_VALUE]
        if not value:
            return None
        row, created = self.store.upsert_trait(kind, value, example, status="known" if is_known(kind, value) else "new")
        if created:
            if row["status"] == "new":
                self.event("inventory", f"new {kind.replace('_', ' ')} seen: {value}" + (f" ({example})" if example else ""),
                           level="warn", data={"trait_id": row["id"], "kind": kind, "value": value})
            else:
                self.event("inventory", f"first use of {kind.replace('_', ' ')} {value}" + (f" ({example})" if example else ""),
                           data={"trait_id": row["id"], "kind": kind, "value": value})
        return row

    def arch_of(self, path: str) -> Optional[dict]:
        """GGUF header facts, cached until the file changes. None for a missing or unreadable file."""
        try:
            key = (path, os.path.getmtime(path))
        except OSError:
            return None
        if key not in self._arch_cache:
            self._arch_cache[key] = read_arch(path)
        return self._arch_cache[key]

    def observe_command(self, command: str, label: str = "") -> dict:
        """Register everything a command uses. Safe for any text; returns the facts."""
        try:
            path = ""
            try:
                path = parse_command(command or "").summary().get("model") or ""
            except ValueError:
                pass
            arch = self.arch_of(path) if path.lower().endswith(".gguf") else None
            facts = extract(command, path, arch)
            for kind, value in facts["traits"]:
                self.note(kind, value, label)
            return facts
        except Exception as exc:  # noqa: BLE001 - recording must never break the thing being recorded
            self.event("inventory", f"could not read a command for the inventory: {exc}", level="warn")
            return {"flags": {}, "env": {}, "traits": [], "unparsed": True}

    def aliases(self) -> dict[tuple[str, str], str]:
        return {(t["kind"], t["value"]): t["canonical"] for t in self.store.list_traits(status="alias") if t["canonical"]}

    # ----- sessions -------------------------------------------------------------------
    def sync_sessions(self, running: dict[str, dict], now: Optional[float] = None) -> dict:
        """Make the open sessions match what is running now.

        ``running``: model id -> {"cmd", "state", "cards", "size_gib", "load_seconds", "measured_gib"}. Call it only
        after a successful look at Argos: an unreachable Argos must not look like "everything stopped".
        """
        self.invalidate()
        now = now or time.time()
        opened, closed, moved = [], [], []
        with self._lock:
            first, self._synced = not self._synced, True
            open_by_model: dict[str, dict] = {}
            for s in self.store.list_sessions(open_only=True, limit=5000):
                if s["model_id"] in open_by_model:  # a stray duplicate: close the older one
                    self._close(s, now)
                else:
                    open_by_model[s["model_id"]] = s
            for mid, info in running.items():
                s = open_by_model.pop(mid, None)
                cards = [str(c) for c in info.get("cards") or []]
                if s is None:
                    self._open(mid, info, cards, now, observed=first)
                    opened.append(mid)
                    continue
                changes: dict[str, Any] = {"last_seen": now}
                if cards and cards != [str(c) for c in s["gpus"]]:
                    changes["gpus"] = cards
                    moved.append(mid)
                    self.event("inventory", f"{mid} moved to GPU {','.join(cards)}", data={"model": mid, "cards": cards})
                for k in ("size_gib", "measured_gib", "load_seconds"):
                    if info.get(k) and not s.get(k):
                        changes[k] = info[k]
                self.store.update_session(s["id"], **changes)
            for s in open_by_model.values():
                self._close(s, now)
                closed.append(s["model_id"])
        return {"opened": opened, "closed": closed, "moved": moved}

    def _open(self, mid: str, info: dict, cards: list[str], now: float, observed: bool) -> None:
        cmd = info.get("cmd") or ""
        facts = self.observe_command(cmd, mid) if cmd else {"flags": {}, "env": {}}
        if not cards:  # not placed by the pool router: use the card the command itself pins, if any
            pinned = str((facts.get("env") or {}).get("HIP_VISIBLE_DEVICES") or (facts.get("env") or {}).get("CUDA_VISIBLE_DEVICES") or "")
            cards = [c.strip() for c in pinned.split(",") if c.strip().isdigit()]
        self.store.open_session({
            "model_id": mid, "started_at": now, "last_seen": now, "observed_running": int(observed),
            "source": "engine", "gpus": cards, "size_gib": info.get("size_gib"),
            "measured_gib": info.get("measured_gib"), "load_seconds": info.get("load_seconds"),
            "quant": facts.get("quant"), "ftype": facts.get("ftype"), "arch": facts.get("arch"), "ctx": facts.get("ctx"),
            "cache_k": facts.get("cache_k"), "cache_v": facts.get("cache_v"), "split_mode": facts.get("split_mode"),
            "spec_type": facts.get("spec_type"), "load_mode": facts.get("load_mode"), "ngl": facts.get("ngl"),
            "parallel": facts.get("parallel"), "mmproj": int(bool(facts.get("mmproj"))),
            "flags": facts.get("flags") or {}, "env": facts.get("env") or {}, "command": cmd})
        on = f" on GPU {','.join(cards)}" if cards else ""
        self.event("inventory", f"{mid} {'was already running' if observed else 'loaded'}{on}",
                   data={"model": mid, "cards": cards})

    def _close(self, s: dict, now: float) -> None:
        # If we were not looking for a while, the model stopped some time ago: use the last time we saw it.
        ended = s["last_seen"] if now - s["last_seen"] > 180 else now
        self.store.update_session(s["id"], ended_at=ended, open=0)

    def close_all(self, now: Optional[float] = None) -> None:
        """Used at shutdown or when Argos goes away for good.

        At exit the database may already be gone - a dropped schema, a removed
        file - and there is nothing left to close, so a failure here is swallowed
        rather than printed as an unraisable exception during shutdown.
        """
        self.invalidate()
        try:
            for s in self.store.list_sessions(open_only=True, limit=5000):
                self._close(s, now or time.time())
        except Exception:
            pass

    # ----- refresh: new models, new quants, host --------------------------------------
    def refresh(self, library: list[dict], commands: dict[str, str], live_ids: Optional[set[str]] = None,
                hardware: Optional[dict] = None, version: Optional[dict] = None) -> dict:
        """Scan what exists and record anything new. Each stage is independent: one failing does not stop the rest."""
        self.invalidate()
        out: dict[str, Any] = {"models_new": [], "models_gone": [], "traits_new": [], "host_changed": False, "problems": []}
        before = {t["id"] for t in self.store.list_traits()}

        def stage(name: str, fn: Callable[[], None]) -> None:
            try:
                fn()
            except Exception as exc:  # noqa: BLE001
                out["problems"].append(f"{name}: {exc}")

        def library_stage() -> None:
            ids = []
            for m in library:
                ids.append(m["id"])
                arch = self.arch_of(m["path"])
                ftype = (arch or {}).get("ftype")
                quant = m.get("quant") or (FTYPE_NAMES.get(ftype, f"ftype-{ftype}") if ftype is not None else "")
                for kind, value in (("quant", quant), ("arch", (arch or {}).get("arch")), ("model_kind", m.get("kind"))):
                    if value:
                        self.note(kind, value, m["name"])
                if self.store.upsert_model_seen({"model_id": m["id"], "name": m["name"], "path": m["path"],
                                                 "size_bytes": m.get("size"), "quant": quant, "ftype": ftype,
                                                 "arch": (arch or {}).get("arch") or "", "kind": m.get("kind"),
                                                 "origin": "library", "extra": {"shards": m.get("shards"),
                                                                                "mmproj": bool(m.get("mmproj"))}}):
                    out["models_new"].append(m["id"])
                    self.event("inventory", f"new model: {m['name']} ({quant or 'quant unknown'}"
                                            f"{', ' + arch['arch'] if arch else ''})", data={"model": m["id"]})
            for gone in self.store.mark_models_absent("library", ids):
                out["models_gone"].append(gone)
                self.event("inventory", f"model no longer in the library: {gone}", data={"model": gone})

        def commands_stage() -> None:
            for mid, command in commands.items():
                self.observe_command(command, mid)

        def live_stage() -> None:
            known = {r["model_id"] for r in self.store.list_models_seen()}
            for mid in sorted((live_ids or set()) - known):
                self.store.upsert_model_seen({"model_id": mid, "name": mid, "origin": "engine"})
                out["models_new"].append(mid)
                self.event("inventory", f"Argos lists a new model: {mid}", data={"model": mid})

        def host_stage() -> None:
            out["host_changed"] = self.snapshot_host(hardware, version)

        stage("library", library_stage)
        stage("commands", commands_stage)
        stage("Argos models", live_stage)
        stage("host", host_stage)
        out["traits_new"] = [t["id"] for t in self.store.list_traits() if t["id"] not in before and t["status"] == "new"]
        return out

    def snapshot_host(self, hardware: Optional[dict], version: Optional[dict]) -> bool:
        """Keep the host/container facts when they changed. Local facts always; Argos's when it answered."""
        changed = False
        local = {"hostname": socket.gethostname(), "platform": platform.platform(), "python": platform.python_version(),
                 "cpus": os.cpu_count(), "container": _in_container(), "mem_gib": _mem_gib()}
        for source, data in (("app", local), ("engine", {"hardware": _stable(hardware), "version": version}
                                                         if hardware or version else None)):
            if not data:
                continue
            digest = hashlib.sha1(json.dumps(data, sort_keys=True, default=str).encode()).hexdigest()
            if self.store.add_host_snapshot(source, data, digest):
                changed = True
                self.event("inventory", f"{source} host facts changed" if self._had_snapshot(source) else f"recorded {source} host facts",
                           data={"source": source})
        return changed

    def _had_snapshot(self, source: str) -> bool:
        return sum(1 for s in self.store.list_host_snapshots(200) if s["source"] == source) > 1

    # ----- reading it back ------------------------------------------------------------
    def invalidate(self) -> None:
        """Drop the cached model list; the next ask rebuilds it before answering."""
        self._summary = (0.0, [])

    def models_summary(self, now: Optional[float] = None) -> list[dict]:
        """One row per model: how often and how long it was loaded, on which GPUs, with which quants and settings.

        Built from the whole history, which on a slow disk takes seconds, and the build holds the database
        lock. So a page must never wait on it: once a list exists, a stale one is returned at once and
        rebuilt in the background. Only the very first ask, or one after ``invalidate()``, builds inline.
        An explicit ``now`` always recomputes.
        """
        if now is not None:
            return self._build_summary(now)
        with self._lock:
            at, rows = self._summary
            if not at:
                at, rows = time.time(), self._build_summary(time.time(), self.speed_snapshot())
                self._summary = (at, rows)
            elif time.time() - at > SUMMARY_TTL_S and not self._rebuilding:
                self._rebuilding = True
                threading.Thread(target=self._rebuild_summary, name="inventory-summary", daemon=True).start()
            return copy.deepcopy(rows)

    def _rebuild_summary(self) -> None:
        try:
            rows = self._build_summary(time.time(), self.speed_snapshot())
            with self._lock:
                if self._summary[0]:  # an invalidate() while we ran means this answer is already old
                    self._summary = (time.time(), rows)
        except Exception:  # noqa: BLE001 - keep serving the last good list
            pass
        finally:
            self._rebuilding = False

    def speed_snapshot(self) -> dict:
        """The 90-day speed statistics per model, as last computed. They come from reading the whole request
        history, so they are refreshed in the background and a page gets the last answer (empty right after a
        start, until the first pass finishes) rather than waiting for the scan."""
        with self._lock:
            at, speeds = self._speeds
            if time.time() - at > SPEEDS_TTL_S and not self._speeds_busy:
                self._speeds_busy = True
                threading.Thread(target=self._refresh_speeds, name="inventory-speeds", daemon=True).start()
            return speeds

    def _refresh_speeds(self) -> None:
        try:
            speeds = self._speed_summary(time.time())
            with self._lock:
                self._speeds = (time.time(), speeds)
            self.invalidate()  # the list now has speeds to show
        except Exception:  # noqa: BLE001 - keep the last statistics
            with self._lock:
                self._speeds = (time.time(), self._speeds[1])
        finally:
            self._speeds_busy = False

    def _speed_summary(self, now: float) -> dict:
        from .model_perf import MIN_OUTPUT_TOKENS, MIN_PROMPT_TOKENS
        return self.store.model_speed_summary(now - 90 * 86400, MIN_PROMPT_TOKENS, MIN_OUTPUT_TOKENS, until=now)

    def _build_summary(self, now: float, speeds: Optional[dict] = None) -> list[dict]:
        alias = self.aliases()
        seen = {m["model_id"]: m for m in self.store.list_models_seen()}
        sessions: dict[str, list[dict]] = {}
        for s in self.store.list_sessions(limit=20000):
            sessions.setdefault(s["model_id"], []).append(s)
        # speed per model over 90 days: requests (long enough to be meaningful) and benchmarks. An explicit
        # ``now`` computes them here; the page path passes the last background result.
        if speeds is None:
            speeds = self._speed_summary(now)
        span = lambda d: {"min": round(d["min"], 1), "max": round(d["max"], 1), "n": d["n"]} if d else None  # noqa: E731
        rows = []
        for mid in sorted(set(seen) | set(sessions)):
            ss, m = sessions.get(mid, []), seen.get(mid, {})
            gpus = sorted({g for s in ss for g in s["gpus"]}, key=lambda x: (len(x), x))
            rows.append({
                "model_id": mid, "name": m.get("name") or mid, "origin": m.get("origin") or "",
                "present": bool(m.get("present", 1)), "kind": m.get("kind") or "",
                "first_seen": m.get("first_seen") or (min(s["started_at"] for s in ss) if ss else None),
                "size_gib": round(m["size_bytes"] / 2 ** 30, 2) if m.get("size_bytes") else None,
                "arch": m.get("arch") or "",
                "quants": _uniq(alias.get(("quant", s["quant"]), s["quant"]) for s in ss) or ([m["quant"]] if m.get("quant") else []),
                "cache": _uniq(f"{alias.get(('cache_type', s['cache_k']), s['cache_k'])}/{alias.get(('cache_type', s['cache_v']), s['cache_v'])}"
                               for s in ss if s["cache_k"] or s["cache_v"]),
                "split_modes": _uniq(alias.get(("split_mode", s["split_mode"]), s["split_mode"]) for s in ss),
                "spec_types": _uniq(alias.get(("spec_type", s["spec_type"]), s["spec_type"]) for s in ss),
                "sessions": len(ss), "loaded_seconds": round(sum((s["ended_at"] or now) - s["started_at"] for s in ss)),
                "last_loaded": max((s["started_at"] for s in ss), default=None), "gpus": gpus,
                "vram_gib_avg": _avg(s["measured_gib"] for s in ss) or _avg(s["size_gib"] for s in ss),  # measured beats any estimate
                "open": any(s["open"] for s in ss),
                "tps_avg": (speeds.get(mid) or {}).get("tg_avg"), "requests": (speeds.get(mid) or {}).get("requests") or None,
                "prefill": span((speeds.get(mid) or {}).get("prefill")), "decode": span((speeds.get(mid) or {}).get("decode")),
                "prefill_stretch": (speeds.get(mid) or {}).get("prefill_stretch"),
            })
        return sorted(rows, key=lambda r: (-r["loaded_seconds"], r["name"].lower()))

    def session_stats(self, sessions: list[dict], now: Optional[float] = None) -> list[dict]:
        """Sessions with what the GPUs and the requests did while each was loaded."""
        now = now or time.time()
        out = []
        for s in sessions:
            end = s["ended_at"] or now
            out.append({**s, "duration_s": round(end - s["started_at"]),
                        "gpu_stats": self.store.gpu_window(s["started_at"], end),
                        "requests": self.store.request_window(s["model_id"], s["started_at"], end)})
        return out

    def suggestions(self) -> list[dict]:
        """Rule-based clean-up for traits still marked new: merge look-alikes, accept ones used a lot."""
        every = self.store.list_traits()
        pending = [t for t in every if t["status"] == "new"]
        out = []
        for t in pending:
            peers = [p for p in every if p["kind"] == t["kind"] and p["id"] != t["id"] and p["status"] != "ignored"]
            same = [p for p in peers if norm_key(p["value"]) == norm_key(t["value"])]
            near = [p for p in peers if p not in same and difflib.SequenceMatcher(None, norm_key(p["value"]), norm_key(t["value"])).ratio() >= 0.9]
            target = max(same or near, key=lambda p: (p["status"] == "known", p["count"]), default=None)
            if target and (target["status"] == "known" or target["count"] > t["count"]):
                out.append({"trait_id": t["id"], "kind": t["kind"], "value": t["value"], "action": "alias",
                            "canonical": target["value"], "source": "rules",
                            "reason": f"looks like {target['value']} ({'same letters' if same else 'very close spelling'})"})
            elif t["count"] >= 3:
                out.append({"trait_id": t["id"], "kind": t["kind"], "value": t["value"], "action": "known", "source": "rules",
                            "reason": f"used {t['count']} times"})
        return out

    def validate_suggestions(self, raw: Any) -> list[dict]:
        """Keep only assistant suggestions that name a pending trait and use an allowed action.

        The assistant is a small model: whatever it returns is checked here, and nothing is applied until a person clicks.
        """
        if not isinstance(raw, list):
            return []
        every = {t["id"]: t for t in self.store.list_traits()}
        out, used = [], set()
        for item in raw:
            if not isinstance(item, dict):
                continue
            try:
                tid = int(item.get("trait_id", item.get("id")))
            except (TypeError, ValueError):
                continue
            t = every.get(tid)
            action = str(item.get("action", "")).lower()
            if not t or t["status"] != "new" or tid in used or action not in ("known", "ignore", "alias"):
                continue
            s = {"trait_id": tid, "kind": t["kind"], "value": t["value"], "action": action, "source": "assistant",
                 "reason": str(item.get("reason") or item.get("note") or "")[:200]}
            if action == "alias":
                canon = str(item.get("canonical") or "").strip()
                if not canon or canon == t["value"] or not any(p["kind"] == t["kind"] and p["value"] == canon for p in every.values()):
                    continue
                s["canonical"] = canon
            used.add(tid)
            out.append(s)
        return out

    def digest_lines(self, now: Optional[float] = None) -> list[str]:
        """A few lines of facts for the assistant. Empty until something has been recorded."""
        now = now or time.time()
        models = self.models_summary(now)
        if not models:
            return []
        lines = [f"INVENTORY: {len(models)} models seen ({sum(1 for m in models if m['present'])} present), "
                 f"{sum(m['sessions'] for m in models)} loads recorded."]
        top = [m for m in models if m["sessions"]][:4]
        if top:
            lines.append("Most loaded: " + " | ".join(
                f"{m['name']} {m['loaded_seconds'] / 3600:.1f} h on GPU {','.join(m['gpus']) or '?'}"
                f" ({'/'.join(m['quants']) or '?'}, kv {'/'.join(m['cache']) or '?'}"
                f"{', ' + '/'.join(x for x in m['split_modes'] if x) if any(m['split_modes']) else ''})" for m in top))
        lines += self.disk_lines(models)
        new = [t for t in self.store.list_traits(status="new")][:8]
        if new:
            lines.append("New to review: " + ", ".join(f"{t['kind']} {t['value']} (x{t['count']})" for t in new))
        host = [h for h in self.store.list_host_snapshots(10) if h["source"] == "engine"]
        if host:
            accel = (host[0]["data"].get("hardware") or {}).get("accelerators") or []
            if accel:
                lines.append("Host GPUs: " + ", ".join(
                    f"{a.get('model') or a.get('kind')} ({(a.get('architecture') or '')})" for a in accel[:4]))
        return lines

    def disk_lines(self, models: list[dict], limit: int = 60) -> list[str]:
        """Every model file on disk with its size and parameter count, smallest first.

        "Which ~9B model is smallest" cannot be answered from the most-loaded few.
        """
        extra = {m["model_id"]: (m.get("extra") or {}) for m in self.store.list_models_seen()}
        rows = []
        for m in models:
            if not m["present"] or m["origin"] == "engine" or not m["size_gib"] or m["kind"] in ("mmproj", "mtp", "draft"):
                continue
            params = extra.get(m["model_id"], {}).get("params") or params_from_name(m["name"])
            rows.append((m["size_gib"], f"{m['name']} {m['size_gib']:.1f} GiB" + (f" {params}" if params else "")
                         + (f" {'/'.join(m['quants'])}" if m["quants"] else "")))
        if not rows:
            return []
        rows.sort()
        more = f" (+{len(rows) - limit} more)" if len(rows) > limit else ""
        return [f"MODELS ON DISK ({len(rows)}, smallest first; name, size in GiB, parameters, quant): "
                + " | ".join(r[1] for r in rows[:limit]) + more]


_PARAMS = re.compile(r"(?<![\w.])(?:[A-Za-z])?(\d+(?:\.\d+)?)\s?([BbMm])(?![A-Za-z])")


def params_from_name(name: str) -> str:
    """'Qwythos-9B-...' -> '9B'. Empty when the name carries no size."""
    m = _PARAMS.search(name.replace("_", "-"))
    return f"{m.group(1)}{m.group(2).upper()}" if m else ""


def _uniq(values) -> list[str]:
    return list(dict.fromkeys(v for v in values if v))


def _avg(values) -> Optional[float]:
    vs = [v for v in values if v]
    return round(sum(vs) / len(vs), 2) if vs else None


def _stable(hardware: Optional[dict]) -> Optional[dict]:
    """Drop the fields that change every call, so a snapshot only counts as changed when the hardware did."""
    if not isinstance(hardware, dict):
        return hardware
    return {k: v for k, v in hardware.items() if k not in ("captured_at",)}


def _in_container() -> bool:
    if os.path.exists("/.dockerenv"):
        return True
    try:
        with open("/proc/1/cgroup", encoding="utf-8") as f:
            return any(w in f.read() for w in ("docker", "containerd", "kubepods", "podman"))
    except OSError:
        return False


def _mem_gib() -> Optional[float]:
    try:
        with open("/proc/meminfo", encoding="utf-8") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    return round(int(line.split()[1]) / 2 ** 20, 1)
    except (OSError, ValueError):
        pass
    return None


__all__ = ["Inventory", "extract", "is_known", "norm_key", "FTYPE_NAMES", "KINDS", "STATUSES"]
