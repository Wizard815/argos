import sqlite3
import time

import pytest

from src import dialect
from src.db import Store


def integrity_error():
    """The error a CHECK constraint raises, whichever database it is.

    psycopg's CheckViolation and sqlite3's IntegrityError are the same failure
    under different names; both subclass their driver's IntegrityError.
    """
    if dialect.is_postgres(dialect.url()):
        import psycopg

        return psycopg.errors.IntegrityError
    return sqlite3.IntegrityError


# Upgrading an old SQLite file in place is a thing only a SQLite file does: a
# Postgres database is created with the current schema and has no v1 or v2 to
# migrate from.
needs_sqlite = pytest.mark.skipif(
    dialect.is_postgres(dialect.url()),
    reason="there is no old SQLite file to upgrade inside a Postgres database")


def test_runs_upsert_list_delete(tmp_path):
    s = Store(str(tmp_path / "a.db"))
    base = {"run_id": "r1", "created_at": time.time(), "base_model": "M", "launch_command": "x",
            "env_json": {"A": "1"}, "tokens_per_second": 10.0, "not_a_column": 1}
    s.add_run(base)
    s.add_run({**base, "tokens_per_second": 12.5})
    s.add_run({**base, "run_id": "r2", "base_model": "N", "created_at": time.time() + 1})
    runs = s.list_runs()
    assert [r["run_id"] for r in runs] == ["r2", "r1"]
    assert s.get_run("r1")["tokens_per_second"] == 12.5
    assert s.get_run("r1")["env_json"] == {"A": "1"}
    assert [r["run_id"] for r in s.list_runs("M")] == ["r1"]
    assert s.delete_run("r1") and not s.delete_run("r1")


def test_profiles_create_update_delete(tmp_path):
    s = Store(str(tmp_path / "a.db"))
    p = s.save_profile({"name": "fast", "base_model": "M", "launch_command": "llama-server -m a"})
    assert p["profile_id"].startswith("prof-")
    p2 = s.save_profile({"profile_id": p["profile_id"], "name": "faster", "launch_command": "llama-server -m b"})
    assert p2["profile_id"] == p["profile_id"] and p2["name"] == "faster"
    assert len(s.list_profiles()) == 1
    assert s.base_models() == []  # base_model was cleared by the update
    assert s.delete_profile(p["profile_id"])
    assert s.list_profiles() == []


def test_catalog(tmp_path):
    s = Store(str(tmp_path / "a.db"))
    fid = s.add_file("mmproj", "gemma", file_path="/m/mmproj-gemma.gguf")
    s.add_file("draft", "qwen", hf_repo="org/qwen-draft")
    assert s.list_files("mmproj")[0]["name"] == "mmproj-gemma.gguf"
    assert s.list_files(base_model="qwen")[0]["name"] == "qwen-draft"
    assert s.base_models() == ["gemma", "qwen"]
    with pytest.raises(integrity_error()):
        s.add_file("lora", "x", file_path="y")
    assert s.delete_file(fid) and not s.delete_file(fid)


def test_env_usage_upsert(tmp_path):
    s = Store(str(tmp_path / "a.db"))
    s.set_last_used("CUDA_VISIBLE_DEVICES", "M", 50.0, 900.0, 1.0)
    s.set_last_used("CUDA_VISIBLE_DEVICES", "N", 60.0, 1000.0, 2.0)
    (row,) = s.list_env_flags()
    assert (row["last_used_for"], row["last_tokens_per_second"], row["last_run_at"]) == ("N", 60.0, 2.0)


LEGACY_SCHEMA = """
CREATE TABLE runs (id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL UNIQUE, created_at REAL NOT NULL,
  base_model TEXT NOT NULL, gguf_path TEXT, mtp_file TEXT, mmjog_file TEXT, launch_command TEXT NOT NULL,
  port INTEGER, backend TEXT, gpu_layers INTEGER DEFAULT -1, n_ctx INTEGER DEFAULT 4096, n_batch INTEGER,
  n_ubatch INTEGER, n_threads INTEGER, n_predict INTEGER, load_mode TEXT, kv_size TEXT, mmap INTEGER,
  flash_attention INTEGER, rope_freq_scale REAL, rope_freq_std REAL, head_pad INTEGER,
  custom_env_json TEXT DEFAULT '{}', tokens_per_second REAL, prompt_per_second REAL, input_tokens INTEGER,
  output_tokens INTEGER, cache_tokens INTEGER DEFAULT 0, draft_tokens INTEGER DEFAULT 0, draft_acc REAL DEFAULT 0,
  duration_ms INTEGER, gpu_util_real REAL, mem_used_gb REAL, temp_c REAL, note TEXT, log_source TEXT,
  success INTEGER DEFAULT 1);
CREATE TABLE mtp_models (id INTEGER PRIMARY KEY AUTOINCREMENT, model_id TEXT NOT NULL, name TEXT NOT NULL,
  hf_repo TEXT, file_path TEXT, base_model TEXT, created_at REAL NOT NULL, notes TEXT);
CREATE TABLE vision_mmjogs (id INTEGER PRIMARY KEY AUTOINCREMENT, model_id TEXT NOT NULL, name TEXT NOT NULL,
  type TEXT CHECK(type IN ('mmjog','gguf')), hf_repo TEXT, file_path TEXT, base_model TEXT,
  modalities TEXT DEFAULT 'image', created_at REAL NOT NULL, notes TEXT);
CREATE TABLE env_flags (id INTEGER PRIMARY KEY AUTOINCREMENT, flag TEXT NOT NULL UNIQUE, category TEXT,
  description TEXT, recommended TEXT, gpu_target TEXT, last_used_for TEXT, last_tokens_per_second REAL,
  last_prompt_per_second REAL, last_run_at REAL, note TEXT);
CREATE TABLE profiles (id INTEGER PRIMARY KEY AUTOINCREMENT, profile_id TEXT NOT NULL UNIQUE, name TEXT NOT NULL,
  source_model TEXT NOT NULL, copied_into TEXT, launch_command TEXT NOT NULL, env_json TEXT DEFAULT '{}',
  flags_json TEXT DEFAULT '[]', created_at REAL NOT NULL, updated_at REAL NOT NULL);
INSERT INTO runs (run_id, created_at, base_model, launch_command, mmjog_file, custom_env_json, gpu_util_real,
  tokens_per_second) VALUES ('old-1', 1, 'M', 'llama-server -m a', '/m/mm.gguf', '{"X":"1"}', 0.5, 33.0);
INSERT INTO mtp_models (model_id, name, file_path, base_model, created_at) VALUES ('q', 'q.mtp', '/m/q.mtp', 'qwen', 1);
INSERT INTO vision_mmjogs (model_id, name, type, file_path, base_model, created_at)
  VALUES ('g', 'mm', 'mmjog', '/m/mm.gguf', NULL, 1);
INSERT INTO profiles (profile_id, name, source_model, copied_into, launch_command, created_at, updated_at)
  VALUES ('prof-1', 'old', 'odysseus', 'qwen', 'llama-server -m q', 1, 1);
"""


@needs_sqlite
def test_migrates_first_version_database(tmp_path):
    path = str(tmp_path / "legacy.db")
    con = sqlite3.connect(path)
    con.executescript(LEGACY_SCHEMA)
    con.commit()
    con.close()

    s = Store(path)
    run = s.get_run("old-1")
    assert run["mmproj_file"] == "/m/mm.gguf"
    assert run["env_json"] == {"X": "1"}
    assert run["gpu_util"] == 0.5
    s.add_run({"run_id": "new-1", "created_at": 2, "base_model": "M", "launch_command": "x",
               "layers_offloaded": "33/33", "cache_type_k": "q8_0"})
    assert s.get_run("new-1")["layers_offloaded"] == "33/33"

    files = {(f["kind"], f["base_model"], f["file_path"]) for f in s.list_files()}
    assert files == {("draft", "qwen", "/m/q.mtp"), ("mmproj", "g", "/m/mm.gguf")}
    (p,) = s.list_profiles()
    assert (p["profile_id"], p["base_model"], p["source"]) == ("prof-1", "qwen", "odysseus")

    Store(path)  # reopening a migrated database is a no-op


@needs_sqlite
def test_migrates_v2_catalog_mtp_rows_to_draft(tmp_path):
    path = str(tmp_path / "v2.db")
    con = sqlite3.connect(path)
    con.executescript("""
CREATE TABLE catalog_files (id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL CHECK (kind IN ('mtp', 'mmproj')), base_model TEXT NOT NULL, name TEXT NOT NULL,
  hf_repo TEXT DEFAULT '', file_path TEXT DEFAULT '', notes TEXT DEFAULT '', created_at REAL NOT NULL);
INSERT INTO catalog_files (kind, base_model, name, file_path, created_at) VALUES ('mtp', 'q', 'd', '/m/d.gguf', 1);
CREATE TABLE profiles (id INTEGER PRIMARY KEY AUTOINCREMENT, profile_id TEXT NOT NULL UNIQUE, name TEXT NOT NULL,
  base_model TEXT, source TEXT DEFAULT '', launch_command TEXT NOT NULL, env_json TEXT DEFAULT '{}',
  notes TEXT DEFAULT '', created_at REAL NOT NULL, updated_at REAL NOT NULL);
INSERT INTO profiles (profile_id, name, launch_command, created_at, updated_at) VALUES ('p1', 'x', 'llama-server', 1, 1);
""")
    con.commit()
    con.close()
    s = Store(path)
    assert [(f["kind"], f["file_path"]) for f in s.list_files()] == [("draft", "/m/d.gguf")]
    p = s.get_profile("p1")
    assert p["favorite"] == 0 and p["model_id"] is None  # new columns added
    s.add_file("draft", "q", file_path="/m/e.gguf")
