#!/usr/bin/env bash
# Configure a per-worker OpenClaw state directory pointed at the local vLLM
# server, and install the PMPA skills and virtual environment into it.
#
# Everything OpenClaw touches derives from OPENCLAW_STATE_DIR
# (repo/eval/openclaw_injection/utils.py:110-132), so one state dir per worker
# is sufficient isolation — the same property that makes the Claude Code
# sandbox safe.
#
# Usage: setup-openclaw.sh [-n WORKERS] [-s SERVED_NAME] [-f]

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=pmpa-env.sh
source "$SCRIPT_DIR/pmpa-env.sh"

WORKERS=1
FORCE=0
while getopts ":n:s:fh" opt; do
  case "$opt" in
    n) WORKERS="$OPTARG" ;;
    s) PMPA_SERVED_MODEL="$OPTARG" ;;
    f) FORCE=1 ;;
    h) sed -n '2,10p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) echo "unknown option: -$OPTARG" >&2; exit 2 ;;
  esac
done

command -v openclaw >/dev/null || { echo "openclaw not on PATH (expected $PMPA_TOOLS/bin)" >&2; exit 1; }

for ((k = 0; k < WORKERS; k++)); do
  state="$PMPA_SANDBOX/w$k/openclaw"

  if [[ -d "$state/workspace" && "$FORCE" -eq 0 ]]; then
    echo "w$k: openclaw state exists, skipping (use -f to rebuild)"
    continue
  fi

  rm -rf "$state"
  mkdir -p "$state/workspace/skills" "$state/environment" "$state/download"

  # skills/README.md: skill folders go directly in <state>/workspace/skills,
  # with no extra skills/skills level.
  rsync -a --exclude 'README.md' "$PMPA_REPO/skills/" "$state/workspace/skills/"
  # README.md: OpenClaw reads its own copies of these, not the repo's.
  cp -R "$PMPA_REPO/environment/." "$state/environment/"
  cp -R "$PMPA_REPO/download/." "$state/download/"

  export OPENCLAW_STATE_DIR="$state"
  openclaw config patch --stdin <<JSON
{
  "models": {
    "providers": {
      "vllm-local": {
        "baseUrl": "http://${PMPA_VLLM_HOST}:${PMPA_VLLM_PORT}/v1",
        "api": "openai-completions",
        "apiKey": "pmpa-local",
        "models": [
          {
            "id": "${PMPA_SERVED_MODEL}",
            "name": "${PMPA_SERVED_MODEL}",
            "reasoning": true,
            "contextWindow": ${PMPA_MAX_MODEL_LEN:-65536},
            "input": ["text", "image"]
          }
        ]
      }
    }
  },
  "agents": {
    "defaults": {
      "model": "vllm-local/${PMPA_SERVED_MODEL}"
    }
  }
}
JSON

  echo "w$k: state=$state model=vllm-local/$PMPA_SERVED_MODEL"
done

echo "done: $WORKERS openclaw state dir(s) under $PMPA_SANDBOX"
