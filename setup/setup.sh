#!/usr/bin/env bash
# Argos setup helper. Run from the Argos repo root (where docker-compose.yml is):
#   bash setup/setup.sh
# Needs config.yaml.template and remote-llama.sh next to this script. Writes: .env,
# docker-compose.override.yaml, config.yaml (existing files are backed up as *.bak).
# STATUS: the generated compose files are validated with `docker compose config`, not run on real GPUs.
set -euo pipefail
export MSYS_NO_PATHCONV=1   # Git Bash on Windows: do not rewrite /app/... paths

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEMPLATE="$HERE/config.yaml.template"
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT

say()  { printf '\n\033[1m%s\033[0m\n' "$*"; }
warn() { printf '\033[33m! %s\033[0m\n' "$*"; }
ask()  { # ask "prompt" default -> REPLY
  local d="${2:-}"; read -r -p "$1${d:+ [$d]}: " REPLY || REPLY=""; REPLY="${REPLY:-$d}"; }
yes()  { ask "$1 (y/n)" "${2:-n}"; [[ "$REPLY" =~ ^[Yy] ]]; }
backup() { [[ -f "$1" ]] && cp "$1" "$1.bak" && echo "  backed up $1 -> $1.bak" || true; }
random_secret() { # a password for the database: 32 hex characters, no punctuation to quote
  if command -v openssl >/dev/null 2>&1; then openssl rand -hex 32
  else head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n'
  fi
}

[[ -f docker-compose.yml ]] || { echo "Run this from the Argos repo root (no docker-compose.yml here)."; exit 1; }
[[ -f "$TEMPLATE" ]] || { echo "Missing $TEMPLATE"; exit 1; }
grep -q '^  argos:' docker-compose.yml || warn "docker-compose.yml has no 'argos' service; the override may not merge."

# ----------------------------------------------------------- previous answers
# .env holds the last run's choices, so a second run can offer them back instead of
# asking the same six stages. Reusing them writes NOTHING: the files are already
# right, and rewriting them is how a working deployment gets changed by accident.
# Answer no and every question is asked again, seeded from these values.
env_get() { [[ -f .env ]] && sed -n "s/^$1=//p" .env | head -1 || true; }

REUSE=0
PREV_IMAGE="$(env_get BASE_IMAGE)"; PREV_TAG="$(env_get BASE_TAG)"
PREV_MODELS="$(env_get MODELS_DIR)"; PREV_PORT="$(env_get ARGOS_PORT)"
PREV_KEY="$(env_get REMOTE_SSH_KEY_FILE)"; PREV_GID="$(env_get VIDEO_GID)"
PREV_ROCM_HOST="$(env_get ROCM_SSH_HOST)"; PREV_ROCM_PORT="$(env_get ROCM_SSH_PORT)"
PREV_ROCM_USER="$(env_get ROCM_SSH_USER)"; PREV_ROCM_BIN="$(env_get ROCM_SSH_BIN)"
PREV_CUDA_HOST="$(env_get CUDA_SSH_HOST)"; PREV_POOL="$(env_get ARGOS_SCHEDULE_POOL)"
PREV_CARDS="$(env_get ARGOS_SCHEDULE_CARDS)"
PREV_CUDA_PORT="$(env_get CUDA_SSH_PORT)"; PREV_CUDA_BIN="$(env_get CUDA_SSH_BIN)"
PREV_ROCM_IMAGE="$(env_get ROCM_IMAGE)"; PREV_CUDA_IMAGE="$(env_get CUDA_IMAGE)"
PREV_ROCM_IMG_BIN="$(env_get ROCM_BIN)"; PREV_CUDA_IMG_BIN="$(env_get CUDA_BIN)"
# Carried over rather than regenerated: changing it would leave the running
# Postgres on the old password and the app unable to reach it.
PREV_PG_PASSWORD="$(env_get POSTGRES_PASSWORD)"

if [[ -f .env ]]; then
  # what the last run chose, as far as .env records it
  PREV_BACKEND=""
  [[ -n "$PREV_ROCM_HOST" || -n "$(env_get ROCM_IMAGE)" ]] && PREV_BACKEND=rocm
  [[ -n "$PREV_CUDA_HOST" || -n "$(env_get CUDA_IMAGE)" ]] && PREV_BACKEND="${PREV_BACKEND:+$PREV_BACKEND/}cuda"
  PREV_MODE="$(env_get ARGOS_SETUP_MODE)"
  if [[ -z "$PREV_MODE" ]]; then  # written before setup recorded its mode
    PREV_MODE=1
    [[ -n "$(env_get HOST_MODELS_DIR)" ]] && PREV_MODE=2
    [[ -n "$PREV_KEY" ]] && PREV_MODE=3
  fi
  PREV_SCHED=groups; [[ -n "$PREV_POOL" ]] && PREV_SCHED=pool
  say "Found your last setup (.env)"
  printf '  backend   %s\n  mode      %s\n  models    %s\n  port      %s\n  scheduler %s\n' \
    "${PREV_BACKEND:-?}" "$PREV_MODE" "${PREV_MODELS:-?}" "${PREV_PORT:-?}" "$PREV_SCHED"
  if yes "Use these again? (y = keep the files and skip the questions)" y; then REUSE=1; fi
fi

# With REUSE=1 every question is answered from the last run, so the stages still run
# (the closing notes stay accurate) but nothing is asked and nothing is written.
ask() {
  local d="${2:-}"
  if [[ "${REUSE:-0}" == 1 ]]; then REPLY="$d"; printf '  %s [%s]\n' "$1" "$REPLY"; return; fi
  read -r -p "$1${d:+ [$d]}: " REPLY || REPLY=""; REPLY="${REPLY:-$d}"
}

# ---------------------------------------------------------------- preflight
say "1/6  Checking Docker"
if command -v docker >/dev/null 2>&1; then
  CV="$(docker compose version --short 2>/dev/null || true)"
  echo "  docker compose: ${CV:-not found}"
  if [[ -n "$CV" ]]; then
    IFS=. read -r MA MI _ <<<"${CV#v}"
    if (( MA < 2 || (MA == 2 && MI < 24) )); then
      warn "Compose < 2.24: '!override' / '!reset' in the override will fail. Update Docker Compose."
    fi
  else
    warn "docker compose plugin not found."
  fi
else
  warn "docker not found on this machine (fine if you only generate files here and run them on the server)."
fi

say "2/6  Backends"
DEF_BK="rocm"
ask "GPU backend(s): rocm / cuda / both" "${PREV_BACKEND:-$DEF_BK}"; BACKEND="$REPLY"
case "$BACKEND" in rocm|cuda|both) ;; *) echo "Invalid: $BACKEND"; exit 1;; esac

echo "  Where llama.cpp runs:"
echo "  Mode 1 = inside the Argos container: Argos is built on a llama.cpp image."
echo "  Mode 2 = next to it: Argos starts a llama.cpp container per model (needs docker.sock)."
echo "  Mode 3 = in llama.cpp container(s) you already run: Argos starts models there over SSH."
echo "  Modes 1 and 2 use the official llama.cpp images unless you name your own."
ask "Mode (1, 2 or 3)" "${PREV_MODE:-3}"; MODE="$REPLY"
[[ "$MODE" == 1 || "$MODE" == 2 || "$MODE" == 3 ]] || { echo "Invalid mode"; exit 1; }

ROCM_SSH_HOST=""; ROCM_SSH_PORT=22; ROCM_SSH_BIN=""; CUDA_SSH_HOST=""; CUDA_SSH_PORT=22; CUDA_SSH_BIN=""
CUSTOM_SSH_HOST=""; CUSTOM_SSH_PORT=22; CUSTOM_SSH_BIN=""; SSH_USER=root; SSH_KEY_HOST=""
VIDEO_GID="${PREV_GID:-44}"; ROCM_IMAGE=""; CUDA_IMAGE=""; BASE_IMAGE="ubuntu"; BASE_TAG="24.04"
OFFICIAL_ROCM="ghcr.io/ggml-org/llama.cpp:server-rocm"; OFFICIAL_CUDA="ghcr.io/ggml-org/llama.cpp:server-cuda"
ROCM_BIN="/app/llama-server"; CUDA_BIN="/app/llama-server"
# the previous run's images only when it was Mode 1 or 2 (Mode 3 records none)
[[ "${PREV_MODE:-}" == 3 ]] && { PREV_ROCM_IMAGE=""; PREV_CUDA_IMAGE=""; }
split_ref() { # image reference -> BASE_IMAGE BASE_TAG (registry:port/name:tag keeps its port)
  local r="$1"
  if [[ "${r##*/}" == *:* ]]; then BASE_IMAGE="${r%:*}"; BASE_TAG="${r##*:}"; else BASE_IMAGE="$r"; BASE_TAG="latest"; fi
}
if [[ "$BACKEND" != cuda && "$MODE" != 3 ]]; then
  ask "Numeric GID owning /dev/kfd and /dev/dri (ls -la /dev/dri)" "$VIDEO_GID"; VIDEO_GID="$REPLY"
fi
if [[ "$MODE" == 1 || "$MODE" == 2 ]]; then
  if [[ "$MODE" == 1 ]]; then where="Argos is built on it"; else where="Argos starts one per model next to it"; fi
  echo "  The llama.cpp image for each backend ($where). Enter for the official image,"
  echo "  or type your own (any image with llama-server in it)."
  if [[ "$MODE" == 1 && "$BACKEND" == both ]]; then
    warn "Mode 1 is one image, so it must hold both builds (no official image does); Mode 2 runs each in its own."
    ask "llama.cpp image with both builds" "${PREV_ROCM_IMAGE:-$OFFICIAL_ROCM}"; ROCM_IMAGE="$REPLY"; CUDA_IMAGE="$REPLY"
    ask "  ROCm llama-server path in it" "${PREV_ROCM_IMG_BIN:-$ROCM_BIN}"; ROCM_BIN="$REPLY"
    ask "  CUDA llama-server path in it" "${PREV_CUDA_IMG_BIN:-$CUDA_BIN}"; CUDA_BIN="$REPLY"
  else
    if [[ "$BACKEND" != cuda ]]; then
      ask "ROCm llama.cpp image" "${PREV_ROCM_IMAGE:-$OFFICIAL_ROCM}"; ROCM_IMAGE="$REPLY"
      ask "  llama-server path in it" "${PREV_ROCM_IMG_BIN:-$ROCM_BIN}"; ROCM_BIN="$REPLY"
    fi
    if [[ "$BACKEND" != rocm ]]; then
      ask "CUDA llama.cpp image" "${PREV_CUDA_IMAGE:-$OFFICIAL_CUDA}"; CUDA_IMAGE="$REPLY"
      ask "  llama-server path in it" "${PREV_CUDA_IMG_BIN:-$CUDA_BIN}"; CUDA_BIN="$REPLY"
    fi
  fi
  if [[ "$MODE" == 1 ]]; then split_ref "${ROCM_IMAGE:-$CUDA_IMAGE}"
  else
    warn "Mode 2: docker.sock gives Argos root-equivalent control of the host."
  fi
else
  echo "  For each backend: the llama.cpp container you already run. Its hostname on a shared Docker"
  echo "  network (or IP), its sshd port, and the llama-server path INSIDE that container."
  echo "  That container must mount your models folder at /app/models, same as Argos."
  ask "HOST path of the private ssh key Argos uses" "${PREV_KEY:-$PWD/ssh/argos_ed25519}"; SSH_KEY_HOST="$REPLY"
  if [[ ! -f "$SSH_KEY_HOST" ]] && command -v ssh-keygen >/dev/null 2>&1 && yes "  no key there yet; create one now?" y; then
    mkdir -p "$(dirname "$SSH_KEY_HOST")" && ssh-keygen -q -t ed25519 -N "" -C argos -f "$SSH_KEY_HOST" && echo "  created $SSH_KEY_HOST(.pub)"
  fi
  ask "ssh user" "${PREV_ROCM_USER:-root}"; SSH_USER="$REPLY"
  if [[ "$BACKEND" != cuda ]]; then
    ask "ROCm container host" "${PREV_ROCM_HOST:-llama-rocm}"; ROCM_SSH_HOST="$REPLY"
    ask "  sshd port" "${PREV_ROCM_PORT:-22}"; ROCM_SSH_PORT="$REPLY"
    ask "  llama-server path in it" "${PREV_ROCM_BIN:-llama-server}"; ROCM_SSH_BIN="$REPLY"
  fi
  if [[ "$BACKEND" != rocm ]]; then
    ask "CUDA container host" "${PREV_CUDA_HOST:-llama-cuda}"; CUDA_SSH_HOST="$REPLY"
    ask "  sshd port" "${PREV_CUDA_PORT:-22}"; CUDA_SSH_PORT="$REPLY"
    ask "  llama-server path in it" "${PREV_CUDA_BIN:-llama-server}"; CUDA_SSH_BIN="$REPLY"
  fi
fi
CUSTOM_BIN_HOST=""
if yes "Add a custom llama.cpp build (fork) as an extra backend?" n; then
  if [[ "$MODE" == 3 ]]; then
    ask "Custom container host (ssh)" "llama-custom"; CUSTOM_SSH_HOST="$REPLY"
    ask "  sshd port" "22"; CUSTOM_SSH_PORT="$REPLY"
    ask "  llama-server path in it" "llama-server"; CUSTOM_SSH_BIN="$REPLY"
    CUSTOM_BIN_HOST="(ssh)"
  else
    ask "HOST folder containing your custom llama-server" "/mnt/user/appdata/my-llama"; CUSTOM_BIN_HOST="$REPLY"
  fi
fi

# ---------------------------------------------------------------- scheduler
say "3/6  Scheduler"
echo "  pool   = planned on Argos's Schedule page. Models share the GPUs while they fit, each is placed on a"
echo "           card, requests wait in a FIFO queue, and a model that is answering is never unloaded. Recommended."
echo "  groups = you write routing groups by hand in config.yaml (the Schedule page then stays off)."
ask "Scheduler: pool / groups" "${PREV_SCHED:-pool}"; SCHED="$REPLY"
case "$SCHED" in pool|groups) ;; *) echo "Invalid: $SCHED"; exit 1;; esac
CARDS=""
if [[ "$SCHED" == pool ]]; then
  echo "  VRAM of each GPU in GiB, in the order HIP_VISIBLE_DEVICES / CUDA_VISIBLE_DEVICES number them"
  echo "  (two MI50 32GB: 32,32). Blank lets the engine detect AMD cards with rocm-smi."
  while :; do
    ask "GiB per card, comma-separated" ""; CARDS="${REPLY//[[:space:]]/}"
    [[ -z "$CARDS" || "$CARDS" =~ ^[0-9]+(\.[0-9]+)?(,[0-9]+(\.[0-9]+)?)*$ ]] && break
    warn "Use numbers separated by commas, e.g. 32,32 or 24,12."
  done
  [[ -z "$CARDS" && "$BACKEND" != rocm ]] && warn "NVIDIA or mixed GPUs are not detected: set the cards on the Schedule page."
fi

# ---------------------------------------------------------------- paths / volumes
say "4/6  Models and volumes"
ask "HOST path to your models folder" "${PREV_MODELS:-./models}"; MODELS_DIR="$REPLY"
[[ "$MODELS_DIR" == ./* || -d "$MODELS_DIR" ]] || warn "$MODELS_DIR does not exist on this machine (ok if it exists on the target)."
echo "  (mounted at /app/models inside the container)"
ask "HOST folder for Argos state (default ./data; blank keeps default)" ""; DATA_DIR="$REPLY"
ask "HOST folder for generated config fragments (default ./config.d; blank keeps default)" ""; CFGD_DIR="$REPLY"

VOLS=(); SCAN_DIRS=("/app/models")
echo "  Add extra volumes: HOST_PATH, then CONTAINER_PATH. Empty host path = done."
while :; do
  ask "  extra volume HOST path" ""; H="$REPLY"; [[ -z "$H" ]] && break
  ask "  CONTAINER path" "/app/extra-$(( ${#VOLS[@]} + 1 ))"; C="$REPLY"
  ask "  read-only? (y/n)" "y"; RO=""; [[ "$REPLY" =~ ^[Yy] ]] && RO=":ro"
  VOLS+=("$H:$C$RO")
  yes "  scan $C for model files too?" y && SCAN_DIRS+=("$C")
done

# ---------------------------------------------------------------- network
say "5/6  Network"
echo "  Networks must already exist (docker network ls). Names are used as given, not checked."
ask "Docker network name(s), comma-separated (blank = Docker default)" ""; NETS_RAW="$REPLY"
NETNAMES=(); NETIP=""; BINDIP=""
if [[ -n "$NETS_RAW" ]]; then
  IFS=',' read -r -a _n <<<"$NETS_RAW"
  for n in "${_n[@]}"; do n="${n//[[:space:]]/}"; [[ -n "$n" ]] && NETNAMES+=("$n"); done
fi
if (( ${#NETNAMES[@]} == 1 )); then
  ask "Fixed IPv4 for Argos on ${NETNAMES[0]} (blank = auto; required on macvlan/br0)" ""; NETIP="$REPLY"
fi
ask "Publish ports on one host IP only? (blank = all interfaces)" ""; BINDIP="$REPLY"
port_busy() { command -v ss >/dev/null 2>&1 && ss -ltnH "sport = :$1" 2>/dev/null | grep -q .; }
pick_port() {  # $1 label, $2 default
  local d="$2"
  while port_busy "$d"; do echo "  host port $d is already in use"; d=$((d + 1)); done
  ask "$1" "$d"; REPLY="${REPLY//[[:space:]]/}"
  if port_busy "$REPLY"; then echo "  warning: host port $REPLY is in use now; 'docker compose up' will fail until it is free"; fi
}
pick_port "HOST port for Argos (web page and OpenAI API, one port)" "${PREV_PORT:-8080}"; API_PORT="$REPLY"; UI_PORT="$API_PORT"
[[ "$MODE" == 2 ]] && echo "  Mode 2 note: --network container:argos requires container_name 'argos' and no host networking."

# ---------------------------------------------------------------- write files
if [[ "$REUSE" == 1 ]]; then
  say "6/6  Keeping your files"
  echo "  .env, docker-compose.override.yaml and config.yaml are already set up."
  echo "  Nothing was rewritten. Answer n to the question above to change something."
else
say "6/6  Writing files"

# Kept from the last run if there was one, so a re-run does not lock the app out
# of a Postgres that is already running on the old password.
PG_PASSWORD="${PREV_PG_PASSWORD:-$(random_secret)}"

# .env
backup .env
{
  echo "# generated by setup.sh"
  echo "# where llama.cpp runs (1 inside Argos, 2 next to it, 3 over SSH); Argos adds it to Servers on start"
  echo "ARGOS_SETUP_MODE=$MODE"; echo "ARGOS_SETUP_BACKENDS=$BACKEND"
  echo "BASE_IMAGE=$BASE_IMAGE"; echo "BASE_TAG=$BASE_TAG"
  echo "MODELS_DIR=$MODELS_DIR"
  echo "ARGOS_PORT=$API_PORT"
  [[ "$BACKEND" != cuda ]] && echo "VIDEO_GID=$VIDEO_GID"
  [[ -n "$ROCM_IMAGE" ]] && echo "ROCM_IMAGE=$ROCM_IMAGE" && echo "ROCM_BIN=$ROCM_BIN"
  [[ -n "$CUDA_IMAGE" ]] && echo "CUDA_IMAGE=$CUDA_IMAGE" && echo "CUDA_BIN=$CUDA_BIN"
  if [[ "$MODE" == 2 && -n "$CUSTOM_BIN_HOST" ]]; then
    echo "CUSTOM_IMAGE=${ROCM_IMAGE:-$CUDA_IMAGE}"
    if [[ -n "$ROCM_IMAGE" ]]; then echo "CUSTOM_GPU=rocm"; else echo "CUSTOM_GPU=cuda"; fi
  fi
  [[ "$MODE" == 2 ]] && echo "ARGOS_DOCKER_VOLUMES=${VOLS[*]+"${VOLS[*]}"}"
  [[ -n "$CUSTOM_BIN_HOST" && "$MODE" != 3 ]] && echo "HOST_CUSTOM_BIN=$CUSTOM_BIN_HOST"
  if [[ "$MODE" == 3 ]]; then
    echo "REMOTE_SSH_KEY_FILE=$SSH_KEY_HOST"
    [[ -n "$ROCM_SSH_HOST" ]] && echo "ROCM_SSH_HOST=$ROCM_SSH_HOST" && echo "ROCM_SSH_PORT=$ROCM_SSH_PORT" && echo "ROCM_SSH_USER=$SSH_USER" && echo "ROCM_SSH_BIN=$ROCM_SSH_BIN"
    [[ -n "$CUDA_SSH_HOST" ]] && echo "CUDA_SSH_HOST=$CUDA_SSH_HOST" && echo "CUDA_SSH_PORT=$CUDA_SSH_PORT" && echo "CUDA_SSH_USER=$SSH_USER" && echo "CUDA_SSH_BIN=$CUDA_SSH_BIN"
    [[ -n "$CUSTOM_SSH_HOST" ]] && echo "CUSTOM_SSH_HOST=$CUSTOM_SSH_HOST" && echo "CUSTOM_SSH_PORT=$CUSTOM_SSH_PORT" && echo "CUSTOM_SSH_USER=$SSH_USER" && echo "CUSTOM_SSH_BIN=$CUSTOM_SSH_BIN"
  fi
  [[ "$MODE" == 2 ]] && echo "HOST_MODELS_DIR=$MODELS_DIR" && echo "HOST_HF_DIR=/mnt/user/appdata/hf-cache"
  # The database password. Generated once and kept, so the compose file's
  # placeholder is never what a real install runs on.
  echo "# Postgres (used when ARGOS_DATABASE_URL is set)"
  echo "POSTGRES_PASSWORD=$PG_PASSWORD"
  if [[ "$SCHED" == pool ]]; then
    echo "# starting values for the Schedule page; what you save there wins"
    echo "ARGOS_SCHEDULE_POOL=1"
    [[ -n "$CARDS" ]] && echo "ARGOS_SCHEDULE_CARDS=$CARDS"
  fi
  true
} > .env
echo "  wrote .env"

# override
backup docker-compose.override.yaml
{
  echo "# generated by setup.sh -- edit freely"
  echo "services:"
  echo "  argos:"
  echo "    volumes:"
  echo "      - ./config.yaml:/app/config.yaml:ro"
  [[ -n "$DATA_DIR" ]] && echo "      - $DATA_DIR:/data"
  [[ -n "$CFGD_DIR" ]] && echo "      - $CFGD_DIR:/app/config.d"
  for v in "${VOLS[@]+"${VOLS[@]}"}"; do echo "      - $v"; done
  [[ "$MODE" == 2 ]] && echo "      - /var/run/docker.sock:/var/run/docker.sock"
  # CPU power for the header's watts counter: Docker hides these counters from containers; read-only, and
  # harmless where the folder does not exist (no RAPL: a VM, or not Linux)
  [[ -d /sys/devices/virtual/powercap ]] && echo "      - /sys/devices/virtual/powercap:/host/powercap:ro"
  if [[ "$MODE" == 3 ]]; then
    # The remote-llama wrapper is deliberately NOT mounted. The image ships it
    # at /app/bin/remote-llama with its execute bit; mounting a script from a
    # host share loses that bit (shares hand files back as 0666) and every model
    # launch then fails with "fork/exec ...: permission denied". To run your own
    # copy instead, mount it yourself and keep the mode correct on the host.
    echo "      - \${REMOTE_SSH_KEY_FILE}:/run/secrets/argos_ssh_key:ro"
  fi
  [[ -n "$CUSTOM_BIN_HOST" && "$MODE" == 1 ]] && echo "      - $CUSTOM_BIN_HOST:/opt/my-llama:ro"

  # GPU access for Argos itself
  if [[ "$MODE" == 2 || "$MODE" == 3 ]]; then
    echo "    # Mode $MODE: GPUs belong to the backend containers, not Argos."
    echo "    devices: !reset []"; echo "    group_add: !reset []"
  elif [[ "$BACKEND" == cuda ]]; then
    echo "    devices: !reset []"; echo "    group_add: !reset []"; echo "    gpus: all"
  elif [[ "$BACKEND" == both ]]; then
    echo "    gpus: all"
  fi
  echo "    environment:"
  echo "      # what setup chose: Argos adds it to Servers when it starts"
  echo "      - ARGOS_SETUP_MODE=\${ARGOS_SETUP_MODE:-}"
  echo "      - ARGOS_SETUP_BACKENDS=\${ARGOS_SETUP_BACKENDS:-}"
  if [[ "$MODE" == 1 || "$MODE" == 2 ]]; then
    for P in ROCM CUDA; do
      i="${P}_IMAGE"; [[ -n "${!i}" ]] || continue
      echo "      - ${P}_IMAGE=\${${P}_IMAGE}"; echo "      - ${P}_BIN=\${${P}_BIN}"
    done
  fi
  if [[ "$MODE" == 2 ]]; then
    echo "      - HOST_MODELS_DIR=\${HOST_MODELS_DIR}"
    echo "      - VIDEO_GID=\${VIDEO_GID:-44}"
    echo "      - ARGOS_DOCKER_VOLUMES=\${ARGOS_DOCKER_VOLUMES:-}"
    echo "      - HOST_CUSTOM_BIN=\${HOST_CUSTOM_BIN:-}"
    echo "      - CUSTOM_IMAGE=\${CUSTOM_IMAGE:-}"
    echo "      - CUSTOM_GPU=\${CUSTOM_GPU:-}"
    echo "      - HOST_HF_DIR=\${HOST_HF_DIR:-}"
  elif [[ "$MODE" == 3 ]]; then
    for P in ROCM CUDA CUSTOM; do
      h="${P}_SSH_HOST"; [[ -n "${!h}" ]] || continue
      for k in HOST PORT USER BIN; do echo "      - ${P}_SSH_${k}=\${${P}_SSH_${k}}"; done
    done
  elif [[ "$BACKEND" != rocm ]]; then
    echo "      - NVIDIA_VISIBLE_DEVICES=all"; echo "      - NVIDIA_DRIVER_CAPABILITIES=compute,utility"
  fi

  if [[ -n "$BINDIP" ]]; then
    echo "    ports: !override"; echo "      - \"$BINDIP:$API_PORT:8080\""
  fi
  if (( ${#NETNAMES[@]} > 0 )); then
    echo "    networks:"
    for n in "${NETNAMES[@]}"; do
      if [[ -n "$NETIP" ]]; then echo "      $n:"; echo "        ipv4_address: $NETIP"; else echo "      - $n"; fi
    done
    echo "networks:"
    for n in "${NETNAMES[@]}"; do echo "  $n:"; echo "    external: true"; done
  fi
} > docker-compose.override.yaml
echo "  wrote docker-compose.override.yaml"

# config.yaml from template
DEFAULT_BK="bk-rocm"; GPU_ENV="HIP_VISIBLE_DEVICES"
[[ "$BACKEND" == cuda ]] && { DEFAULT_BK="bk-cuda"; GPU_ENV="CUDA_VISIBLE_DEVICES"; }

MAC="$TMP/macros"; : > "$MAC"
if [[ "$MODE" == 1 ]]; then
  [[ "$BACKEND" != cuda ]] && echo "  bk-rocm: $ROCM_BIN" >> "$MAC"
  [[ "$BACKEND" != rocm ]] && echo "  bk-cuda: $CUDA_BIN" >> "$MAC"
  [[ -n "$CUSTOM_BIN_HOST" ]] && echo "  bk-custom: /opt/my-llama/llama-server" >> "$MAC"
elif [[ "$MODE" == 3 ]]; then
  [[ -n "$ROCM_SSH_HOST" ]] && echo "  bk-rocm: /app/bin/remote-llama rocm" >> "$MAC"
  [[ -n "$CUDA_SSH_HOST" ]] && echo "  bk-cuda: /app/bin/remote-llama cuda" >> "$MAC"
  [[ -n "$CUSTOM_SSH_HOST" ]] && echo "  bk-custom: /app/bin/remote-llama custom" >> "$MAC"
else
  # docker-llama starts a container from <BACKEND>_IMAGE per model, next to Argos
  [[ -n "$ROCM_IMAGE" ]] && echo "  bk-rocm: /app/bin/docker-llama rocm" >> "$MAC"
  [[ -n "$CUDA_IMAGE" ]] && echo "  bk-cuda: /app/bin/docker-llama cuda" >> "$MAC"
  [[ -n "$CUSTOM_BIN_HOST" ]] && echo "  bk-custom: /app/bin/docker-llama custom" >> "$MAC"
fi

GRP="$TMP/groups"; : > "$GRP"
if [[ "$BACKEND" == both ]]; then cat >> "$GRP" <<'EOF'
    # ---- NVIDIA / CUDA, GPU 0: files whose path contains /cuda/ ----
    - match: "/cuda/"
      dirs: [@@MODEL_DIRS@@]
      env:
        - "CUDA_VISIBLE_DEVICES=0"
      cmdTemplate: >
        ${bk-cuda} --host 0.0.0.0 --port ${PORT}
        -ngl 99 -c 32768 --parallel 2 --flash-attn on
        --cache-type-k q8_0 --cache-type-v q8_0
        --split-mode none --jinja -m ${MODEL_PATH}
      outputFile: /app/config.d/models.cuda0.generated.yaml
EOF
fi
if [[ -n "$CUSTOM_BIN_HOST" ]]; then cat >> "$GRP" <<EOF

    # ---- custom llama.cpp build: files whose path contains /custom/ ----
    - match: "/custom/"
      dirs: [@@MODEL_DIRS@@]
      env:
        - "$GPU_ENV=0"
      cmdTemplate: >
        \${bk-custom} --host 0.0.0.0 --port \${PORT}
        -ngl 99 -c 16384 --parallel 1 --flash-attn on --jinja -m \${MODEL_PATH}
      outputFile: /app/config.d/models.custom.generated.yaml
EOF
fi
[[ -s "$GRP" ]] || echo "    # (no extra backend groups selected)" > "$GRP"

DIRS_CSV="$(IFS=,; echo "${SCAN_DIRS[*]}" | sed 's/,/, /g')"
backup config.yaml
PYBIN="$(command -v python3 || command -v python || true)"
[[ -n "$PYBIN" ]] || { echo "python3 is required to render config.yaml"; exit 1; }
printf '%s' "$DIRS_CSV" > "$TMP/dirs"; printf '%s' "$DEFAULT_BK" > "$TMP/bk"; printf '%s' "$GPU_ENV" > "$TMP/env"
pp() { if command -v cygpath >/dev/null 2>&1; then cygpath -m "$1"; else printf '%s' "$1"; fi; }
"$PYBIN" - "$(pp "$TEMPLATE")" "$(pp "$TMP")" > "$TMP/config.out" <<'PY'
import sys
t,d=sys.argv[1:3]
r=lambda n: open(d+'/'+n,encoding='utf-8').read()
s=open(t,encoding='utf-8').read()
s=s.replace('@@MACROS@@',r('macros').rstrip(chr(10)) or '  {}')
s=s.replace('@@EXTRA_GROUPS@@',r('groups').rstrip(chr(10)))
s=s.replace('@@MODEL_DIRS@@',r('dirs')).replace('@@DEFAULT_BK@@',r('bk')).replace('@@GPU_ENV@@',r('env'))
sys.stdout.write(s)
PY
mv "$TMP/config.out" config.yaml
echo "  wrote config.yaml"

if [[ "$MODE" == 3 ]]; then
  # Placed for reference, and for anyone who would rather mount their own copy.
  # The image ships the copy that actually runs.
  [[ "$HERE" == "$PWD" ]] || cp "$HERE/remote-llama.sh" ./remote-llama.sh
  chmod +x ./remote-llama.sh 2>/dev/null || true
  echo "  placed remote-llama.sh (reference copy; the image ships the one that runs)"
fi

# model sanity check
if [[ -d "$MODELS_DIR" ]]; then
  N="$(find "$MODELS_DIR" -type f -name '*.gguf' 2>/dev/null | wc -l | tr -d ' ')"
  echo "  found $N .gguf file(s) under $MODELS_DIR"
  [[ "$N" == 0 ]] && warn "no .gguf files found there."
fi

if command -v docker >/dev/null 2>&1 && docker compose config -q 2>"$TMP/err"; then
  echo "  docker compose config: OK"
else
  [[ -s "$TMP/err" ]] && { warn "docker compose config reported problems:"; cat "$TMP/err"; }
fi

if [[ "$MODE" == 3 ]]; then cat <<EOF

Mode 3 checklist (each llama.cpp container)
  * sshd running, key auth only, and Argos's public key in its authorized_keys:
      ssh-keygen -t ed25519 -N "" -f $SSH_KEY_HOST        # then copy the .pub into the container
  * the SAME models folder mounted at /app/models
  * on a Docker network Argos can reach (the one(s) you listed), hostname/IP as entered
  * from the argos container: docker exec argos ssh -i /run/secrets/argos_ssh_key ${SSH_USER}@<host> llama-server --version
  Argos adds each container to Servers when it starts, once the key is mounted. Its public key:
      $(cat "$SSH_KEY_HOST.pub" 2>/dev/null || echo "(no $SSH_KEY_HOST.pub yet)")
EOF
fi
fi

HOSTNAME_HINT="${BINDIP:-<host>}"
cat <<EOF

Next steps
  1. docker compose up -d --build
  2. curl -X POST http://localhost:$API_PORT/api/models/rescan     (repeat if config.d is incomplete)
  3. grep -hE '^  [a-z0-9]' config.d/models.*.generated.yaml | sort | uniq -d     (must print nothing)
EOF
if [[ "$SCHED" == pool ]]; then cat <<EOF
  4. http://$HOSTNAME_HINT:$UI_PORT/#schedule -> Plan: check the cards${CARDS:+ ($CARDS GiB)}, Save. Keep 'routing:' in config.yaml
     commented out: saving the plan writes it to config.d. Watch it work under Live.
EOF
else cat <<EOF
  4. Put your model IDs in config.yaml 'routing:' groups, uncomment it, then: docker compose restart argos
     (leave the pool off on the Schedule page)
EOF
fi
cat <<EOF
  5. http://$HOSTNAME_HINT:$UI_PORT/#servers -> your llama.cpp is listed there already; Check it sees the same model files.
  6. http://$HOSTNAME_HINT:$UI_PORT/#assistant -> Settings: pick a chat model (or Set up Qwen3-0.6B) and an embedding model.
EOF
