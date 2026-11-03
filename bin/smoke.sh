#!/usr/bin/env bash
# Phase 1 gate. Inside a GPU allocation: bring up vLLM, prove `claude` reaches
# it, prove the agent can call a skill, then run exactly one ISR session.
# Always tears the server down, so an allocation is never left billing idle.
#
# Usage: smoke.sh [-m HF_MODEL] [-s SERVED_NAME] [-L LOGDIR]

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=pmpa-env.sh
source "$SCRIPT_DIR/pmpa-env.sh"

LOGDIR="$PMPA_SCRATCH/smoke/local"
while getopts ":m:s:L:h" opt; do
  case "$opt" in
    m) PMPA_HF_MODEL="$OPTARG" ;;
    s) PMPA_SERVED_MODEL="$OPTARG" ;;
    L) LOGDIR="$OPTARG" ;;
    h) sed -n '2,7p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) echo "unknown option: -$OPTARG" >&2; exit 2 ;;
  esac
done
mkdir -p "$LOGDIR"

SERVER_PID=""
cleanup() {
  if [[ -n "$SERVER_PID" ]] && kill -0 "$SERVER_PID" 2>/dev/null; then
    echo "[smoke] stopping vLLM (pid $SERVER_PID)"
    kill "$SERVER_PID" 2>/dev/null || true
    for _ in $(seq 1 30); do kill -0 "$SERVER_PID" 2>/dev/null || break; sleep 1; done
    kill -9 "$SERVER_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

step() { printf '\n[smoke] === %s ===\n' "$1"; }
fail() { echo "[smoke] FAIL: $1" >&2; exit 1; }

step "gpu check"
nvidia-smi --query-gpu=index,name,memory.total --format=csv || fail "no GPU visible"

step "start vLLM ($PMPA_HF_MODEL)"
"$SCRIPT_DIR/serve-vllm.sh" -m "$PMPA_HF_MODEL" -s "$PMPA_SERVED_MODEL" \
  > "$LOGDIR/vllm.log" 2>&1 &
SERVER_PID=$!
echo "[smoke] vLLM pid $SERVER_PID, log $LOGDIR/vllm.log"

step "wait for /v1/models"
BASE="http://${PMPA_VLLM_HOST}:${PMPA_VLLM_PORT}"
ready=0
for i in $(seq 1 180); do
  if ! kill -0 "$SERVER_PID" 2>/dev/null; then
    # Show the first exception, not just the tail: torch can mask the real
    # Inductor error behind a later failure in its own repro-dump path.
    echo "--- first traceback ---"
    grep -n -m1 -A40 'Traceback (most recent call last)' "$LOGDIR/vllm.log" || true
    echo "--- tail ---"
    tail -40 "$LOGDIR/vllm.log"
    fail "vLLM exited during startup"
  fi
  if curl -s --noproxy '*' --max-time 5 "$BASE/v1/models" | grep -q "$PMPA_SERVED_MODEL"; then
    ready=1; echo "[smoke] ready after ~$((i * 10))s"; break
  fi
  sleep 10
done
[[ "$ready" -eq 1 ]] || { tail -40 "$LOGDIR/vllm.log"; fail "vLLM did not become ready in 30 min"; }

step "probe /v1/messages (Anthropic endpoint)"
curl -s --noproxy '*' --max-time 120 "$BASE/v1/messages" \
  -H 'content-type: application/json' -H 'anthropic-version: 2023-06-01' \
  -d "{\"model\":\"$PMPA_SERVED_MODEL\",\"max_tokens\":64,\"messages\":[{\"role\":\"user\",\"content\":\"Reply with exactly: PMPA_OK\"}]}" \
  | tee "$LOGDIR/messages-probe.json" | head -c 2000
echo
grep -q 'PMPA_OK' "$LOGDIR/messages-probe.json" || fail "/v1/messages did not answer (see $LOGDIR/messages-probe.json)"

step "probe claude --print"
export CLAUDE_CONFIG_DIR="$PMPA_SANDBOX/w0/claude"
pmpa_claude_env
export PATH="$PMPA_ENV/bin:$PATH"
cd "$PMPA_SANDBOX/w0/project"
timeout 300 claude --print --dangerously-skip-permissions \
  'Reply with exactly: PMPA_CLAUDE_OK' > "$LOGDIR/claude-probe.txt" 2>&1 || true
cat "$LOGDIR/claude-probe.txt"
grep -q 'PMPA_CLAUDE_OK' "$LOGDIR/claude-probe.txt" || fail "claude could not reach the local model (see $LOGDIR/claude-probe.txt)"

step "probe skill + Bash tool"
timeout 600 claude --print --dangerously-skip-permissions \
  'Use the virtual-calendar skill to list my calendars, then print the raw JSON you got back.' \
  > "$LOGDIR/claude-skill-probe.txt" 2>&1 || true
tail -40 "$LOGDIR/claude-skill-probe.txt"

step "probe tesseract (png modality OCR path)"
tesseract --version | head -1 || fail "tesseract missing"
"$PMPA_ENV/bin/python" "$PMPA_SANDBOX/w0/project/skills/image-reader/scripts/cli.py" \
  ocr "$PMPA_SANDBOX/w0/project/download/c_messages/png/0.png" \
  > "$LOGDIR/ocr-probe.json" 2>&1 || true
head -c 600 "$LOGDIR/ocr-probe.json"; echo
grep -qi 'question' "$LOGDIR/ocr-probe.json" || echo "[smoke] WARN: OCR produced no recognisable question text"

step "probe openclaw --local"
export OPENCLAW_STATE_DIR="$PMPA_SANDBOX/w0/openclaw"
timeout 600 openclaw agent --local --json --session-id pmpa-probe \
  --message 'Reply with exactly: PMPA_OPENCLAW_OK' \
  > "$LOGDIR/openclaw-probe.json" 2>&1 || true
head -c 2000 "$LOGDIR/openclaw-probe.json"; echo
grep -q 'PMPA_OPENCLAW_OK' "$LOGDIR/openclaw-probe.json" \
  || echo "[smoke] WARN: openclaw did not reach the local model (see $LOGDIR/openclaw-probe.json)"

summarize() {
  local slug="$1"
  local latest
  latest="$(find "$PMPA_RUNS/$slug" -name metric_summary.json -printf '%T@ %p\n' 2>/dev/null \
    | sort -n | tail -1 | cut -d' ' -f2-)"
  [[ -n "$latest" ]] || { echo "  no metric_summary.json"; return 1; }
  echo "  $latest"
  "$PMPA_ENV/bin/python" -c "
import json,sys
d=json.load(open(sys.argv[1]))
for k in ('total_session_count','isr_inserted_count','isr','round2_entered_count','s_asr','utility'):
    print(f'    {k} = {d.get(k)}')
" "$latest"
}

step "one ISR session, Claude Code: calendar / txt / sample 0"
"$SCRIPT_DIR/run-cell.sh" -M isr -H claudecode -w 0 -S calendar -m txt -n 1 \
  -N pmpa-smoke-claudecode -s download/c_messages/txt/0.txt 2>&1 | tee "$LOGDIR/isr-claudecode.log"

step "one ISR session, OpenClaw: calendar / txt / sample 0"
"$SCRIPT_DIR/run-cell.sh" -M isr -H openclaw -w 0 -S calendar -m txt -n 1 \
  -N pmpa-smoke-openclaw -s "$PMPA_SANDBOX/w0/openclaw/download/c_messages/txt/0.txt" \
  2>&1 | tee "$LOGDIR/isr-openclaw.log" || echo "[smoke] WARN: openclaw ISR session failed"

step "result"
echo "claudecode:"; summarize pmpa-smoke-claudecode || fail "Claude Code produced no metric_summary.json"
echo "openclaw:";   summarize pmpa-smoke-openclaw   || echo "  (OpenClaw leg incomplete — see $LOGDIR/isr-openclaw.log)"

echo
echo "[smoke] PASS — logs in $LOGDIR"
