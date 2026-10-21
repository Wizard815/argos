#!/bin/sh
# Move the data from the SQLite files into Postgres.
#
#   docker compose exec argos argos-migrate
#   docker compose exec argos argos-migrate --dry-run
#
# It exists because this image deliberately has no system Python: uv brings its
# own and installs it at /opt/venv, so `python scripts/...` is not a command that
# works in here. Adding /opt/venv/bin to PATH makes `python` work too, but the
# path is spelled out here so this keeps working even if the base image has a
# python of its own that would otherwise win.
set -e

exec /opt/venv/bin/python /app/builder/scripts/migrate_to_postgres.py "$@"
