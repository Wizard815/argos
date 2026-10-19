"""Two databases, one store.

The store was written against a SQLite file, and SQLite is not a bad database.
The problem was never the engine - it was the shape: one file, one writer at a
time, and a journal that a long read stops from checkpointing, so the log grows
and every read has to walk it. That is exactly what the container's logs showed,
as queries that took longer the longer the container had been up: 37 rows in
17.8 seconds, 296 rows in 66.

Postgres removes the shape. Readers never wait for a writer, there is no file
lock to hold, and no journal to grow. That is the whole reason for the move.

This module is the seam. It holds the handful of places where the two dialects
genuinely differ - placeholders, a time bucket, introspection, a row count - and
nothing else, so the rest of the store stays ordinary SQL that both understand.
Everything here is string work and introspection: it needs no server to test,
which is the point. ``translate`` and ``schema_for_postgres`` are pure functions
and are covered by tests/test_dialect.py.
"""

from __future__ import annotations

import os
import re
from typing import Any, Iterable, Optional

__all__ = ["url", "is_postgres", "translate", "schema_for_postgres", "connect",
           "tables", "columns", "statement_count"]


# ---------------------------------------------------------------- choosing one

def url() -> str:
    """The database to use.

    ``ARGOS_DATABASE_URL`` wins. With it unset the builder keeps using the SQLite
    file under /data, so a deployment can move across one piece at a time instead
    of in a flag day - and can fall back if the move goes wrong.
    """
    return (os.environ.get("ARGOS_DATABASE_URL") or "").strip()


def is_postgres(dsn: str) -> bool:
    return (dsn or "").startswith(("postgres://", "postgresql://"))


# ------------------------------------------------------------- translating SQL

# The three time-bucket queries in the store. SQLite's CAST(x AS INTEGER)
# truncates toward zero; Postgres's CAST rounds, so 1.6 would land in bucket 2
# and every chart would be a bucket out. floor() is the truncation both mean.
_CAST_INT = re.compile(r"CAST\(\s*([^()]*?)\s+AS\s+INTEGER\s*\)", re.IGNORECASE)
_AUTOINC = re.compile(r"\bINTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT\b", re.IGNORECASE)
_INTEGER = re.compile(r"\bINTEGER\b")
_REAL = re.compile(r"\bREAL\b")
_BLOB = re.compile(r"\bBLOB\b")
_COLLATE_NOCASE = re.compile(r"\s+COLLATE\s+NOCASE\b", re.IGNORECASE)
_INSERT_OR_IGNORE = re.compile(r"^\s*INSERT\s+OR\s+IGNORE\b", re.IGNORECASE)
_INSERT_OR_REPLACE = re.compile(r"^\s*INSERT\s+OR\s+REPLACE\b", re.IGNORECASE)


def _placeholders(sql: str) -> str:
    """? to %s, and every % to %%, walking the string once.

    The two are not symmetric. A '?' is only a placeholder outside a literal - a
    question mark in a note body is data, and rewriting it would corrupt the row.
    A '%' is different: psycopg counts the ones inside literals too, so
    ``SELECT 'a%b' WHERE x=%s`` raises "two placeholders but one parameter" on
    the real server (verified against 3.3.6). Every % therefore doubles,
    including in literals, and psycopg puts them back.
    """
    parts: list[str] = []
    i, n = 0, len(sql)
    while i < n:
        ch = sql[i]
        if ch == "'":
            j = i + 1
            body: list[str] = []
            while j < n:
                if sql[j] == "'":
                    if j + 1 < n and sql[j + 1] == "'":
                        body.append("''")
                        j += 2
                        continue
                    break
                body.append("%%" if sql[j] == "%" else sql[j])
                j += 1
            parts.append("'" + "".join(body) + "'")
            i = j + 1
            continue
        if ch == "?":
            parts.append("%s")
        elif ch == "%":
            parts.append("%%")
        else:
            parts.append(ch)
        i += 1
    return "".join(parts)


def translate(sql: str, params: bool = True) -> str:
    """The SQLite SQL, in Postgres.

    Placeholders become ``%s``. A literal ``%`` becomes ``%%``, because psycopg
    reads an unescaped one as the start of a placeholder the moment arguments are
    passed - a LIKE '%x%' would otherwise raise. Both happen outside string
    literals only.
    """
    sql = _placeholders(sql)

    # Upserts. Both dialects spell "ignore a duplicate" differently; the conflict
    # target is what makes the Postgres form legal.
    if _INSERT_OR_IGNORE.match(sql):
        sql = _INSERT_OR_IGNORE.sub("INSERT", sql)
        if "ON CONFLICT" not in sql.upper():
            sql = sql.rstrip().rstrip(";") + " ON CONFLICT DO NOTHING"
    elif _INSERT_OR_REPLACE.match(sql):
        sql = _INSERT_OR_REPLACE.sub("INSERT", sql)

    # CASE NOCASE is SQLite's case-insensitive collation. Postgres has no such
    # collation to name; ORDER BY lower(name) is the portable spelling, and the
    # store uses that instead.
    sql = _COLLATE_NOCASE.sub("", sql)

    # A time bucket. Before the plain INTEGER rule, or it would eat the CAST.
    sql = _CAST_INT.sub(r"floor(\1)::bigint", sql)

    return sql


def schema_for_postgres(sqlite_schema: str) -> str:
    """The schema, as Postgres DDL.

    Column types are widened to what Postgres actually has: an identity column
    instead of AUTOINCREMENT, double precision instead of REAL, bytea for the
    embedding vector. TEXT, CHECK, UNIQUE, the composite primary key and
    CREATE ... IF NOT EXISTS are the same in both and are left alone.
    """
    sql = _AUTOINC.sub("bigint GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY", sqlite_schema)
    sql = _CAST_INT.sub(r"floor(\1)::bigint", sql)
    sql = _INTEGER.sub("bigint", sql)
    sql = _REAL.sub("double precision", sql)
    sql = _BLOB.sub("bytea", sql)
    return sql


def statement_count(sql: str) -> int:
    """How many statements a script holds. Used by the migration's dry run."""
    return len([s for s in sql.split(";") if s.strip()])


# --------------------------------------------------------------- the connection

def connect(dsn: str):
    """A psycopg connection behind the sqlite3 surface the store already uses.

    The store calls ``conn.execute(sql, args)``, ``conn.executemany``,
    ``conn.executescript``, ``conn.commit()`` and reads ``cursor.rowcount``.
    Wrapping psycopg to answer those keeps every one of the store's ~1000 lines
    of SQL as it is.
    """
    import psycopg
    from psycopg.rows import dict_row

    conn = psycopg.connect(dsn, row_factory=dict_row, autocommit=False,
                           connect_timeout=10)
    # ARGOS_PG_SCHEMA keeps this store's tables out of public. The suite uses it to
    # give each test a schema of its own; a deployment could use it to share one
    # server between instances. Quoted, because it is an identifier.
    schema = (os.environ.get("ARGOS_PG_SCHEMA") or "").strip()
    if schema:
        if not schema.replace("_", "").isalnum():
            raise ValueError("ARGOS_PG_SCHEMA must be alphanumeric: " + schema)
        with conn.cursor() as cur:
            cur.execute('SET search_path TO "' + schema + '"')
        conn.commit()
    return PgConn(conn)


class PgCursor:
    """What ``_x`` hands back. ``lastrowid`` is gone on purpose: Postgres has no
    rowid, so the store asks with RETURNING id and reads it from the row."""

    def __init__(self, cur: Any, rowcount: int = -1):
        self._cur = cur
        self._rowcount = rowcount

    def fetchall(self):
        return self._cur.fetchall()

    def fetchone(self):
        return self._cur.fetchone()

    @property
    def rowcount(self) -> int:
        return self._cur.rowcount if self._rowcount < 0 else self._rowcount


class PgConn:
    """A psycopg connection wearing sqlite3's clothes."""

    def __init__(self, conn: Any):
        self._conn = conn

    def execute(self, sql: str, args: Iterable = ()):
        args = tuple(args)
        cur = self._conn.execute(translate(sql, bool(args)), args or None)
        return PgCursor(cur)

    def executemany(self, sql: str, seq: Iterable):
        seq = [tuple(s) for s in seq]
        if not seq:
            return PgCursor(self._conn.cursor(), 0)
        with self._conn.cursor() as cur:
            cur.executemany(translate(sql, True), seq)
            return PgCursor(cur, len(seq))

    def executescript(self, sql: str):
        """A schema. psycopg has no executescript, so run the statements."""
        with self._conn.cursor() as cur:
            for stmt in schema_for_postgres(sql).split(";"):
                if stmt.strip():
                    cur.execute(stmt)

    def commit(self) -> None:
        self._conn.commit()

    def rollback(self) -> None:
        self._conn.rollback()

    def close(self) -> None:
        self._conn.close()


# ------------------------------------------------------------- introspection

def tables(conn: Any, pg: bool) -> dict[str, str]:
    """Table name -> the text of its CHECK constraints.

    The store reads that text to tell one schema generation from the next (v1
    allowed a 'mtp' catalog kind, v2 does not). SQLite keeps the whole CREATE
    statement in sqlite_master; Postgres keeps constraints separately, so this
    rebuilds the part that is actually read.
    """
    if pg:
        rows = conn.execute(
            "SELECT c.relname AS name, COALESCE(("
            "  SELECT string_agg(pg_get_constraintdef(con.oid), ' ') "
            "  FROM pg_constraint con WHERE con.conrelid = c.oid AND con.contype = 'c'"
            "), '') AS sql "
            "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = current_schema() AND c.relkind = 'r'").fetchall()
        return {r["name"]: r["sql"] or "" for r in rows}
    rows = conn.execute("SELECT name, sql FROM sqlite_master WHERE type='table'").fetchall()
    return {r["name"]: r["sql"] or "" for r in rows}


def columns(conn: Any, table: str, pg: bool) -> set[str]:
    if pg:
        rows = conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = current_schema() AND table_name = ?", (table,)).fetchall()
        return {r["column_name"] for r in rows}
    return {r[1] for r in conn.execute("PRAGMA table_info(" + table + ")")}
