"""SQLite persistence: recorded runs, saved profiles, model-file catalog and
env-var usage history.

Tables
------
runs           one row per benchmark / recorded launch (the perf sheet)
profiles       saved launch commands, reusable across models
catalog_files  MTP and mmproj (vision projector) files keyed to a base model
env_flags      last performance seen with each env var set

Databases created by the first version of this app (``mtp_models``,
``vision_mmjogs``, old ``profiles`` columns) are migrated in place on open.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from typing import Any, Optional

RUN_COLUMNS: dict[str, str] = {
    "run_id": "TEXT NOT NULL UNIQUE",
    "launch_id": "TEXT",
    "created_at": "REAL NOT NULL",
    "base_model": "TEXT NOT NULL",
    "profile_name": "TEXT",
    "launch_command": "TEXT NOT NULL",
    "env_json": "TEXT DEFAULT '{}'",
    "gguf_path": "TEXT",
    "mtp_file": "TEXT",
    "mmproj_file": "TEXT",
    "draft_model": "TEXT",
    "port": "INTEGER",
    "backend": "TEXT",
    "gpu_layers": "TEXT",
    "n_ctx": "INTEGER",
    "n_batch": "INTEGER",
    "n_ubatch": "INTEGER",
    "n_threads": "INTEGER",
    "n_parallel": "INTEGER",
    "mmap": "INTEGER",
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

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    {", ".join(f"{k} {v}" for k, v in RUN_COLUMNS.items())}
);
CREATE INDEX IF NOT EXISTS idx_runs_base ON runs (base_model);
CREATE INDEX IF NOT EXISTS idx_runs_created ON runs (created_at DESC);

CREATE TABLE IF NOT EXISTS profiles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_id TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    base_model TEXT,
    source TEXT DEFAULT '',
    launch_command TEXT NOT NULL,
    env_json TEXT DEFAULT '{{}}',
    notes TEXT DEFAULT '',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS catalog_files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL CHECK (kind IN ('mtp', 'mmproj')),
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
"""

PROFILE_FIELDS = ("name", "base_model", "source", "launch_command", "notes")


def new_id(prefix: str) -> str:
    return f"{prefix}-{int(time.time() * 1000)}-{os.urandom(2).hex()}"


class Store:
    def __init__(self, path: str):
        self.path = path
        if os.path.dirname(path):
            os.makedirs(os.path.dirname(path), exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._migrate_legacy()
            self._conn.executescript(SCHEMA)
            self._add_missing_run_columns()
            self._conn.commit()

    # ----- migrations -----------------------------------------------------
    def _tables(self) -> set[str]:
        rows = self._conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        return {r[0] for r in rows}

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
        legacy_catalog = (("mtp_models", "mtp"), ("vision_mmjogs", "mmproj"))
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

    def _add_missing_run_columns(self) -> None:
        have = self._columns("runs")
        for name, decl in RUN_COLUMNS.items():
            if name not in have:
                decl = decl.replace("NOT NULL UNIQUE", "").replace("NOT NULL", "")
                self._conn.execute(f"ALTER TABLE runs ADD COLUMN {name} {decl}")

    # ----- runs -----------------------------------------------------------
    def add_run(self, rec: dict[str, Any]) -> None:
        """Insert or replace (by run_id) a perf-sheet row; unknown keys are ignored."""
        row = {k: v for k, v in rec.items() if k in RUN_COLUMNS}
        if isinstance(row.get("env_json"), dict):
            row["env_json"] = json.dumps(row["env_json"])
        cols = ", ".join(row)
        updates = ", ".join(f"{k}=excluded.{k}" for k in row if k != "run_id")
        sql = (f"INSERT INTO runs ({cols}) VALUES ({', '.join('?' * len(row))}) "
               f"ON CONFLICT(run_id) DO UPDATE SET {updates}")
        with self._lock:
            self._conn.execute(sql, tuple(row.values()))
            self._conn.commit()

    def get_run(self, run_id: str) -> Optional[dict]:
        with self._lock:
            row = self._conn.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
        return _decode(row) if row else None

    def list_runs(self, base_model: Optional[str] = None, limit: int = 500) -> list[dict]:
        q, args = "SELECT * FROM runs", ()
        if base_model:
            q, args = q + " WHERE base_model=?", (base_model,)
        with self._lock:
            rows = self._conn.execute(q + " ORDER BY created_at DESC LIMIT ?", args + (limit,))
            return [_decode(r) for r in rows.fetchall()]

    def delete_run(self, run_id: str) -> bool:
        with self._lock:
            cur = self._conn.execute("DELETE FROM runs WHERE run_id=?", (run_id,))
            self._conn.commit()
        return cur.rowcount > 0

    def base_models(self) -> list[str]:
        q = ("SELECT base_model FROM runs UNION SELECT base_model FROM profiles "
             "UNION SELECT base_model FROM catalog_files")
        with self._lock:
            return sorted({r[0] for r in self._conn.execute(q) if r[0]}, key=str.lower)

    # ----- profiles -------------------------------------------------------
    def save_profile(self, d: dict[str, Any]) -> dict:
        """Create, or update when ``profile_id`` matches an existing profile."""
        pid = d.get("profile_id") or new_id("prof")
        vals = {k: str(d.get(k) or "").strip() for k in PROFILE_FIELDS}
        now = time.time()
        with self._lock:
            self._conn.execute(
                f"INSERT INTO profiles (profile_id, {', '.join(PROFILE_FIELDS)}, created_at, updated_at) "
                f"VALUES (?, {', '.join('?' * len(PROFILE_FIELDS))}, ?, ?) "
                f"ON CONFLICT(profile_id) DO UPDATE SET "
                f"{', '.join(f'{k}=excluded.{k}' for k in PROFILE_FIELDS)}, updated_at=excluded.updated_at",
                (pid, *vals.values(), now, now))
            self._conn.commit()
        return self.get_profile(pid)  # type: ignore[return-value]

    def get_profile(self, profile_id: str) -> Optional[dict]:
        with self._lock:
            row = self._conn.execute("SELECT * FROM profiles WHERE profile_id=?", (profile_id,)).fetchone()
        return _decode(row) if row else None

    def list_profiles(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM profiles ORDER BY updated_at DESC").fetchall()
        return [_decode(r) for r in rows]

    def delete_profile(self, profile_id: str) -> bool:
        with self._lock:
            cur = self._conn.execute("DELETE FROM profiles WHERE profile_id=?", (profile_id,))
            self._conn.commit()
        return cur.rowcount > 0

    # ----- catalog (MTP / mmproj files) ----------------------------------
    def add_file(self, kind: str, base_model: str, *, name: str = "", hf_repo: str = "",
                 file_path: str = "", notes: str = "") -> int:
        name = name or os.path.basename((file_path or hf_repo).rstrip("/")) or kind
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO catalog_files (kind, base_model, name, hf_repo, file_path, notes, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (kind, base_model, name, hf_repo, file_path, notes, time.time()))
            self._conn.commit()
        return int(cur.lastrowid or 0)

    def list_files(self, kind: Optional[str] = None, base_model: Optional[str] = None) -> list[dict]:
        q, args, where = "SELECT * FROM catalog_files", [], []
        if kind:
            where.append("kind=?")
            args.append(kind)
        if base_model:
            where.append("base_model=?")
            args.append(base_model)
        if where:
            q += " WHERE " + " AND ".join(where)
        with self._lock:
            rows = self._conn.execute(q + " ORDER BY base_model, created_at DESC", args).fetchall()
        return [dict(r) for r in rows]

    def delete_file(self, file_id: int) -> bool:
        with self._lock:
            cur = self._conn.execute("DELETE FROM catalog_files WHERE id=?", (file_id,))
            self._conn.commit()
        return cur.rowcount > 0

    # ----- env-var usage --------------------------------------------------
    def list_env_flags(self) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self._conn.execute("SELECT * FROM env_flags ORDER BY flag")]

    def set_last_used(self, flag: str, base_model: str, tps: Optional[float],
                      pps: Optional[float], run_at: float) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO env_flags (flag, last_used_for, last_tokens_per_second, "
                "last_prompt_per_second, last_run_at) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(flag) DO UPDATE SET last_used_for=excluded.last_used_for, "
                "last_tokens_per_second=excluded.last_tokens_per_second, "
                "last_prompt_per_second=excluded.last_prompt_per_second, "
                "last_run_at=excluded.last_run_at",
                (flag, base_model, tps, pps, run_at))
            self._conn.commit()


def _json(v: Any, default: Any) -> Any:
    try:
        return json.loads(v) if v else default
    except (TypeError, ValueError):
        return default


def _decode(row: sqlite3.Row) -> dict:
    d = dict(row)
    d["env_json"] = _json(d.get("env_json"), {})
    return d


__all__ = ["Store", "RUN_COLUMNS", "new_id"]
