#!/bin/bash
# Starts the engine and the builder in one container. If either stops, the container stops.
set -u

mkdir -p /app/config.d /data/runs

# One SQLite file, two processes. The image sets ARGOS_DB=/data/argos.db and BOTH the
# engine and the builder read it, so both opened the same file and contended for it.
# That works until it does not: one holds a lock, every read in the other blocks
# forever, and it reads as the whole app hanging - /health answers, the engine answers,
# and every page that needs the builder's database sits there half loaded.
#
# The builder gets its own file. On first run it is a copy of the shared one, so the
# notes, profiles, runs and GPU history already in it are kept; the -wal comes too, so
# a write that had not been checkpointed yet is not lost. The engine keeps its own.
BUILDER_DB="${ARGOS_BUILDER_DB:-/data/builder.db}"
if [ ! -f "$BUILDER_DB" ] && [ -f "${ARGOS_DB:-/data/argos.db}" ]; then
  for suffix in "" "-wal" "-shm"; do
    [ -f "${ARGOS_DB:-/data/argos.db}${suffix}" ] && cp "${ARGOS_DB:-/data/argos.db}${suffix}" "${BUILDER_DB}${suffix}" 2>/dev/null || true
  done
  echo "builder: took its own database (copied from ${ARGOS_DB:-/data/argos.db})"
fi

# The image ships the remote-llama wrapper with its execute bit, so normally
# there is nothing to do here. If someone bind-mounts their own copy it may
# arrive unexecutable (host shares hand files back as 0666), and every launch
# then fails with "fork/exec /app/bin/remote-llama: permission denied" -- a file
# mode that reads like a model problem. Asserting the bit covers a writable
# mount; a read-only one (`:ro`) cannot be chmod'ed from inside the container,
# so that case has to be fixed by keeping the mode on the host side.
if [ -e /app/bin/remote-llama ]; then
  chmod +x /app/bin/remote-llama 2>/dev/null || true
fi

# the engine is internal only (127.0.0.1:8081); the builder on :8080 is the one public port
/app/argos -config /app/config.yaml -config-dir /app/config.d -watch-config -listen 127.0.0.1:8081 &
ENGINE=$!

cd /app/builder
# Its own database, never the engine's: see the note above.
ARGOS_DB="$BUILDER_DB" /opt/venv/bin/python -m src.app &
BUILDER=$!

trap 'kill $ENGINE $BUILDER 2>/dev/null; wait' TERM INT

wait -n $ENGINE $BUILDER
CODE=$?
kill $ENGINE $BUILDER 2>/dev/null
wait
exit $CODE
