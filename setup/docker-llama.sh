#!/usr/bin/env bash
# docker-llama: make "start llama-server" mean "start it in a NEW llama.cpp container next to Argos" (Mode 2).
#
#   docker-llama <backend> <llama-server args...>
#   e.g. docker-llama rocm --host 0.0.0.0 --port 5801 -ngl 99 -m /app/models/x.gguf
#   docker-llama <backend> --bench <llama-bench args...>   llama-bench in that image instead
#   docker-llama <backend> --ps                            the llama.cpp processes in its containers
#
# One container per model, from the image setup.sh chose for the backend. It shares Argos's network
# (--network container:argos), so the model's port is localhost here, and it is removed when the model
# stops (docker passes Argos's stop signal on to it).
#
# Per backend NAME (rocm, cuda, custom) it reads, upper-cased:
#   <NAME>_IMAGE (required)   e.g. ghcr.io/ggml-org/llama.cpp:server-rocm
#   <NAME>_BIN   (/app/llama-server, the path of llama-server inside that image)
#   <NAME>_GPU   (the backend name; "cuda..." passes --gpus all, anything else the AMD devices)
# Shared: HOST_MODELS_DIR (required: the models folder ON THE HOST, mounted at /app/models),
#         VIDEO_GID (44), ARGOS_CONTAINER (argos), ARGOS_DOCKER_VOLUMES (extra "host:container[:ro]" words),
#         HOST_CUSTOM_BIN (custom only: the host folder holding your own llama-server, mounted at /opt/custom),
#         REMOTE_ENV_PASSTHRU (the GPU-pinning variables passed in)
set -euo pipefail

[[ $# -ge 1 ]] || { echo "usage: docker-llama <backend> <llama-server args...>" >&2; exit 2; }
name="$1"; shift
NAME="${name^^}"
get() { local v="${NAME}_$1"; printf '%s' "${!v:-$2}"; }

command -v docker >/dev/null 2>&1 || { echo "docker-llama: no docker CLI in this container" >&2; exit 2; }
[[ -S /var/run/docker.sock ]] || { echo "docker-llama: /var/run/docker.sock is not mounted (Mode 2 needs it)" >&2; exit 2; }

if [[ "${1:-}" == "--ps" ]]; then
  # one line each, "<pid> <rss KiB> <cpu seconds> <command line>", like remote-llama --ps
  for c in $(docker ps -q --filter "label=argos.backend=$name"); do
    docker top "$c" -o pid,rss,times,args 2>/dev/null | tail -n +2 | grep -E 'llama-(server|bench)' || true
  done
  exit 0
fi

image="$(get IMAGE "")"
bin="$(get BIN /app/llama-server)"
[[ "$NAME" == CUSTOM ]] && bin="/opt/custom/llama-server"
[[ -n "$image" ]] || { echo "docker-llama: ${NAME}_IMAGE is not set" >&2; exit 2; }
[[ -n "${HOST_MODELS_DIR:-}" ]] || { echo "docker-llama: HOST_MODELS_DIR is not set (the models folder on the host)" >&2; exit 2; }

bench=0
if [[ "${1:-}" == "--bench" ]]; then
  bench=1; shift
  bin="${bin%/*}/llama-bench"
fi

# the port llama-server listens on: also names the container, so a leftover from a crash is replaced
port=""; args=("$@")
for ((i = 0; i < ${#args[@]}; i++)); do
  case "${args[i]}" in
    --port) port="${args[i+1]:-}" ;;
    --port=*) port="${args[i]#--port=}" ;;
  esac
done
[[ $bench == 1 || "$port" =~ ^[0-9]+$ ]] || { echo "docker-llama: no --port <n> in the llama-server args" >&2; exit 2; }

run=(docker run --rm --init --network "container:${ARGOS_CONTAINER:-argos}" --label "argos.backend=$name"
     -v "${HOST_MODELS_DIR}:/app/models:ro")
if [[ $bench == 0 ]]; then
  cname="argos-${name}-${port}"
  docker rm -f "$cname" >/dev/null 2>&1 || true
  run+=(--name "$cname")
fi
case "$(get GPU "$name")" in
  cuda*) run+=(--gpus all) ;;
  *) run+=(--device /dev/kfd --device /dev/dri --group-add "${VIDEO_GID:-44}") ;;
esac
for v in ${ARGOS_DOCKER_VOLUMES:-}; do run+=(-v "$v"); done
[[ "$NAME" == CUSTOM && -n "${HOST_CUSTOM_BIN:-}" ]] && run+=(-v "${HOST_CUSTOM_BIN}:/opt/custom:ro")
for v in ${REMOTE_ENV_PASSTHRU:-HIP_VISIBLE_DEVICES CUDA_VISIBLE_DEVICES ROCR_VISIBLE_DEVICES HSA_OVERRIDE_GFX_VERSION LLAMA_ENABLE_MTP_OPT}; do
  [[ -n "${!v:-}" ]] && run+=(-e "$v=${!v}")
done
run+=(--entrypoint "$bin" "$image")
exec "${run[@]}" "${args[@]}"
