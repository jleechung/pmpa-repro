#!/usr/bin/env bash
# Run one evaluation cell inside an isolated worker sandbox.
#
# Usage:
#   run-cell.sh -M isr  -H claudecode -w 0 -S calendar -m txt [-n SESSIONS]
#   run-cell.sh -M casr -H openclaw   -w 0 -S calendar        [-n SESSIONS]
#
#   -M  isr  -> injection: ISR + S-ASR + utility (repo/jobscripts/ISR.py)
#       casr -> trigger from pre-poisoned memory (repo/jobscripts/CASR.py)
#   -H  harness: claudecode (default) | openclaw
#   -w  worker index; must have been built by setup-sandbox.sh (and
#       setup-openclaw.sh for -H openclaw)
#   -S  scenario: calendar | docs | form | email
#   -m  modality: txt | pdf | png   (isr only)
#   -n  sessions per sample (isr, default 1) or total sessions (casr, default 30)
#   -N  run slug for the output root (default: pmpa-<mode>)
#   -s  override the samples path (a single file runs exactly one sample)
#   -B  override the round-1 benign prompt file (absolute path)
#   -T  per-round timeout in seconds (default 300; upstream default is 1800)
#   -d  dry run (evaluator --dry-run)
#
# Output: $PMPA_RUNS/<slug>/<model>/<...>/ — see repo/eval/injection/injection_cli.py:185

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=pmpa-env.sh
source "$SCRIPT_DIR/pmpa-env.sh"

MODE=""
HARNESS="claudecode"
WORKER=0
SCENARIO=""
MODALITY="txt"
SESSIONS=""
SLUG=""
SAMPLES=""
BENIGN_OVERRIDE=""
# A healthy round takes seconds; upstream defaults to 1800 s per round, so a
# single looping session could stall a worker for 30 min.
TIMEOUT="${PMPA_TIMEOUT_SECONDS:-300}"
DRY=0

while getopts ":M:H:w:S:m:n:N:s:T:B:dh" opt; do
  case "$opt" in
    M) MODE="$OPTARG" ;;
    H) HARNESS="$OPTARG" ;;
    w) WORKER="$OPTARG" ;;
    S) SCENARIO="$OPTARG" ;;
    m) MODALITY="$OPTARG" ;;
    n) SESSIONS="$OPTARG" ;;
    N) SLUG="$OPTARG" ;;
    s) SAMPLES="$OPTARG" ;;
    T) TIMEOUT="$OPTARG" ;;
    B) BENIGN_OVERRIDE="$OPTARG" ;;
    d) DRY=1 ;;
    h) sed -n '2,21p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) echo "unknown option: -$OPTARG" >&2; exit 2 ;;
  esac
done

[[ "$MODE" == "isr" || "$MODE" == "casr" ]] || { echo "-M must be isr or casr" >&2; exit 2; }
[[ "$HARNESS" == "claudecode" || "$HARNESS" == "openclaw" ]] || { echo "-H must be claudecode or openclaw" >&2; exit 2; }
[[ -n "$SCENARIO" ]] || { echo "-S scenario is required" >&2; exit 2; }
SLUG="${SLUG:-pmpa-$HARNESS-$MODE}"

PROJECT="$PMPA_SANDBOX/w$WORKER/project"
CONFIG="$PMPA_SANDBOX/w$WORKER/claude"
[[ -d "$PROJECT" ]] || { echo "worker $WORKER not built; run setup-sandbox.sh -n $((WORKER+1))" >&2; exit 1; }

# The evaluator wipes every file under $CLAUDE_CONFIG_DIR/projects/<cwd>/memory
# and every *.jsonl beside it. Refuse to run against the real config dir.
case "$CONFIG" in
  "$PMPA_SANDBOX"/*) : ;;
  *) echo "refusing: CLAUDE_CONFIG_DIR is outside $PMPA_SANDBOX" >&2; exit 1 ;;
esac
export CLAUDE_CONFIG_DIR="$CONFIG"

pmpa_claude_env
# `python` must resolve for the skills' `python <skill>/scripts/cli.py` calls
# and for the evaluator itself.
export PATH="$PMPA_ENV/bin:$PATH"

declare -A SAMPLE_DIR_BY_SCENARIO=(
  [calendar]="c_messages" [docs]="d_messages" [form]="f_messages" [email]="e_messages"
)
declare -A BENIGN_PROMPT_BY_MODALITY=(
  [txt]="prompts/injection_phase_prompts/benign_text.txt"
  [pdf]="prompts/injection_phase_prompts/benign_pdf.txt"
  [png]="prompts/injection_phase_prompts/benign_image.txt"
)

# Claude Code sends reasoning_effort=high by default. Qwen3.8 rejects it:
# "Unexpected reasoning effort high. Supported types are xhigh (default),
# medium, and low." The check lives in the model's own chat_template.jinja, so
# it is a model constraint, not a vLLM one. Set PMPA_CLAUDE_EFFORT=xhigh for
# Qwen3.8. OpenClaw sends no effort and is unaffected.
OUT_ROOT="$PMPA_RUNS/$SLUG"

ARGS=(
  --prompt-file "prompts/trigger_phase_prompts/${SCENARIO}.txt"
  --scenario "$SCENARIO"
  --model-name "$PMPA_SERVED_MODEL"
  --timeout-seconds "$TIMEOUT"
  --output-dir "$OUT_ROOT"
)

if [[ "$HARNESS" == "claudecode" ]]; then
  PREFIX=""
  ARGS+=(
    --cmd claude
    --cmd-args "--print --dangerously-skip-permissions${PMPA_CLAUDE_EFFORT:+ --effort $PMPA_CLAUDE_EFFORT}"
    --cwd "$PROJECT"
    --environment-dir "$PROJECT/environment"
  )
else
  # Driven one-shot: `openclaw agent --local --json --session-id X --message P`
  # (repo/eval/openclaw_injection/runner.py:90). No gateway daemon.
  PREFIX="Openclaw_"
  OC_STATE="$PMPA_SANDBOX/w$WORKER/openclaw"
  [[ -d "$OC_STATE/workspace" ]] || { echo "worker $WORKER has no openclaw state; run setup-openclaw.sh" >&2; exit 1; }
  export OPENCLAW_STATE_DIR="$OC_STATE"
  ARGS+=(
    --cmd openclaw
    --cmd-args "agent --local --json"
    --openclaw-state-dir "$OC_STATE"
    --openclaw-environment-dir "$OC_STATE/environment"
  )
fi

if [[ "$MODE" == "isr" ]]; then
  SCRIPT="jobscripts/${PREFIX}ISR.py"
  SAMPLE_DIR="${SAMPLE_DIR_BY_SCENARIO[$SCENARIO]:?unknown scenario $SCENARIO}"
  BENIGN="${BENIGN_OVERRIDE:-${BENIGN_PROMPT_BY_MODALITY[$MODALITY]:?unknown modality $MODALITY}}"
  # Claude Code reads the repo's own download/; OpenClaw reads its state copy.
  if [[ "$HARNESS" == "claudecode" ]]; then
    SAMPLE_ROOT="download/$SAMPLE_DIR/$MODALITY"
  else
    SAMPLE_ROOT="$OC_STATE/download/$SAMPLE_DIR/$MODALITY"
  fi
  ARGS+=(
    --benign-task-prompt-file "$BENIGN"
    --samples-path "${SAMPLES:-$SAMPLE_ROOT}"
    --method "$MODALITY"
    --sessions "${SESSIONS:-1}"
  )
  [[ "$HARNESS" == "claudecode" ]] && ARGS+=(--skills-dir "$PROJECT/skills")
else
  SCRIPT="jobscripts/${PREFIX}CASR.py"
  ARGS+=(--sessions "${SESSIONS:-30}")
fi

[[ "$DRY" -eq 1 ]] && ARGS+=(--dry-run)

echo "mode=$MODE worker=$WORKER scenario=$SCENARIO modality=$MODALITY"
echo "project=$PROJECT"
echo "config=$CLAUDE_CONFIG_DIR"
echo "endpoint=$ANTHROPIC_BASE_URL model=$ANTHROPIC_MODEL"
echo "output=$OUT_ROOT"

cd "$PROJECT"
exec python "$SCRIPT" "${ARGS[@]}"
