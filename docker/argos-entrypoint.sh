#!/bin/bash
# Starts the engine and the builder in one container. If either stops, the container stops.
set -u

mkdir -p /app/config.d /data/runs

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
/opt/venv/bin/python -m src.app &
BUILDER=$!

trap 'kill $ENGINE $BUILDER 2>/dev/null; wait' TERM INT

wait -n $ENGINE $BUILDER
CODE=$?
kill $ENGINE $BUILDER 2>/dev/null
wait
exit $CODE
