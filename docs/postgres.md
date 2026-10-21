# Postgres

Argos runs on either a SQLite file (the default) or Postgres. `ARGOS_DATABASE_URL` picks: set it and Postgres is used, leave it unset and the file under `./data` is.

## Why you would

SQLite is not a bad database, but it has one shape: a single file, one writer at a time, and a journal that a long-running read stops from checkpointing. When the journal grows, every read has to walk it, and queries that took microseconds start taking seconds — and get worse the longer the process runs. Postgres has none of that. Readers never wait for a writer, there is no file lock, and there is no journal to grow.

## How the two are kept in step

`builder/src/dialect.py` is the whole seam. It holds the handful of places the dialects genuinely differ, so the store's ~1000 lines of SQL stay put:

| | SQLite | Postgres |
|---|---|---|
| placeholders | `?` | `%s` |
| a literal `%` | `%` | `%%` — psycopg counts one inside a string too |
| new row's id | `cursor.lastrowid` | `RETURNING id` |
| a time bucket | `CAST(ts / ? AS INTEGER)` | `floor(ts / ?)::bigint` — SQLite truncates, Postgres rounds |
| `SUM(a > 0)` | counts as 0/1 | `SUM(CASE WHEN a > 0 THEN 1 ELSE 0 END)` |
| case-insensitive sort | `COLLATE NOCASE` | `lower(name)` |
| schema | `AUTOINCREMENT`, `REAL`, `BLOB` | identity, `double precision`, `bytea` |
| introspection | `sqlite_master`, `PRAGMA table_info` | `pg_class`, `information_schema` |

The translations are pure functions and are tested without a server in `tests/test_dialect.py`. The rest of the differences are in the store itself, written portably.

## Things that bite

- **A failed statement poisons its connection.** Postgres refuses every later statement on a connection until the failed one is rolled back. `Store._abort()` does that, so one bad write cannot take a thread's connection down for good.
- **Read rows by name, never by position.** `sqlite3.Row` answers `row[0]`; psycopg's `dict_row` answers `row["col"]` and only that.
- **Columns have types.** SQLite will put a string in an INTEGER column; Postgres will not.
- **Close your connections.** A connection left open holds its transaction and the locks in it, so a schema drop or an ALTER waits forever. `Store.close()` exists for that.
- **Upgrading an old SQLite file is a SQLite thing.** Those tests are skipped under Postgres — there is no v1 or v2 schema inside a fresh Postgres database.

## The command that runs it

```sh
docker compose exec argos argos-migrate
```

It is a small script in the image that calls the real one with the venv's
interpreter. The indirection exists for a reason: this image has no system
Python — uv brings its own and installs it at `/opt/venv` — so on an `ubuntu`
base there is no `python` and no `python3` on `PATH`, and
`docker compose exec argos python scripts/...` fails with
`executable file not found in $PATH`. `builder/scripts` is copied into the image
for the same reason; `builder/src` alone was copied before, so the migration
script was not in there at all.

## Testing against Postgres

The suite runs on both. Each test gets its own schema, so tests cannot see each other's rows:

```sh
ARGOS_DATABASE_URL=postgresql://postgres:test@127.0.0.1:5432/argos venv/bin/python -m pytest tests -q
```

## It is opt-in, and that is deliberate

`docker-compose.yml` passes `ARGOS_DATABASE_URL` with **no default**. Unset means SQLite. An earlier version defaulted it to the Postgres URL, which meant starting the stack silently switched a working deployment onto an empty database: the app came up in a fraction of a second showing no profiles, no runs and no history. Nothing was lost — the migration script only reads the files — but nothing was migrated either, and the speed made it look like everything was fine.

`Store` now warns when it finds an empty Postgres beside a populated SQLite file, naming the script that fixes it. It cannot be an error, because a genuinely new install looks identical.

## The engine

The engine's store (`internal/store`) runs on either database the same way, chosen by the same `ARGOS_DATABASE_URL`. It holds one table, the activity log, and creates it with goose migrations — `internal/store/migrations/postgres` for Postgres and `migrations/sqlite` for the file. Both sets carry the same version numbers, so a database that has already run them does not run them again.

`internal/store/dialect.go` is the engine's half of the seam and mirrors the Python one: `IsPostgres`, and `Rebind`, which turns `?` into `$1, $2, ...`. The id of a new row comes from `RETURNING id` on Postgres, because there is no `LastInsertId` to ask for.

The Postgres migrations use `IF NOT EXISTS` throughout, so the engine can start before or after the data migration — whichever runs first, the other is a no-op.

`go test ./internal/store/` needs no server. To run the Postgres half as well:

```sh
ARGOS_TEST_POSTGRES=postgresql://postgres:test@127.0.0.1:5432/argos_test go test ./internal/store/
```
