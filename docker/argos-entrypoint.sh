#!/bin/bash
# Starts the engine and the builder in one container. If either stops, the container stops.
set -u

mkdir -p /app/config.d /data/runs

/app/argos -config /app/config.yaml -config-dir /app/config.d -watch-config -listen 0.0.0.0:8080 &
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
