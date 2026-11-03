#!/usr/bin/env bash
# Bring up vLLM, run the C-ASR matrix with per-harness output roots, tear down.
#
# This duplicates the startup and teardown of serve-and-run.sh on purpose so
# C-ASR can be launched standalone with per-harness output roots.
#
# Usage: serve-casr.sh [-m HF_MODEL] [-s SERVED_NAME] [-W workers]

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=pmpa-env.sh
source "$SCRIPT_DIR/pmpa-env.sh"

WORKERS=8
while getopts ":m:s:W:h" opt; do
  case "$opt" in
    m) PMPA_HF_MODEL="$OPTARG" ;;
    s) PMPA_SERVED_MODEL="$OPTARG" ;;
    W) WORKERS="$OPTARG" ;;
    h) sed -n '2,10p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) echo "unknown option: -$OPTARG" >&2; exit 2 ;;
  esac
done

LOGDIR="$PMPA_SCRATCH/serve/local"
mkdir -p "$LOGDIR"

SERVER_PID=""
cleanup() {
  if [[ -n "$SERVER_PID" ]] && kill -0 "$SERVER_PID" 2>/dev/null; then
    echo "[casr] stopping vLLM (pid $SERVER_PID)"
    kill "$SERVER_PID" 2>/dev/null || true
    for _ in $(seq 1 30); do kill -0 "$SERVER_PID" 2>/dev/null || break; sleep 1; done
    kill -9 "$SERVER_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

echo "[casr] sandboxes: $WORKERS workers under $PMPA_SANDBOX"
"$SCRIPT_DIR/setup-sandbox.sh"  -n "$WORKERS"
"$SCRIPT_DIR/setup-openclaw.sh" -n "$WORKERS" -s "$PMPA_SERVED_MODEL"

echo "[casr] starting vLLM ($PMPA_HF_MODEL)"
"$SCRIPT_DIR/serve-vllm.sh" -m "$PMPA_HF_MODEL" -s "$PMPA_SERVED_MODEL" \
  > "$LOGDIR/vllm.log" 2>&1 &
SERVER_PID=$!

BASE="http://${PMPA_VLLM_HOST}:${PMPA_VLLM_PORT}"
ready=0
for i in $(seq 1 180); do
  if ! kill -0 "$SERVER_PID" 2>/dev/null; then
    grep -n -m1 -A40 'Traceback (most recent call last)' "$LOGDIR/vllm.log" || true
    echo "[casr] FAIL: vLLM exited during startup"; exit 1
  fi
  if curl -s --noproxy '*' --max-time 5 "$BASE/v1/models" | grep -q "$PMPA_SERVED_MODEL"; then
    ready=1; echo "[casr] vLLM ready after ~$((i * 10))s"; break
  fi
  sleep 10
done
[[ "$ready" -eq 1 ]] || { tail -40 "$LOGDIR/vllm.log"; echo "[casr] FAIL: not ready"; exit 1; }

"$SCRIPT_DIR/run-casr.sh" -W "$WORKERS"

echo
echo "################ AGGREGATE ################"
"$PMPA_ENV/bin/python" "$SCRIPT_DIR/aggregate.py" --runs-root "$PMPA_RUNS" \
  --model "$PMPA_SERVED_MODEL" || echo "[casr] aggregate failed (results on disk)"
