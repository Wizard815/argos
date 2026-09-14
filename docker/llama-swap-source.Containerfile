# Builds llama-swap (with the modelscan fork changes) from source, instead of
# downloading a pre-built upstream release like docker/llama-swap.Containerfile
# does. Uses the repo's own `make linux-amd64` target (builds the UI, then
# the Go binary with it embedded via the embed_ui tag) rather than
# reimplementing those steps, since the UI build writes to
# internal/server/ui_dist via a relative path in ui/vite.config.ts and the
# Makefile is what keeps that working correctly.
#
# IMPORTANT: The default BASE_IMAGE/BASE_TAG below (ggml-org/llama.cpp,
# CUDA) is almost certainly NOT what you want, since your llama-server is a
# custom ROCm build (mx-llama.cpp-Rocm10), not upstream llama.cpp.
# llama-swap execs llama-server as a local child process (not over the
# network), so it has to live in the same container/filesystem as that
# binary and its ROCm runtime libraries. Override BASE_IMAGE/BASE_TAG to
# point at whatever image already has your ROCm llama.cpp build in it — see
# README-DEPLOY.md. If no such image exists yet, this file alone is not
# enough; you'd need to either build mx-llama.cpp-Rocm10 into this same
# image, or point BASE_IMAGE at wherever your "Cookbook" launcher's llama.cpp
# build already lives.

# ARGs used in a FROM must be declared before the FIRST FROM in the file to
# be visible there — declaring them between stage 1 and stage 2 (as this
# file originally did) silently doesn't work, BuildKit just warns
# "UndefinedArgInFrom" and the stage 2 FROM resolves to an empty image ref.
# Confirmed via `docker images` on RaidLab: mx-llamacpp-mx-llamacpp-ssh is
# the mx-llama.cpp-Rocm10 image already running llama-server successfully
# via Cookbook/mx-llamacpp-ssh.
ARG BASE_IMAGE=mx-llamacpp-mx-llamacpp-ssh
ARG BASE_TAG=latest

# ---- Stage 1: build UI + Go binary using the repo's own Makefile ----
FROM golang:1.27-bookworm AS builder

# Node 22, for `make ui` (npm run build)
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /src
COPY . .
RUN make linux-amd64

# ---- Stage 2: runtime — MUST match your ROCm llama-server's environment ----
FROM ${BASE_IMAGE}:${BASE_TAG} AS runtime

ARG UID=10001
ARG GID=10001
ARG USER_HOME=/app
ENV HOME=$USER_HOME

RUN if [ "$UID" -ne 0 ]; then \
      if [ "$GID" -ne 0 ]; then groupadd --system --gid $GID app; fi; \
      useradd --system --uid $UID --gid $GID --home $USER_HOME app; \
    fi
RUN mkdir --parents $HOME /app && chown --recursive $UID:$GID $HOME /app

COPY --from=builder --chown=$UID:$GID /src/build/llama-swap-linux-amd64 /app/llama-swap
COPY --chown=$UID:$GID config.example.yaml /app/config.yaml

USER $UID:$GID
WORKDIR /app
ENV PATH="/app:${PATH}"

HEALTHCHECK CMD curl -f http://localhost:8080/ || exit 1
ENTRYPOINT ["/app/llama-swap", "-config", "/app/config.yaml", "-config-dir", "/app/config.d", "-watch-config"]
