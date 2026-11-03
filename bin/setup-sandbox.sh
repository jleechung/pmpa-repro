#!/usr/bin/env bash
# Build N isolated PMPA worker sandboxes under $PMPA_SANDBOX.
#
# Each worker gets a full working copy of the upstream repo, because the
# evaluator resolves pdf/png sample paths relative to its own cwd and mutates
# environment/*.json in place. Each worker also gets its own CLAUDE_CONFIG_DIR,
# because the evaluator wipes the memory directory derived from --cwd between
# sessions (repo/eval/injection/memory.py:102).
#
# Usage: setup-sandbox.sh [-n WORKERS] [-f]
#   -n  number of workers (default 1)
#   -f  rebuild existing worker directories

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=pmpa-env.sh
source "$SCRIPT_DIR/pmpa-env.sh"

WORKERS=1
FORCE=0
while getopts ":n:fh" opt; do
  case "$opt" in
    n) WORKERS="$OPTARG" ;;
    f) FORCE=1 ;;
    h) sed -n '2,15p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) echo "unknown option: -$OPTARG" >&2; exit 2 ;;
  esac
done

[[ -d "$PMPA_REPO" ]] || { echo "repo not found: $PMPA_REPO" >&2; exit 1; }

for ((k = 0; k < WORKERS; k++)); do
  worker="$PMPA_SANDBOX/w$k"
  project="$worker/project"
  config="$worker/claude"

  if [[ -d "$project" && "$FORCE" -eq 0 ]]; then
    echo "w$k: exists, skipping (use -f to rebuild)"
    continue
  fi

  rm -rf "$project"
  mkdir -p "$project" "$config"

  # Working copy of upstream, without upstream's history.
  rsync -a --exclude '.git' --exclude '.claude' --exclude 'runs' \
    "$PMPA_REPO/" "$project/"

  # Claude Code derives its memory directory from the enclosing *project root*
  # (nearest .git), not from cwd, while the evaluator derives it from --cwd
  # (repo/eval/injection/utils.py:100). Without a .git here, Claude Code walks
  # up to any parent .git directory and writes memory under the parent project
  # key while the evaluator watches the worker project key — so ISR reads 0
  # no matter what the model did. Upstream's instruction to run from the PMPA
  # repo root works because that root is itself a git repo.
  git -C "$project" init -q

  # Seed-all ablation, applied to the sandbox copy only. See bin/seed-all-patch.py.
  if [[ "${PMPA_SEED_ALL:-0}" == "1" ]]; then
    "$SCRIPT_DIR/seed-all-patch.py" "$project/eval/injection/modes.py"
  fi

  # Skills, installed where skills/README.md says Claude Code expects them.
  mkdir -p "$project/.claude/skills"
  rsync -a --exclude 'README.md' "$PMPA_REPO/skills/" "$project/.claude/skills/"

  # Permissions: the skills shell out to python, and `claude --print` would
  # otherwise deny every Bash call, scoring S-ASR 0 for reasons unrelated to
  # the attack. The sandbox is a throwaway tree whose only side effects are
  # local JSON files, so bypassing is safe here. Recorded as a delta in
  # docs/ai/reproduction-plan.md.
  cat > "$project/.claude/settings.json" <<'JSON'
{
  "includeCoAuthoredBy": false,
  "permissions": {
    "defaultMode": "bypassPermissions"
  }
}
JSON

  # Pre-create the memory directory the evaluator snapshots and wipes, so the
  # first cleanup does not record a spurious "does not exist" error.
  sanitized="$(python3 - "$project" <<'PY'
import sys
print("".join(c if c.isalnum() else "-" for c in sys.argv[1]))
PY
)"
  mkdir -p "$config/projects/$sanitized/memory"
  : > "$config/projects/$sanitized/memory/MEMORY.md"

  echo "w$k: project=$project config=$config memory=$config/projects/$sanitized/memory"
done

echo "done: $WORKERS worker(s) under $PMPA_SANDBOX"
