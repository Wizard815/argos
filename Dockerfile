# Argos: the engine (Go) and the builder (Python) in one image.
#
# You bring the backend. The engine starts llama-server as a child process, so
# the image has to contain it. Point BASE_IMAGE at an image that already has
# your llama.cpp build (and its ROCm or CUDA libraries):
#
#   docker build --build-arg BASE_IMAGE=my-llamacpp --build-arg BASE_TAG=latest -t argos .

ARG BASE_IMAGE=ubuntu
ARG BASE_TAG=24.04

# ---- engine: the UI and the Go binary, built the way the Makefile does it ----
FROM golang:1.27-bookworm AS engine
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /src
COPY . .
RUN make linux-amd64

# ---- runtime: your llama.cpp image plus Argos ----
FROM ${BASE_IMAGE}:${BASE_TAG}

USER root
RUN command -v curl >/dev/null 2>&1 || (apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*)

# the Servers view reaches other machines over SSH (ssh to connect, ssh-keygen for each server's key)
RUN command -v ssh >/dev/null 2>&1 && command -v ssh-keygen >/dev/null 2>&1 || (apt-get update \
    && apt-get install -y --no-install-recommends openssh-client \
    && rm -rf /var/lib/apt/lists/*)

# uv brings its own Python, so the base image does not need one
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
COPY builder/requirements.txt /tmp/requirements.txt
RUN UV_PYTHON_INSTALL_DIR=/opt/python uv venv --python 3.14 /opt/venv \
    && uv pip install --python /opt/venv/bin/python -r /tmp/requirements.txt

COPY --from=engine /src/build/argos-linux-amd64 /app/argos
# The remote-llama wrapper ships in the image so /app/bin/remote-llama always
# exists and is executable. A host mount can still shadow it -- that is how it
# stays editable without a rebuild -- and the entrypoint re-asserts the mode on
# whatever ends up there.
COPY setup/remote-llama.sh /app/bin/remote-llama
RUN chmod 755 /app/bin/remote-llama
COPY builder/src /app/builder/src
COPY docker/argos.config.yaml /app/config.yaml
COPY docker/argos-entrypoint.sh /app/argos-entrypoint.sh

ENV ARGOS_ENGINE_URL=http://127.0.0.1:8081 \
    MODELS_DIR=/app/models \
    # The engine's database. The builder is given its own file by the entrypoint
    # (ARGOS_BUILDER_DB, default /data/builder.db): two processes on one SQLite file
    # contend for its lock, and when one wins the other's reads block forever.
    ARGOS_DB=/data/argos.db \
    ARGOS_RUNS_DIR=/data/runs \
    ARGOS_SCHEDULE_FILE=/app/config.d/scheduler.generated.yaml \
    HOST=0.0.0.0 \
    PORT=8080

VOLUME ["/data", "/app/config.d"]
EXPOSE 8080
WORKDIR /app
HEALTHCHECK CMD curl -f http://localhost:8080/health || exit 1
ENTRYPOINT ["/app/argos-entrypoint.sh"]
