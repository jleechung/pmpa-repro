#!/usr/bin/env bash
# Run a full PMPA evaluation matrix across parallel worker sandboxes.
#
# Cells are (harness × scenario × modality) for ISR, or (harness × scenario)
# for C-ASR. Each cell is 30 samples/sessions. Cells are dealt round-robin to
# W workers; a worker runs its cells serially, so no two processes ever share a
# sandbox — which matters because the evaluator wipes the memory directory
# between sessions.
#
# Usage:
#   run-matrix.sh -M isr  [-W 8] [-N slug] [-H "claudecode openclaw"]
#                 [-S "calendar docs form email"] [-m "txt pdf png"] [-T 300]
#
# One vLLM server serves all workers; throughput comes from worker parallelism.

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=pmpa-env.sh
source "$SCRIPT_DIR/pmpa-env.sh"

MODE="isr"
WORKERS=8
SLUG=""
HARNESSES="claudecode openclaw"
SCENARIOS="calendar docs form email"
MODALITIES="txt pdf png"
TIMEOUT="${PMPA_TIMEOUT_SECONDS:-300}"
SESSIONS=""

while getopts ":M:W:N:H:S:m:T:n:h" opt; do
  case "$opt" in
    M) MODE="$OPTARG" ;;
    W) WORKERS="$OPTARG" ;;
    N) SLUG="$OPTARG" ;;
    H) HARNESSES="$OPTARG" ;;
    S) SCENARIOS="$OPTARG" ;;
    m) MODALITIES="$OPTARG" ;;
    T) TIMEOUT="$OPTARG" ;;
    n) SESSIONS="$OPTARG" ;;
    h) sed -n '2,16p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) echo "unknown option: -$OPTARG" >&2; exit 2 ;;
  esac
done

SLUG="${SLUG:-pmpa-$MODE-$PMPA_SERVED_MODEL}"
LOGDIR="$PMPA_RUNS/$SLUG/_cell_logs"
mkdir -p "$LOGDIR"

# Build the cell list.
CELLS=()
for harness in $HARNESSES; do
  for scenario in $SCENARIOS; do
    if [[ "$MODE" == "isr" ]]; then
      for modality in $MODALITIES; do
        CELLS+=("$harness:$scenario:$modality")
      done
    else
      CELLS+=("$harness:$scenario:-")
    fi
  done
done

echo "matrix: $MODE | ${#CELLS[@]} cells | $WORKERS workers | timeout ${TIMEOUT}s"
echo "model:  $PMPA_SERVED_MODEL"
echo "slug:   $SLUG"
echo "output: $PMPA_RUNS/$SLUG"
echo

run_worker() {
  local worker="$1"; shift
  local cell
  for cell in "$@"; do
    IFS=':' read -r harness scenario modality <<< "$cell"
    local tag="${harness}_${scenario}_${modality}"
    # One output root per harness. Both harnesses sharing a slug let the
    # evaluator stamp the same <scenario>/<timestamp> directory twice; the two
    # runs then interleave sessions and overwrite each other's
    # metric_summary.json when all eight C-ASR cells start in the same second.
    local cell_slug="${SLUG}-${harness}"
    local log="$LOGDIR/${tag}.log"
    local started
    started=$(date +%s)

    local -a cmd=(
      "$SCRIPT_DIR/run-cell.sh" -M "$MODE" -H "$harness" -w "$worker"
      -S "$scenario" -N "$cell_slug" -T "$TIMEOUT"
    )
    [[ "$MODE" == "isr" ]] && cmd+=(-m "$modality")
    [[ -n "$SESSIONS" ]] && cmd+=(-n "$SESSIONS")

    if "${cmd[@]}" > "$log" 2>&1; then
      local status="ok"
    else
      local status="FAILED(rc=$?)"
    fi
    printf '[w%s] %-34s %-12s %4ss\n' \
      "$worker" "$tag" "$status" "$(( $(date +%s) - started ))"
  done
}

# Deal cells round-robin, then run one serial stream per worker.
pids=()
for ((w = 0; w < WORKERS; w++)); do
  mine=()
  for ((i = w; i < ${#CELLS[@]}; i += WORKERS)); do
    mine+=("${CELLS[$i]}")
  done
  [[ ${#mine[@]} -eq 0 ]] && continue
  run_worker "$w" "${mine[@]}" &
  pids+=($!)
done

failed=0
for pid in "${pids[@]}"; do
  wait "$pid" || failed=$((failed + 1))
done

echo
echo "matrix done: $failed worker stream(s) reported a failure"
echo "cell logs: $LOGDIR"
exit 0
