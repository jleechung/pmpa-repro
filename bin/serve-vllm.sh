#!/usr/bin/env bash
# Serve a Qwen3.5 checkpoint with vLLM's Anthropic-compatible /v1/messages
# endpoint, which is what `claude` talks to via ANTHROPIC_BASE_URL.
#
# Usage: serve-vllm.sh [-m HF_MODEL] [-s SERVED_NAME] [-p PORT] [-l MAX_LEN]
#                      [-t TOOL_PARSER] [-r REASONING_PARSER] [-- extra vllm args]
#
# Tool- and reasoning-parser names change between vLLM releases, so they are
# discovered from `vllm serve --help` at runtime rather than hardcoded.
# Override with -t / -r or PMPA_TOOL_PARSER / PMPA_REASONING_PARSER.

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=pmpa-env.sh
source "$SCRIPT_DIR/pmpa-env.sh"

MAX_LEN="${PMPA_MAX_MODEL_LEN:-65536}"
TOOL_PARSER="${PMPA_TOOL_PARSER:-}"
REASONING_PARSER="${PMPA_REASONING_PARSER:-}"

while getopts ":m:s:p:l:t:r:h" opt; do
  case "$opt" in
    m) PMPA_HF_MODEL="$OPTARG" ;;
    s) PMPA_SERVED_MODEL="$OPTARG" ;;
    p) PMPA_VLLM_PORT="$OPTARG" ;;
    l) MAX_LEN="$OPTARG" ;;
    t) TOOL_PARSER="$OPTARG" ;;
    r) REASONING_PARSER="$OPTARG" ;;
    h) sed -n '2,13p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) echo "unknown option: -$OPTARG" >&2; exit 2 ;;
  esac
done
shift $((OPTIND - 1))

VLLM="$PMPA_ENV/bin/vllm"
[[ -x "$VLLM" ]] || { echo "vllm not found at $VLLM" >&2; exit 1; }

# Parser names are registry keys that move between vLLM releases, so resolve
# them against the installed registry instead of hardcoding. `vllm serve --help`
# cannot be used for this: it instantiates the device config and fails on any
# node without a GPU.
pick_parser() {
  local registry="$1"; shift
  "$PMPA_ENV/bin/python" - "$registry" "$@" <<'PY'
import importlib, sys
registry, *candidates = sys.argv[1:]
mod = importlib.import_module(registry)
table = getattr(mod, "_TOOL_PARSERS_TO_REGISTER", None) or getattr(
    mod, "_REASONING_PARSERS_TO_REGISTER", {}
)
for name in candidates:
    if name in table:
        print(name)
        break
PY
}

# Qwen3.5 emits Qwen3-Coder-style XML tool calls and <think> blocks
# (chat_template.jinja:53, :112).
if [[ -z "$TOOL_PARSER" ]]; then
  TOOL_PARSER="$(pick_parser vllm.tool_parsers qwen3_5 qwen3_coder qwen3_xml hermes || true)"
fi
if [[ -z "$REASONING_PARSER" ]]; then
  REASONING_PARSER="$(pick_parser vllm.reasoning qwen3_5 qwen3 deepseek_r1 || true)"
fi

ARGS=(
  serve "$PMPA_HF_MODEL"
  --served-model-name "$PMPA_SERVED_MODEL"
  --host "$PMPA_VLLM_HOST"
  --port "$PMPA_VLLM_PORT"
  --max-model-len "$MAX_LEN"
  --gpu-memory-utilization 0.90
  --enable-auto-tool-choice
)
[[ -n "$TOOL_PARSER" ]] && ARGS+=(--tool-call-parser "$TOOL_PARSER")
[[ -n "$REASONING_PARSER" ]] && ARGS+=(--reasoning-parser "$REASONING_PARSER")
# Extra vLLM flags, for cases the wrapper does not model (e.g.
# PMPA_VLLM_EXTRA="--tensor-parallel-size 4" for multi-GPU models).
if [[ -n "${PMPA_VLLM_EXTRA:-}" ]]; then
  # shellcheck disable=SC2206
  ARGS+=(${PMPA_VLLM_EXTRA})
fi
ARGS+=("$@")

echo "model:            $PMPA_HF_MODEL  (served as $PMPA_SERVED_MODEL)"
echo "endpoint:         http://${PMPA_VLLM_HOST}:${PMPA_VLLM_PORT}/v1/messages"
echo "max-model-len:    $MAX_LEN"
echo "tool parser:      ${TOOL_PARSER:-<none detected>}"
echo "reasoning parser: ${REASONING_PARSER:-<none detected>}"
echo "+ $VLLM ${ARGS[*]}"

exec "$VLLM" "${ARGS[@]}"
