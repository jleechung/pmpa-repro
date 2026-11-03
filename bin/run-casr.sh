#!/usr/bin/env bash
# Run the C-ASR matrix with one output root per harness.
#
# run-matrix.sh gives both harnesses the same run slug. For C-ASR all eight
# cells start together, both harnesses get the same timestamp, and they
# interleave sessions into one directory and overwrite each other's
# metric_summary.json.
#
# The ISR phase escaped this by luck, not by design. With W=8, a Claude Code
# cell at index i and its OpenClaw twin at i+12 sit at different positions in
# their workers' serial streams (i//8 vs (i+12)//8), so they never start in the
# same second. The harnesses did run concurrently: openclaw_calendar_txt began
# while claudecode_docs_txt and claudecode_calendar_png were still running. A
# different -W could break the accident. Verified after the fact: each of the 38
# ISR timestamp directories holds 30 or 0 sessions carrying
# claudecode_session_1_path, never a mix.
#
# This passes -N "<slug>-<harness>" so the two never share a directory.
#
# Usage: run-casr.sh [-W 8] [-H "claudecode openclaw"] [-n 30] [-T 300]

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=pmpa-env.sh
source "$SCRIPT_DIR/pmpa-env.sh"

WORKERS=8
HARNESSES="claudecode openclaw"
SCENARIOS="calendar docs form email"
SESSIONS=30
TIMEOUT="${PMPA_TIMEOUT_SECONDS:-300}"

while getopts ":W:H:S:n:T:h" opt; do
  case "$opt" in
    W) WORKERS="$OPTARG" ;;
    H) HARNESSES="$OPTARG" ;;
    S) SCENARIOS="$OPTARG" ;;
    n) SESSIONS="$OPTARG" ;;
    T) TIMEOUT="$OPTARG" ;;
    h) sed -n '2,16p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) echo "unknown option: -$OPTARG" >&2; exit 2 ;;
  esac
done

CELLS=()
for harness in $HARNESSES; do
  for scenario in $SCENARIOS; do
    CELLS+=("$harness:$scenario")
  done
done

echo "casr: ${#CELLS[@]} cells | $WORKERS workers | $SESSIONS sessions/cell"
echo "model: $PMPA_SERVED_MODEL"

run_worker() {
  local worker="$1"; shift
  local cell
  for cell in "$@"; do
    IFS=':' read -r harness scenario <<< "$cell"
    local slug="pmpa-casr-${harness}-${PMPA_SERVED_MODEL}"
    local logdir="$PMPA_RUNS/$slug/_cell_logs"
    mkdir -p "$logdir"
    local started; started=$(date +%s)
    if "$SCRIPT_DIR/run-cell.sh" -M casr -H "$harness" -w "$worker" \
        -S "$scenario" -N "$slug" -n "$SESSIONS" -T "$TIMEOUT" \
        > "$logdir/${scenario}.log" 2>&1; then
      local status="ok"
    else
      local status="FAILED(rc=$?)"
    fi
    printf '[w%s] %-30s %-12s %4ss\n' \
      "$worker" "${harness}_${scenario}" "$status" "$(( $(date +%s) - started ))"
  done
}

pids=()
for ((w = 0; w < WORKERS; w++)); do
  mine=()
  for ((i = w; i < ${#CELLS[@]}; i += WORKERS)); do mine+=("${CELLS[$i]}"); done
  [[ ${#mine[@]} -eq 0 ]] && continue
  run_worker "$w" "${mine[@]}" &
  pids+=($!)
done

failed=0
for pid in "${pids[@]}"; do wait "$pid" || failed=$((failed + 1)); done
echo "casr done: $failed worker stream(s) reported a failure"
exit 0
