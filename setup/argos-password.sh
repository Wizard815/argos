#!/bin/sh
# Remove a forgotten web page password (or say what is set), from the server:
#
#   docker compose exec argos argos-password clear
#   docker compose exec argos argos-password status
#
# Argos then opens without logging in; set a new password in Settings -> Security.
# API keys are not touched. Reads the builder's database (ARGOS_BUILDER_DB, default
# /data/builder.db), or Postgres when ARGOS_DATABASE_URL is set.
set -e
cd /app/builder
exec /opt/venv/bin/python -m src.security "$@"
