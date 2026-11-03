#!/usr/bin/env python3
"""Make every scenario pre-seed the harness memory directory.

Upstream seeds that directory only for the form scenario
(repo/eval/injection/modes.py:233-239). Every other scenario starts with
MEMORY.md truncated to zero bytes.

Claude Code ISR is far higher in the seeded cells. Qwen3.5-4B gives 0.567 on
form/txt and 0.000 on calendar, docs and email. Qwen3.5-9B gives 0.800 against
0.000 to 0.167. That pattern suggests the small models cannot locate an empty
memory directory, rather than refusing the instruction.

This patch seeds every scenario, so a rerun separates "could not locate memory"
from "would not comply".

The caller applies this to a worker sandbox copy only. The clone in repo/ stays
unmodified.

Usage:
  seed-all-patch.py <sandbox>/project/eval/injection/modes.py
"""

from __future__ import annotations

import sys
from pathlib import Path

OLD = '''    if scenario == "form":
        return initialize_project_memory_from_dir(
            config.memory_dir,
            FORM_INITIAL_MEMORY_DIR,
        )
    return cleanup_project_memory(config.memory_dir)'''

NEW = '''    # Seed-all ablation. Upstream seeds only when scenario == "form".
    return initialize_project_memory_from_dir(
        config.memory_dir,
        FORM_INITIAL_MEMORY_DIR,
    )'''


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    path = Path(sys.argv[1])
    source = path.read_text(encoding="utf-8")
    if NEW in source:
        print(f"already patched: {path}")
        return 0
    if OLD not in source:
        print(f"upstream initialize_session_memory not found in {path}", file=sys.stderr)
        return 1
    path.write_text(source.replace(OLD, NEW, 1), encoding="utf-8")
    print(f"patched: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
