"""SQLite persistence -- the app's embedded database.

Tables
------
runs            one row per benchmark / recorded launch (the perf sheet)
profiles        saved launch commands; no limit, any number per model
catalog_files   extra mmproj / draft-model files keyed to a base model
env_flags       last performance seen with each env var set
settings        UI-editable settings (JSON values), overriding env defaults
events          long-term app log: launches, exits, benches, downloads, errors
gpu_samples     raw GPU readings (kept for the raw retention window)
gpu_hourly      hourly GPU rollups, kept forever
requests        per-request speeds pulled from llama-swap's activity log
downloads       Hugging Face downloads and their progress
chat_messages   the assistant's single conversation

Databases written by earlier versions are migrated in place on open.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from typing import Any, Iterable, Optional

RUN_COLUMNS: dict[str, str] = {
    "run_id": "TEXT NOT NULL UNIQUE",
    "launch_id": "TEXT",
    "created_at": "REAL NOT NULL",
    "base_model": "TEXT NOT NULL",
    "model_id": "TEXT",
    "profile_name": "TEXT",
    "launch_command": "TEXT NOT NULL",
    "env_json": "TEXT DEFAULT '{}'",
    "gguf_path": "TEXT",
    "mmproj_file": "TEXT",
    "draft_model": "TEXT",
    "spec_type": "TEXT",
    "port": "INTEGER",
    "backend": "TEXT",
    "gpu_layers": "TEXT",
    "n_ctx": "INTEGER",
    "n_batch": "INTEGER",
    "n_ubatch": "INTEGER",
    "n_threads": "INTEGER",
    "n_parallel": "INTEGER",
    "mmap": "INTEGER",
    "load_mode": "TEXT",
    "flash_attention": "TEXT",
    "cache_type_k": "TEXT",
    "cache_type_v": "TEXT",
    "input_tokens": "INTEGER",
    "output_tokens": "INTEGER",
    "draft_tokens": "INTEGER",
    "draft_acc": "REAL",
    "prompt_per_second": "REAL",
    "tokens_per_second": "REAL",
    "prompt_ms": "REAL",
    "eval_ms": "REAL",
    "duration_ms": "INTEGER",
    "load_ms": "REAL",
    "layers_offloaded": "TEXT",
    "gpu_util": "REAL",
    "mem_used_gb": "REAL",
    "temp_c": "REAL",
    "note": "TEXT",
    "log_source": "TEXT",
    "success": "INTEGER DEFAULT 1",
}

PROFILE_COLUMNS: dict[str, str] = {
    "profile_id": "TEXT NOT NULL UNIQUE",
    "name": "TEXT NOT NULL",
    "base_model": "TEXT",
    "model_id": "TEXT",
    "source": "TEXT DEFAULT ''",
    "launch_command": "TEXT NOT NULL",
    "env_json": "TEXT DEFAULT '{}'",
    "notes": "TEXT DEFAULT ''",
    "favorite": "INTEGER DEFAULT 0",
    "created_at": "REAL NOT NULL",
    "updated_at": "REAL NOT NULL",
}


def _cols(spec: dict[str, str]) -> str:
    return ",\n    ".join(f"{k} {v}" for k, v in spec.items())


SCHEMA = f"""
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    {_cols(RUN_COLUMNS)}
);
CREATE INDEX IF NOT EXISTS idx_runs_base ON runs (base_model);
CREATE INDEX IF NOT EXISTS idx_runs_created ON runs (created_at DESC);

CREATE TABLE IF NOT EXISTS profiles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    {_cols(PROFILE_COLUMNS)}
);

CREATE TABLE IF NOT EXISTS catalog_files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL CHECK (kind IN ('mmproj', 'draft')),
    base_model TEXT NOT NULL,
    name TEXT NOT NULL,
    hf_repo TEXT DEFAULT '',
    file_path TEXT DEFAULT '',
    notes TEXT DEFAULT '',
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS env_flags (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    flag TEXT NOT NULL UNIQUE,
    category TEXT,
    description TEXT,
    recommended TEXT,
    gpu_target TEXT,
    last_used_for TEXT,
    last_tokens_per_second REAL,
    last_prompt_per_second REAL,
    last_run_at REAL,
    note TEXT
);

CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    level TEXT NOT NULL DEFAULT 'info',
    kind TEXT NOT NULL,
    message TEXT NOT NULL,
    data TEXT DEFAULT '{{}}'
);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events (ts DESC);

CREATE TABLE IF NOT EXISTS gpu_samples (
    ts REAL NOT NULL, gpu INTEGER NOT NULL, name TEXT, util REAL, mem_used_mb REAL,
    mem_total_mb REAL, temp_c REAL, power_w REAL, source TEXT
);
CREATE INDEX IF NOT EXISTS idx_gpu_samples_ts ON gpu_samples (ts);

CREATE TABLE IF NOT EXISTS gpu_hourly (
    hour REAL NOT NULL, gpu INTEGER NOT NULL, name TEXT, samples INTEGER,
    util_avg REAL, util_max REAL, mem_avg_mb REAL, mem_max_mb REAL, mem_total_mb REAL,
    temp_avg REAL, temp_max REAL, power_avg REAL, power_max REAL,
    PRIMARY KEY (hour, gpu)
);

CREATE TABLE IF NOT EXISTS requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id INTEGER, ts REAL NOT NULL, model TEXT, src TEXT, path TEXT, status INTEGER,
    input_tokens INTEGER, output_tokens INTEGER, cache_tokens INTEGER, draft_tokens INTEGER,
    draft_acc_tokens INTEGER, prompt_per_second REAL, tokens_per_second REAL,
    duration_ms INTEGER, error TEXT,
    UNIQUE (source_id, ts)
);
CREATE INDEX IF NOT EXISTS idx_requests_ts ON requests (ts DESC);

CREATE TABLE IF NOT EXISTS downloads (
    id TEXT PRIMARY KEY, repo TEXT NOT NULL, files TEXT NOT NULL, dest TEXT NOT NULL,
    status TEXT NOT NULL, bytes_done INTEGER DEFAULT 0, bytes_total INTEGER DEFAULT 0,
    error TEXT DEFAULT '', created_at REAL NOT NULL, updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS chat_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL, role TEXT NOT NULL, content TEXT NOT NULL,
    compacted INTEGER DEFAULT 0
);
"""

PROFILE_FIELDS = ("name", "base_model", "model_id", "source", "launch_command", "notes")
GPU_FIELDS = ("ts", "gpu", "name", "util", "mem_used_mb", "mem_total_mb", "temp_c", "power_w", "source")
REQUEST_FIELDS = ("source_id", "ts", "model", "src", "path", "status", "input_tokens", "output_tokens",
                  "cache_tokens", "draft_tokens", "draft_acc_tokens", "prompt_per_second",
                  "tokens_per_second", "duration_ms", "error")


def new_id(prefix: str) -> str:
    return f"{prefix}-{int(time.time() * 1000)}-{os.urandom(2).hex()}"


class Store:
    def __init__(self, path: str):
        self.path = path
        if os.path.dirname(path):
            os.makedirs(os.path.dirname(path), exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        with self._lock:
            self._migrate_legacy()
            self._conn.executescript(SCHEMA)
            self._add_missing_columns("runs", RUN_COLUMNS)
            self._add_missing_columns("profiles", PROFILE_COLUMNS)
            self._conn.commit()

    def _q(self, sql: str, args: Iterable = ()) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self._conn.execute(sql, tuple(args)).fetchall()]

    def _x(self, sql: str, args: Iterable = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self._conn.execute(sql, tuple(args))
            self._conn.commit()
            return cur

    # ----- migrations -----------------------------------------------------
    def _tables(self) -> dict[str, str]:
        rows = self._conn.execute("SELECT name, sql FROM sqlite_master WHERE type='table'")
        return {r[0]: r[1] or "" for r in rows}

    def _columns(self, table: str) -> set[str]:
        return {r[1] for r in self._conn.execute(f"PRAGMA table_info({table})")}

    def _migrate_legacy(self) -> None:
        tables = self._tables()
        if "runs" in tables:
            cols = self._columns("runs")
            for old, new in (("mmjog_file", "mmproj_file"), ("custom_env_json", "env_json"),
                             ("gpu_util_real", "gpu_util")):
                if old in cols and new not in cols:
                    self._conn.execute(f"ALTER TABLE runs RENAME COLUMN {old} TO {new}")
        if "profiles" in tables and "copied_into" in self._columns("profiles"):
            self._conn.execute("ALTER TABLE profiles RENAME TO profiles_legacy")
            self._conn.executescript(SCHEMA)
            self._conn.execute(
                "INSERT INTO profiles (profile_id, name, base_model, source, launch_command, "
                "env_json, created_at, updated_at) SELECT profile_id, name, copied_into, "
                "source_model, launch_command, env_json, created_at, updated_at FROM profiles_legacy")
            self._conn.execute("DROP TABLE profiles_legacy")
        # catalog: v1 had two tables, v2 a CHECK allowing 'mtp' (not a llama.cpp file
        # type -- MTP is --spec-type draft-mtp); both become 'draft' rows now.
        if "catalog_files" in tables and "'draft'" not in tables["catalog_files"]:
            self._conn.execute("ALTER TABLE catalog_files RENAME TO catalog_v2")
            self._conn.executescript(SCHEMA)
            self._conn.execute(
                "INSERT INTO catalog_files (kind, base_model, name, hf_repo, file_path, notes, created_at) "
                "SELECT CASE kind WHEN 'mtp' THEN 'draft' ELSE kind END, base_model, name, hf_repo, "
                "file_path, notes, created_at FROM catalog_v2")
            self._conn.execute("DROP TABLE catalog_v2")
        legacy_catalog = (("mtp_models", "draft"), ("vision_mmjogs", "mmproj"))
        if any(t in tables for t, _ in legacy_catalog):
            self._conn.executescript(SCHEMA)
            for table, kind in legacy_catalog:
                if table in tables:
                    self._conn.execute(
                        "INSERT INTO catalog_files (kind, base_model, name, hf_repo, file_path, notes, "
                        f"created_at) SELECT '{kind}', COALESCE(base_model, model_id), name, "
                        f"COALESCE(hf_repo, ''), COALESCE(file_path, ''), COALESCE(notes, ''), "
                        f"created_at FROM {table}")
                    self._conn.execute(f"DROP TABLE {table}")

    def _add_missing_columns(self, table: str, spec: dict[str, str]) -> None:
        have = self._columns(table)
        for name, decl in spec.items():
            if name not in have:
                decl = decl.replace("NOT NULL UNIQUE", "").replace("NOT NULL", "")
                self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")

    # ----- runs -----------------------------------------------------------
    def add_run(self, rec: dict[str, Any]) -> None:
        """Insert or replace (by run_id) a perf-sheet row; unknown keys are ignored."""
        row = {k: v for k, v in rec.items() if k in RUN_COLUMNS}
        if isinstance(row.get("env_json"), dict):
            row["env_json"] = json.dumps(row["env_json"])
        updates = ", ".join(f"{k}=excluded.{k}" for k in row if k != "run_id")
        self._x(f"INSERT INTO runs ({', '.join(row)}) VALUES ({', '.join('?' * len(row))}) "
                f"ON CONFLICT(run_id) DO UPDATE SET {updates}", row.values())

    def get_run(self, run_id: str) -> Optional[dict]:
        rows = self._q("SELECT * FROM runs WHERE run_id=?", (run_id,))
        return _decode(rows[0]) if rows else None

    def list_runs(self, base_model: Optional[str] = None, limit: int = 500) -> list[dict]:
        q, args = "SELECT * FROM runs", []
        if base_model:
            q, args = q + " WHERE base_model=?", [base_model]
        return [_decode(r) for r in self._q(q + " ORDER BY created_at DESC LIMIT ?", args + [limit])]

    def delete_run(self, run_id: str) -> bool:
        return self._x("DELETE FROM runs WHERE run_id=?", (run_id,)).rowcount > 0

    def base_models(self) -> list[str]:
        q = ("SELECT base_model FROM runs UNION SELECT base_model FROM profiles "
             "UNION SELECT base_model FROM catalog_files")
        return sorted({r["base_model"] for r in self._q(q) if r["base_model"]}, key=str.lower)

    # ----- profiles -------------------------------------------------------
    def save_profile(self, d: dict[str, Any]) -> dict:
        """Create, or update when ``profile_id`` matches an existing profile. No limit."""
        pid = d.get("profile_id") or new_id("prof")
        vals = {k: str(d.get(k) or "").strip() for k in PROFILE_FIELDS}
        now = time.time()
        self._x(
            f"INSERT INTO profiles (profile_id, {', '.join(PROFILE_FIELDS)}, favorite, created_at, updated_at) "
            f"VALUES (?, {', '.join('?' * len(PROFILE_FIELDS))}, ?, ?, ?) ON CONFLICT(profile_id) DO UPDATE SET "
            f"{', '.join(f'{k}=excluded.{k}' for k in PROFILE_FIELDS)}, favorite=excluded.favorite, "
            f"updated_at=excluded.updated_at",
            (pid, *vals.values(), 1 if d.get("favorite") else 0, now, now))
        return self.get_profile(pid)  # type: ignore[return-value]

    def get_profile(self, profile_id: str) -> Optional[dict]:
        rows = self._q("SELECT * FROM profiles WHERE profile_id=?", (profile_id,))
        return _decode(rows[0]) if rows else None

    def list_profiles(self, model_id: Optional[str] = None) -> list[dict]:
        q, args = "SELECT * FROM profiles", []
        if model_id:
            q, args = q + " WHERE model_id=?", [model_id]
        return [_decode(r) for r in self._q(q + " ORDER BY favorite DESC, updated_at DESC", args)]

    def delete_profile(self, profile_id: str) -> bool:
        return self._x("DELETE FROM profiles WHERE profile_id=?", (profile_id,)).rowcount > 0

    # ----- catalog (extra mmproj / draft files) ---------------------------
    def add_file(self, kind: str, base_model: str, *, name: str = "", hf_repo: str = "",
                 file_path: str = "", notes: str = "") -> int:
        name = name or os.path.basename((file_path or hf_repo).rstrip("/")) or kind
        cur = self._x("INSERT INTO catalog_files (kind, base_model, name, hf_repo, file_path, notes, "
                      "created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                      (kind, base_model, name, hf_repo, file_path, notes, time.time()))
        return int(cur.lastrowid or 0)

    def list_files(self, kind: Optional[str] = None, base_model: Optional[str] = None) -> list[dict]:
        where, args = [], []
        if kind:
            where.append("kind=?")
            args.append(kind)
        if base_model:
            where.append("base_model=?")
            args.append(base_model)
        q = "SELECT * FROM catalog_files" + (" WHERE " + " AND ".join(where) if where else "")
        return self._q(q + " ORDER BY base_model, created_at DESC", args)

    def delete_file(self, file_id: int) -> bool:
        return self._x("DELETE FROM catalog_files WHERE id=?", (file_id,)).rowcount > 0

    # ----- env-var usage --------------------------------------------------
    def list_env_flags(self) -> list[dict]:
        return self._q("SELECT * FROM env_flags ORDER BY flag")

    def set_last_used(self, flag: str, base_model: str, tps: Optional[float],
                      pps: Optional[float], run_at: float) -> None:
        self._x("INSERT INTO env_flags (flag, last_used_for, last_tokens_per_second, "
                "last_prompt_per_second, last_run_at) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(flag) DO UPDATE SET last_used_for=excluded.last_used_for, "
                "last_tokens_per_second=excluded.last_tokens_per_second, "
                "last_prompt_per_second=excluded.last_prompt_per_second, last_run_at=excluded.last_run_at",
                (flag, base_model, tps, pps, run_at))

    # ----- settings -------------------------------------------------------
    def get_settings(self) -> dict[str, Any]:
        return {r["key"]: _json(r["value"], None) for r in self._q("SELECT key, value FROM settings")}

    def set_setting(self, key: str, value: Any) -> None:
        if value is None:
            self._x("DELETE FROM settings WHERE key=?", (key,))
        else:
            self._x("INSERT INTO settings (key, value) VALUES (?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, json.dumps(value)))

    # ----- events ---------------------------------------------------------
    def add_event(self, kind: str, message: str, level: str = "info", data: Optional[dict] = None,
                  ts: Optional[float] = None) -> None:
        self._x("INSERT INTO events (ts, level, kind, message, data) VALUES (?, ?, ?, ?, ?)",
                (ts or time.time(), level, kind, message[:2000], json.dumps(data or {})))

    def list_events(self, kind: Optional[str] = None, level: Optional[str] = None,
                    since: Optional[float] = None, q: Optional[str] = None, limit: int = 300) -> list[dict]:
        where, args = [], []
        for col, val in (("kind", kind), ("level", level)):
            if val:
                where.append(f"{col}=?")
                args.append(val)
        if since:
            where.append("ts>=?")
            args.append(since)
        if q:
            where.append("message LIKE ?")
            args.append(f"%{q}%")
        sql = "SELECT * FROM events" + (" WHERE " + " AND ".join(where) if where else "")
        rows = self._q(sql + " ORDER BY ts DESC, id DESC LIMIT ?", args + [limit])
        for r in rows:
            r["data"] = _json(r["data"], {})
        return rows

    # ----- GPU telemetry --------------------------------------------------
    def add_gpu_samples(self, samples: list[dict]) -> None:
        if not samples:
            return
        with self._lock:
            self._conn.executemany(
                f"INSERT INTO gpu_samples ({', '.join(GPU_FIELDS)}) VALUES ({', '.join('?' * len(GPU_FIELDS))})",
                [tuple(s.get(k) for k in GPU_FIELDS) for s in samples])
            self._conn.commit()

    def gpu_series(self, since: float, bucket: int) -> list[dict]:
        """Raw samples averaged into ``bucket``-second buckets, newer than ``since``."""
        return self._q(
            "SELECT CAST(ts / ? AS INTEGER) * ? AS t, gpu, MAX(name) AS name, AVG(util) AS util, "
            "MAX(util) AS util_max, AVG(mem_used_mb) AS mem_used_mb, MAX(mem_total_mb) AS mem_total_mb, "
            "AVG(temp_c) AS temp_c, MAX(temp_c) AS temp_max, AVG(power_w) AS power_w, COUNT(*) AS n "
            "FROM gpu_samples WHERE ts>=? GROUP BY t, gpu ORDER BY t, gpu", (bucket, bucket, since))

    def gpu_summary(self, since: float) -> list[dict]:
        """Per-GPU aggregates over raw samples newer than ``since``."""
        return self._q(
            "SELECT gpu, MAX(name) AS name, COUNT(*) AS n, AVG(util) AS util_avg, MAX(util) AS util_max, "
            "AVG(mem_used_mb) AS mem_avg_mb, MAX(mem_used_mb) AS mem_max_mb, MAX(mem_total_mb) AS mem_total_mb, "
            "MAX(temp_c) AS temp_max, AVG(power_w) AS power_avg FROM gpu_samples WHERE ts>=? "
            "GROUP BY gpu ORDER BY gpu", (since,))

    def gpu_latest(self) -> list[dict]:
        return self._q("SELECT * FROM gpu_samples WHERE ts=(SELECT MAX(ts) FROM gpu_samples) ORDER BY gpu")

    def gpu_rollup_series(self, since: float, bucket: int) -> list[dict]:
        """Hourly rollups regrouped into ``bucket``-second buckets (for ranges beyond raw retention)."""
        return self._q(
            "SELECT CAST(hour / ? AS INTEGER) * ? AS t, gpu, MAX(name) AS name, "
            "SUM(util_avg * samples) / SUM(samples) AS util, MAX(util_max) AS util_max, "
            "SUM(mem_avg_mb * samples) / SUM(samples) AS mem_used_mb, MAX(mem_total_mb) AS mem_total_mb, "
            "SUM(temp_avg * samples) / SUM(samples) AS temp_c, MAX(temp_max) AS temp_max, "
            "SUM(power_avg * samples) / SUM(samples) AS power_w, SUM(samples) AS n "
            "FROM gpu_hourly WHERE hour>=? GROUP BY t, gpu ORDER BY t, gpu", (bucket, bucket, since))

    def gpu_hourly(self, since: float) -> list[dict]:
        return self._q("SELECT * FROM gpu_hourly WHERE hour>=? ORDER BY hour, gpu", (since,))

    def rollup_gpu(self, now: Optional[float] = None, keep_raw_s: float = 14 * 86400) -> None:
        """Fold every finished hour of raw samples into gpu_hourly; drop raw beyond retention."""
        now = now or time.time()
        current_hour = int(now // 3600) * 3600
        with self._lock:
            self._conn.execute(
                "INSERT INTO gpu_hourly (hour, gpu, name, samples, util_avg, util_max, mem_avg_mb, "
                "mem_max_mb, mem_total_mb, temp_avg, temp_max, power_avg, power_max) "
                "SELECT CAST(ts / 3600 AS INTEGER) * 3600 AS h, gpu, MAX(name), COUNT(*), AVG(util), "
                "MAX(util), AVG(mem_used_mb), MAX(mem_used_mb), MAX(mem_total_mb), AVG(temp_c), "
                "MAX(temp_c), AVG(power_w), MAX(power_w) FROM gpu_samples WHERE ts < ? "
                "GROUP BY h, gpu ON CONFLICT(hour, gpu) DO UPDATE SET samples=excluded.samples, "
                "util_avg=excluded.util_avg, util_max=excluded.util_max, mem_avg_mb=excluded.mem_avg_mb, "
                "mem_max_mb=excluded.mem_max_mb, mem_total_mb=excluded.mem_total_mb, "
                "temp_avg=excluded.temp_avg, temp_max=excluded.temp_max, power_avg=excluded.power_avg, "
                "power_max=excluded.power_max", (current_hour,))
            self._conn.execute("DELETE FROM gpu_samples WHERE ts < ?", (now - keep_raw_s,))
            self._conn.commit()

    # ----- llama-swap request history -------------------------------------
    def add_requests(self, rows: list[dict]) -> int:
        if not rows:
            return 0
        with self._lock:
            before = self._conn.total_changes
            self._conn.executemany(
                f"INSERT OR IGNORE INTO requests ({', '.join(REQUEST_FIELDS)}) "
                f"VALUES ({', '.join('?' * len(REQUEST_FIELDS))})",
                [tuple(r.get(k) for k in REQUEST_FIELDS) for r in rows])
            self._conn.commit()
            return self._conn.total_changes - before

    def list_requests(self, model: Optional[str] = None, since: Optional[float] = None,
                      limit: int = 300) -> list[dict]:
        where, args = [], []
        if model:
            where.append("model=?")
            args.append(model)
        if since:
            where.append("ts>=?")
            args.append(since)
        sql = "SELECT * FROM requests" + (" WHERE " + " AND ".join(where) if where else "")
        return self._q(sql + " ORDER BY ts DESC LIMIT ?", args + [limit])

    def request_stats(self, since: float) -> list[dict]:
        return self._q(
            "SELECT model, COUNT(*) AS requests, AVG(NULLIF(tokens_per_second, 0)) AS tg_avg, "
            "MAX(tokens_per_second) AS tg_max, AVG(NULLIF(prompt_per_second, 0)) AS pp_avg, "
            "SUM(input_tokens) AS input_tokens, SUM(output_tokens) AS output_tokens, "
            "SUM(CASE WHEN status >= 400 OR (error IS NOT NULL AND error != '') THEN 1 ELSE 0 END) AS errors, "
            "MAX(ts) AS last_ts FROM requests WHERE ts>=? GROUP BY model ORDER BY requests DESC", (since,))

    # ----- downloads ------------------------------------------------------
    def save_download(self, d: dict) -> None:
        self._x("INSERT INTO downloads (id, repo, files, dest, status, bytes_done, bytes_total, error, "
                "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET "
                "status=excluded.status, bytes_done=excluded.bytes_done, bytes_total=excluded.bytes_total, "
                "error=excluded.error, updated_at=excluded.updated_at",
                (d["id"], d["repo"], json.dumps(d["files"]), d["dest"], d["status"], d.get("bytes_done", 0),
                 d.get("bytes_total", 0), d.get("error", ""), d.get("created_at", time.time()), time.time()))

    def list_downloads(self, limit: int = 100) -> list[dict]:
        rows = self._q("SELECT * FROM downloads ORDER BY created_at DESC LIMIT ?", (limit,))
        for r in rows:
            r["files"] = _json(r["files"], [])
        return rows

    # ----- assistant chat -------------------------------------------------
    def add_message(self, role: str, content: str) -> int:
        return int(self._x("INSERT INTO chat_messages (ts, role, content) VALUES (?, ?, ?)",
                           (time.time(), role, content)).lastrowid or 0)

    def list_messages(self) -> list[dict]:
        return self._q("SELECT * FROM chat_messages ORDER BY id")

    def mark_compacted(self, up_to_id: int) -> None:
        self._x("UPDATE chat_messages SET compacted=1 WHERE id<=?", (up_to_id,))

    def clear_chat(self) -> None:
        self._x("DELETE FROM chat_messages")


def _json(v: Any, default: Any) -> Any:
    try:
        return json.loads(v) if v else default
    except (TypeError, ValueError):
        return default


def _decode(row: dict) -> dict:
    row["env_json"] = _json(row.get("env_json"), {})
    return row


__all__ = ["Store", "RUN_COLUMNS", "new_id"]
