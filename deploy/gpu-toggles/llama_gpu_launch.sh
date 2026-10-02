#!/usr/bin/env bash
# llama_gpu_launch.sh — thin exec wrapper for llama-swap scan groups.
#
# Usage (called by the scan-group cmdTemplate as the launch command):
#   llama_gpu_launch.sh <mode> <model_gb> <margin_gb> [llama-server flags...]
#
# It delegates to pick_gpu.py, reads the chosen device from its JSON, and execs
# llama-server with HIP_VISIBLE_DEVICES set. Everything the model needs
# (--model, -c, --parallel, rope-scaling yarn, etc.) comes from llama-swap's
# cmdTemplate as the trailing flags.
set -euo pipefail

SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

MODE="${1:-auto}"
MODEL_GB="${2:-0}"
MARGIN_GB="${3:-4}"
shift 3 2>/dev/null || shift $#

# Let llama-swap pass a model id/name + parallel count so match-parallel can
# decide reuse-vs-spinup. These come from the scan group's env/cmd, e.g.:
#   --pick-model-id hermes-4_14b-q4_k_m --pick-parallel 1
MODEL_ID=""
MODEL_NAME=""
PARALLEL=1
ARGS=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --pick-model-id)   MODEL_ID="$2"; shift 2 ;;
    --pick-model-name) MODEL_NAME="$2"; shift 2 ;;
    --pick-parallel)   PARALLEL="$2"; shift 2 ;;
    *) ARGS+=("$1"); shift ;;
  esac
done

# If the auto-pick toggle file isn't present, default mode stays "auto" so
# the script reads /app/config.d/pick_gpu_mode at runtime.
CHOICE="$(python3 "$SELF_DIR/pick_gpu.py" "$MODE" "$MODEL_GB" "$MARGIN_GB" \
  --model-id "$MODEL_ID" --model-name "$MODEL_NAME" --parallel "$PARALLEL" \
  --state-file "/app/config.d/gpu_instance_state.json")"

# Parse the JSON with python (avoids jq dependency) and exec llama-server.
DEV="$(python3 -c "import sys,json;print(json.loads('''$CHOICE''')['device'])")"
export HIP_VISIBLE_DEVICES="$DEV"
export HSA_FORCE_FINE_GRAIN_PCIE=1
export GPU_MAX_HW_QUEUES=8

echo "[llama_gpu_launch] mode=$MODE model_gb=$MODEL_GB device=$DEV parallel=$PARALLEL" >&2
exec python3 "$SELF_DIR/llama_server.py" -- "${ARGS[@]}"
