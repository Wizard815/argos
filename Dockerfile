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

# uv brings its own Python, so the base image does not need one
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
COPY builder/requirements.txt /tmp/requirements.txt
RUN UV_PYTHON_INSTALL_DIR=/opt/python uv venv --python 3.14 /opt/venv \
    && uv pip install --python /opt/venv/bin/python -r /tmp/requirements.txt

COPY --from=engine /src/build/llama-swap-linux-amd64 /app/llama-swap
COPY builder/src /app/builder/src
COPY docker/argos.config.yaml /app/config.yaml
COPY docker/argos-entrypoint.sh /app/argos-entrypoint.sh

ENV LLAMA_SWAP_URL=http://127.0.0.1:8080 \
    MODELS_DIR=/app/models \
    ARGOS_DB=/data/argos.db \
    ARGOS_RUNS_DIR=/data/runs \
    ARGOS_EXPORT_FILE=/app/config.d/scheduler.generated.yaml \
    HOST=0.0.0.0 \
    PORT=7861

VOLUME ["/data", "/app/config.d"]
EXPOSE 8080 7861
WORKDIR /app
HEALTHCHECK CMD curl -f http://localhost:8080/ && curl -f http://localhost:7861/ || exit 1
ENTRYPOINT ["/app/argos-entrypoint.sh"]
