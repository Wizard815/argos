"""SQLite persistence for performance history and model-file catalogs.

Mirrors the fields llama-swap-rescan's activity schema tracks so that results
recorded here can later be replayed / matched against real runs:
  tokens_per_second, prompt_per_second, cache_tokens, draft_tokens,
  input_tokens, output_tokens, duration_ms.

Tables
------
runs            one row per launched profile attempt (the big perf-table rows)
mtp_models      catalog of custom .mtp files keyed to a base model
vision_mmjogs   catalog of vision projector (.mmjpg/.gguf) files keyed to base
env_flags       curated env-var reference library + last-used performance notes
profiles        reusable profile definitions (for copy/paste across models)
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Optional


SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL UNIQUE,
    created_at REAL NOT NULL,
    base_model TEXT NOT NULL,
    gguf_path TEXT,
    mtp_file TEXT,
    mmjog_file TEXT,
    launch_command TEXT NOT NULL,
    port INTEGER,
    backend TEXT,
    gpu_layers INTEGER DEFAULT -1,
    n_ctx INTEGER DEFAULT 4096,
    n_batch INTEGER,
    n_ubatch INTEGER,
    n_threads INTEGER,
    n_predict INTEGER,
    load_mode TEXT,
    kv_size TEXT,
    mmap INTEGER,
    flash_attention INTEGER,
    rope_freq_scale REAL,
    rope_freq_std REAL,
    head_pad INTEGER,
    custom_env_json TEXT DEFAULT '{}',
    tokens_per_second REAL,
    prompt_per_second REAL,
    input_tokens INTEGER,
    output_tokens INTEGER,
    cache_tokens INTEGER DEFAULT 0,
    draft_tokens INTEGER DEFAULT 0,
    draft_acc REAL DEFAULT 0,
    duration_ms INTEGER,
    gpu_util_real REAL,
    mem_used_gb REAL,
    temp_c REAL,
    note TEXT,
    log_source TEXT,
    success INTEGER DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_runs_base ON runs (base_model);
CREATE INDEX IF NOT EXISTS idxRunsCreated ON runs (created_at DESC);

CREATE TABLE IF NOT EXISTS mtp_models (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    model_id TEXT NOT NULL,
    name TEXT NOT NULL,
    hf_repo TEXT,
    file_path TEXT,
    base_model TEXT,
    created_at REAL NOT NULL,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS vision_mmjogs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    model_id TEXT NOT NULL,
    name TEXT NOT NULL,
    type TEXT CHECK(type IN ('mmjog','gguf')),
    hf_repo TEXT,
    file_path TEXT,
    base_model TEXT,
    modalities TEXT DEFAULT 'image',
    created_at REAL NOT NULL,
    notes TEXT
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

CREATE TABLE IF NOT EXISTS profiles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_id TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    source_model TEXT NOT NULL,
    copied_into TEXT,
    launch_command TEXT NOT NULL,
    env_json TEXT DEFAULT '{}',
    flags_json TEXT DEFAULT '[]',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
"""


class Store:
    def __init__(self, path: Optional[str] = None):
        if path is None:
            path = os.environ.get(
                "LLAMA_PROFILE_DB",
                os.path.join(os.path.dirname(os.path.dirname(__file__)), "data"),
            )
            db_file = os.path.join(path, "profile_builder.db")
        else:
            db_file = path
        self.db_file = db_file
        os.makedirs(os.path.dirname(db_file), exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(db_file, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    @staticmethod
    def new_run_id() -> str:
        return f"run-{int(time.time()*1000)}-{os.urandom(2).hex()}"

    # ----- runs / performance history -------------------------------------
    def add_run(self, r: dict[str, Any]) -> int:
        with self._lock:
            cur = self._conn.execute("SELECT id FROM runs WHERE run_id=?", (r["run_id"],))
            row = cur.fetchone()
            if row:
                sql = ("UPDATE runs SET created_at=?, base_model=?, gguf_path=?, mtp_file=?, "
                       "mmjog_file=?, launch_command=?, port=?, backend=?, gpu_layers=?, n_ctx=?, "
                       "n_batch=?, n_ubatch=?, n_threads=?, n_predict=?, load_mode=?, kv_size=?, "
                       "mmap=?, flash_attention=?, rope_freq_scale=?, rope_freq_std=?, head_pad=?, "
                       "custom_env_json=?, tokens_per_second=?, prompt_per_second=?, input_tokens=?, "
                       "output_tokens=?, cache_tokens=?, draft_tokens=?, draft_acc=?, duration_ms=?, "
                       "gpu_util_real=?, mem_used_gb=?, temp_c=?, note=?, log_source=?, success=? "
                       "WHERE id=?")
                vals = tuple(r[k] for k in [
                    "created_at","base_model","gguf_path","mtp_file","mmjog_file","launch_command",
                    "port","backend","gpu_layers","n_ctx","n_batch","n_ubatch","n_threads","n_predict",
                    "load_mode","kv_size","mmap","flash_attention","rope_freq_scale","rope_freq_std",
                    "head_pad","custom_env_json","tokens_per_second","prompt_per_second","input_tokens",
                    "output_tokens","cache_tokens","draft_tokens","draft_acc","duration_ms",
                    "gpu_util_real","mem_used_gb","temp_c","note","log_source","success"]) + (r["run_id"],)
                self._conn.execute(sql, vals)
            else:
                cols = ",".join(r.keys())
                ph = ",".join("?"*len(r))
                self._conn.execute(f"INSERT INTO runs ({cols}) VALUES ({ph})", tuple(r.values()))
            self._conn.commit()
        return r.get("id") or 0

    def get_run(self, run_id: str) -> Optional[dict]:
        with self._lock:
            cur = self._conn.execute("SELECT * FROM runs WHERE run_id=?", (run_id,))
            row = cur.fetchone()
        return dict(row) if row else None

    def list_runs(self, base_model: Optional[str] = None, limit: int = 200) -> list[dict]:
        q = "SELECT * FROM runs"
        args: Any = ()
        if base_model:
            q += " WHERE base_model=?"
            args = (base_model,)
        q += " ORDER BY created_at DESC LIMIT ?"
        with self._lock:
            cur = self._conn.execute(q, args + (limit,))
            return [dict(x) for x in cur.fetchall()]

    def recorded_for(self, base_model: str) -> list[dict]:
        """All runs for a base model, newest first."""
        return self.list_runs(base_model=base_model, limit=10_000)

    def delete_run(self, run_id: str) -> bool:
        with self._lock:
            cur = self._conn.execute("DELETE FROM runs WHERE run_id=?", (run_id,))
            self._conn.commit()
            return cur.rowcount > 0

    # ----- MTP catalog ----------------------------------------------------
    def add_mtp(self, d: dict[str, Any]) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO mtp_models (model_id,name,hf_repo,file_path,base_model,created_at,notes) "
                "VALUES (?,?,?,?,?,?,?)",
                (d["model_id"], d["name"], d.get("hf_repo"), d.get("file_path"),
                 d.get("base_model"), time.time(), d.get("notes","")))
            self._conn.commit()
        return cur.lastrowid

    def list_mtp(self, base_model: Optional[str] = None) -> list[dict]:
        q = "SELECT * FROM mtp_models"
        args: Any = ()
        if base_model:
            q += " WHERE base_model=?"
            args = (base_model,)
        q += " ORDER BY created_at DESC"
        with self._lock:
            return [dict(x) for x in self._conn.execute(q, args).fetchall()]

    def delete_mtp(self, mid: int) -> bool:
        with self._lock:
            cur = self._conn.execute("DELETE FROM mtp_models WHERE id=?", (mid,))
            self._conn.commit()
            return cur.rowcount > 0

    # ----- vision / mmjog catalog ----------------------------------------
    def add_mmjog(self, d: dict[str, Any]) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO vision_mmjogs (model_id,name,type,hf_repo,file_path,base_model,modalities,created_at,notes) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (d["model_id"], d["name"], d.get("type","mmjog"), d.get("hf_repo"),
                 d.get("file_path"), d.get("base_model"), d.get("modalities","image"),
                 time.time(), d.get("notes","")))
            self._conn.commit()
        return cur.lastrowid

    def list_mmjogs(self, base_model: Optional[str] = None) -> list[dict]:
        q = "SELECT * FROM vision_mmjogs"
        args: Any = ()
        if base_model:
            q += " WHERE base_model=?"
            args = (base_model,)
        q += " ORDER BY created_at DESC"
        with self._lock:
            return [dict(x) for x in self._conn.execute(q, args).fetchall()]

    def delete_mmjog(self, mid: int) -> bool:
        with self._lock:
            cur = self._conn.execute("DELETE FROM vision_mmjogs WHERE id=?", (mid,))
            self._conn.commit()
            return cur.rowcount > 0

    # ----- env-var / launch-command reference table ----------------------
    def upsert_env_flag(self, d: dict[str, Any]) -> int:
        cols = ["flag","category","description","recommended","gpu_target","note"]
        vals = [d[c] for c in cols if d.get(c) is not None]
        ph = ",".join("?"*len(cols))
        unique = ", ".join(cols + ["id"])
        update_cols = [f"{c}=excluded.{c}" for c in cols]
        sql = (f"INSERT INTO env_flags ({','.join(cols)}) VALUES ({ph}) "
               f"ON CONFLICT(flag) DO UPDATE SET {', '.join(update_cols)}")
        with self._lock:
            cur = self._conn.execute(sql, vals)
            self._conn.commit()
        return cur.lastrowid or -1

    def list_env_flags(self) -> list[dict]:
        with self._lock:
            cur = self._conn.execute(
                "SELECT * FROM env_flags ORDER BY category, flag")
            return [dict(x) for x in cur.fetchall()]

    def set_last_used(self, flag: str, tps: Optional[float], pps: Optional[float], run_at: float) -> None:
        # Upsert so library entries that were never added via add_flag still record usage.
        with self._lock:
            exists = self._conn.execute(
                "SELECT id FROM env_flags WHERE flag=?", (flag,)).fetchone()
            if exists is None:
                self._conn.execute(
                    "INSERT INTO env_flags (flag) VALUES (?)", (flag,))
            self._conn.execute(
                "UPDATE env_flags SET last_used_for=?, last_tokens_per_second=?, "
                "last_prompt_per_second=?, last_run_at=? WHERE flag=?",
                (flag, tps, pps, run_at, flag))
            self._conn.commit()

    # ----- profiles (copy/paste across models) ---------------------------
    def save_profile(self, d: dict[str, Any]) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO profiles (profile_id,name,source_model,copied_into,launch_command,"
                "env_json,flags_json,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (d["profile_id"], d["name"], d["source_model"], d.get("copied_into"),
                 d["launch_command"], json.dumps(d.get("env_json",{})),
                 json.dumps(d.get("flags_json",[])), time.time(), time.time()))
            self._conn.commit()
        return cur.lastrowid

    def list_profiles(self) -> list[dict]:
        with self._lock:
            cur = self._conn.execute("SELECT * FROM profiles ORDER BY updated_at DESC")
            rows = [dict(x) for x in cur.fetchall()]
            for r in rows:
                r["env_json"] = json.loads(r.get("env_json") or "{}")
                r["flags_json"] = json.loads(r.get("flags_json") or "[]")
            return rows

    def delete_profile(self, profile_id: str) -> bool:
        with self._lock:
            cur = self._conn.execute("DELETE FROM profiles WHERE profile_id=?", (profile_id,))
            self._conn.commit()
            return cur.rowcount > 0
