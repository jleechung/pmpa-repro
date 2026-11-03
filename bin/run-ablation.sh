#!/usr/bin/env bash
# Run the paper's injection-strategy ablations: position (ct/cb) and style
# (ca/ci), calendar scenario only, across modalities and both harnesses.
#
# Upstream equivalent: repo/jobscripts/batch/ablation_strategies.sh. The only
# difference from a main ISR cell is the samples directory and the output slug,
# both of which run-cell.sh already takes (-s and -N), so this reuses it rather
# than duplicating the evaluator invocation.
#
# Usage: run-ablation.sh [-W 8] [-H "claudecode openclaw"] [-m "txt pdf png"]
#                        [-g "position:ct_messages position:cb_messages ..."]

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=pmpa-env.sh
source "$SCRIPT_DIR/pmpa-env.sh"

WORKERS=8
HARNESSES="claudecode openclaw"
MODALITIES="txt pdf png"
GROUPS="position:ct_messages position:cb_messages style:ca_messages style:ci_messages"
TIMEOUT="${PMPA_TIMEOUT_SECONDS:-300}"

while getopts ":W:H:m:g:T:h" opt; do
  case "$opt" in
    W) WORKERS="$OPTARG" ;;
    H) HARNESSES="$OPTARG" ;;
    m) MODALITIES="$OPTARG" ;;
    g) GROUPS="$OPTARG" ;;
    T) TIMEOUT="$OPTARG" ;;
    h) sed -n '2,12p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) echo "unknown option: -$OPTARG" >&2; exit 2 ;;
  esac
done

CELLS=()
for harness in $HARNESSES; do
  for group in $GROUPS; do
    for modality in $MODALITIES; do
      CELLS+=("$harness:$group:$modality")
    done
  done
done

echo "ablation: ${#CELLS[@]} cells | $WORKERS workers | model $PMPA_SERVED_MODEL"

run_worker() {
  local worker="$1"; shift
  local cell
  for cell in "$@"; do
    IFS=':' read -r harness dimension group modality <<< "$cell"
    local slug="pmpa-abl-${dimension}-${group%%_*}-${PMPA_SERVED_MODEL}"
    local tag="${harness}_${dimension}_${group%%_*}_${modality}"
    local logdir="$PMPA_RUNS/$slug/_cell_logs"
    mkdir -p "$logdir"

    # Claude Code reads the repo's own download/; OpenClaw its state copy.
    local samples
    if [[ "$harness" == "claudecode" ]]; then
      samples="download/ablation_${dimension}/${group}/${modality}"
    else
      samples="$PMPA_SANDBOX/w$worker/openclaw/download/ablation_${dimension}/${group}/${modality}"
    fi

    local started; started=$(date +%s)
    if "$SCRIPT_DIR/run-cell.sh" -M isr -H "$harness" -w "$worker" \
        -S calendar -m "$modality" -N "$slug" -T "$TIMEOUT" -s "$samples" \
        > "$logdir/${tag}.log" 2>&1; then
      local status="ok"
    else
      local status="FAILED(rc=$?)"
    fi
    printf '[w%s] %-44s %-12s %4ss\n' "$worker" "$tag" "$status" "$(( $(date +%s) - started ))"
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
echo "ablation done: $failed worker stream(s) reported a failure"
exit 0
