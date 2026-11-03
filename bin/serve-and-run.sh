#!/usr/bin/env bash
# Bring up vLLM, wait for readiness, run a matrix (or several), tear the server
# down. Always releases the GPU, so an allocation is never left billing idle.
#
# Usage: serve-and-run.sh [-m HF_MODEL] [-s SERVED_NAME] [-W workers] -- <matrix args>...
#        serve-and-run.sh ... --phase2        # ISR then C-ASR, both harnesses

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=pmpa-env.sh
source "$SCRIPT_DIR/pmpa-env.sh"

WORKERS=8
PHASE2=0
while getopts ":m:s:W:ph" opt; do
  case "$opt" in
    m) PMPA_HF_MODEL="$OPTARG" ;;
    s) PMPA_SERVED_MODEL="$OPTARG" ;;
    W) WORKERS="$OPTARG" ;;
    p) PHASE2=1 ;;
    h) sed -n '2,7p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) echo "unknown option: -$OPTARG" >&2; exit 2 ;;
  esac
done
shift $((OPTIND - 1))

LOGDIR="$PMPA_SCRATCH/serve/local"
mkdir -p "$LOGDIR"

SERVER_PID=""
cleanup() {
  if [[ -n "$SERVER_PID" ]] && kill -0 "$SERVER_PID" 2>/dev/null; then
    echo "[run] stopping vLLM (pid $SERVER_PID)"
    kill "$SERVER_PID" 2>/dev/null || true
    for _ in $(seq 1 30); do kill -0 "$SERVER_PID" 2>/dev/null || break; sleep 1; done
    kill -9 "$SERVER_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

echo "[run] sandboxes: $WORKERS workers"
"$SCRIPT_DIR/setup-sandbox.sh"  -n "$WORKERS"
"$SCRIPT_DIR/setup-openclaw.sh" -n "$WORKERS" -s "$PMPA_SERVED_MODEL"

echo "[run] starting vLLM ($PMPA_HF_MODEL)"
"$SCRIPT_DIR/serve-vllm.sh" -m "$PMPA_HF_MODEL" -s "$PMPA_SERVED_MODEL" \
  > "$LOGDIR/vllm.log" 2>&1 &
SERVER_PID=$!

BASE="http://${PMPA_VLLM_HOST}:${PMPA_VLLM_PORT}"
ready=0
for i in $(seq 1 180); do
  if ! kill -0 "$SERVER_PID" 2>/dev/null; then
    echo "--- first traceback ---"
    grep -n -m1 -A40 'Traceback (most recent call last)' "$LOGDIR/vllm.log" || true
    echo "[run] FAIL: vLLM exited during startup"; exit 1
  fi
  if curl -s --noproxy '*' --max-time 5 "$BASE/v1/models" | grep -q "$PMPA_SERVED_MODEL"; then
    ready=1; echo "[run] vLLM ready after ~$((i * 10))s"; break
  fi
  sleep 10
done
[[ "$ready" -eq 1 ]] || { tail -40 "$LOGDIR/vllm.log"; echo "[run] FAIL: not ready"; exit 1; }

if [[ "$PHASE2" -eq 1 ]]; then
  echo
  echo "################ ISR + S-ASR ################"
  "$SCRIPT_DIR/run-matrix.sh" -M isr  -W "$WORKERS" -N "pmpa-isr-$PMPA_SERVED_MODEL"
  echo
  echo "################ C-ASR ################"
  "$SCRIPT_DIR/run-matrix.sh" -M casr -W "$WORKERS" -N "pmpa-casr-$PMPA_SERVED_MODEL"
else
  "$SCRIPT_DIR/run-matrix.sh" -W "$WORKERS" "$@"
fi

echo
echo "################ AGGREGATE ################"
"$PMPA_ENV/bin/python" "$SCRIPT_DIR/aggregate.py" --runs-root "$PMPA_RUNS" \
  --model "$PMPA_SERVED_MODEL" || echo "[run] aggregate failed (results still on disk)"
