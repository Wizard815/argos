# Argos: the engine (Go) and the builder (Python) in one image.
#
# You bring the backend. setup/setup.sh picks the base for you:
#   Mode 1  llama.cpp inside Argos: Argos is built on a llama.cpp image, e.g. the official
#           ghcr.io/ggml-org/llama.cpp (BASE_TAG server-rocm or server-cuda), or your own
#   Mode 2  a llama.cpp container next to Argos per model (docker-llama): plain ubuntu
#   Mode 3  your existing llama.cpp container(s) over SSH (remote-llama): plain ubuntu
#
#   docker build --build-arg BASE_IMAGE=ghcr.io/ggml-org/llama.cpp --build-arg BASE_TAG=server-rocm -t argos .

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

# uv brings its own Python and the base image may have none at all, so `python`
# is not on PATH by default. Put the venv's first: the app's own interpreter is
# the one anything run in here should get. /app/bin follows so the wrappers
# shipped with the image - argos-migrate, remote-llama - are found by name
# rather than having to be spelled out in full.
ENV PATH="/opt/venv/bin:/app/bin:$PATH"

COPY --from=engine /src/build/argos-linux-amd64 /app/argos
# The remote-llama wrapper ships in the image so /app/bin/remote-llama always
# exists and is executable. A host mount can still shadow it -- that is how it
# stays editable without a rebuild -- and the entrypoint re-asserts the mode on
# whatever ends up there.
COPY setup/remote-llama.sh /app/bin/remote-llama
# Mode 2: docker-llama starts a llama.cpp container per model through the host's docker.sock
COPY setup/docker-llama.sh /app/bin/docker-llama
COPY --from=docker:27-cli /usr/local/bin/docker /usr/local/bin/docker
RUN chmod 755 /app/bin/remote-llama /app/bin/docker-llama
# The official llama.cpp images keep llama-server in /app, which is not on PATH
RUN if ! command -v llama-server >/dev/null 2>&1 && [ -x /app/llama-server ]; then \
      ln -s /app/llama-server /usr/local/bin/llama-server; fi
COPY builder/src /app/builder/src
# The scripts that run inside the container - currently the data migration.
# builder/src alone was copied before, which meant scripts/migrate_to_postgres.py
# was not in the image and the documented command could not work.
COPY builder/scripts /app/builder/scripts
COPY setup/argos-migrate.sh /app/bin/argos-migrate
RUN chmod 755 /app/bin/argos-migrate
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
