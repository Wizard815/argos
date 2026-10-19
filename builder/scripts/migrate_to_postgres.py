#!/usr/bin/env python3
"""Copy every table from the SQLite file into Postgres.

Run it once, after starting the postgres service and before setting
ARGOS_DATABASE_URL on the app. It does not touch the SQLite file - it only reads
- so the old database stays as the fallback until you are satisfied.

    python scripts/migrate_to_postgres.py \
        --sqlite /data/builder.db \
        --postgres postgresql://argos:***@127.0.0.1:5432/argos

The row count of every table is compared at the end, and the script exits
non-zero if any table came across short. Re-running it is safe: rows already
there are skipped, so a half-finished run can be repeated.

Ids are copied as they were, and each table's identity sequence is then moved
past the highest id. Without that the first new row after the move would try to
reuse an id and fail.
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from src import dialect  # noqa: E402
from src.db import SCHEMA  # noqa: E402

BATCH = 500
# SQLite's own bookkeeping, and tables this store no longer has.
SKIP = {"sqlite_sequence", "sqlite_stat1", "note_versions", "profiles_legacy", "catalog_v2", "catalog_v1"}


def tables(src: sqlite3.Connection) -> list[str]:
    rows = src.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    return [r[0] for r in rows if r[0] not in SKIP and not r[0].startswith("sqlite_")]


def columns(src: sqlite3.Connection, table: str) -> list[str]:
    return [r[1] for r in src.execute('PRAGMA table_info("' + table + '")')]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--sqlite", default=os.environ.get("ARGOS_BUILDER_DB", "/data/builder.db"))
    ap.add_argument("--postgres", default=dialect.url())
    ap.add_argument("--dry-run", action="store_true", help="report what would move, write nothing")
    a = ap.parse_args()

    if not dialect.is_postgres(a.postgres):
        print("--postgres must be a postgresql:// URL (or set ARGOS_DATABASE_URL)", file=sys.stderr)
        return 2
    if not os.path.exists(a.sqlite):
        print("no such SQLite file: " + a.sqlite, file=sys.stderr)
        return 2

    src = sqlite3.connect(a.sqlite)
    src.row_factory = sqlite3.Row
    names = tables(src)
    plan = [(t, columns(src, t), src.execute('SELECT COUNT(*) FROM "' + t + '"').fetchone()[0]) for t in names]

    print("from " + a.sqlite + " to " + a.postgres)
    for t, cols, n in plan:
        print("  %-18s %6d rows  %d columns" % (t, n, len(cols)))
    if a.dry_run:
        print("dry run: nothing written")
        return 0

    dst = dialect.connect(a.postgres)
    # The schema. IF NOT EXISTS throughout, so this is safe on a database that
    # already has some of it.
    dst.executescript(SCHEMA)
    dst.commit()

    total = 0
    short: list[str] = []
    for t, cols, n in plan:
        if not n:
            continue
        existing = dst.execute('SELECT COUNT(*) AS n FROM "' + t + '"').fetchone()["n"]
        if existing and existing >= n:
            print("  %-18s already has %d rows, skipping" % (t, existing))
            continue
        # BY DEFAULT AS IDENTITY, so explicit ids are accepted: the rows keep the
        # ids that other tables refer to.
        sql = ('INSERT INTO "' + t + '" (' + ", ".join('"' + c + '"' for c in cols) + ") VALUES ("
               + ", ".join("?" * len(cols)) + ") ON CONFLICT DO NOTHING")
        moved = 0
        batch: list[tuple] = []
        for row in src.execute('SELECT ' + ", ".join('"' + c + '"' for c in cols) + ' FROM "' + t + '"'):
            batch.append(tuple(row))
            if len(batch) >= BATCH:
                dst.executemany(sql, batch)
                dst.commit()
                moved += len(batch)
                batch = []
        if batch:
            dst.executemany(sql, batch)
            dst.commit()
            moved += len(batch)
        total += moved
        print("  %-18s %6d moved" % (t, moved))

    # Ids were copied, so the sequence behind each table still starts at 1 and the
    # next insert would collide. Move it past the highest id in use.
    for t, cols, _n in plan:
        if "id" not in cols:
            continue
        try:
            # downloads and models_seen key on TEXT, so they have no identity
            # column and nothing to advance. Asking first keeps a NULL sequence
            # out of setval, which would abort the transaction and take every
            # later table's sequence down with it.
            seq = dst.execute("SELECT pg_get_serial_sequence(?, 'id') AS s", (t,)).fetchone()["s"]
            if not seq:
                continue
            dst.execute('SELECT setval(?, COALESCE((SELECT MAX(id) FROM "' + t + '"), 1))', (seq,))
            dst.commit()
        except Exception as exc:  # one table's sequence is not worth the run
            dst.rollback()
            print("  %-18s sequence left alone (%s)" % (t, str(exc).splitlines()[0][:60]))
    dst.commit()

    # Say what actually arrived rather than what was sent.
    print("\nverifying:")
    for t, _cols, n in plan:
        got = dst.execute('SELECT COUNT(*) AS n FROM "' + t + '"').fetchone()["n"]
        flag = "ok" if got >= n else "SHORT"
        if got < n:
            short.append(t)
        print("  %-18s source %6d  postgres %6d  %s" % (t, n, got, flag))

    if short:
        print("\n" + ", ".join(short) + " came across short; re-run to fill them in", file=sys.stderr)
        return 1
    print("\n%d rows moved. Set ARGOS_DATABASE_URL and restart the container." % total)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
