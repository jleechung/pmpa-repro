# pmpa — reproduce PMPA with local Qwen3.5 backbones

Reproduction of **"When Malicious Instructions Persist: Persistent Memory Poisoning
Attack on Harness-Based Agents"** (Huang, Zhang, Jia; [arXiv:2609.13889](https://arxiv.org/abs/2609.13889);
code [hsh754/PMPA](https://github.com/hsh754/PMPA)), substituting the paper's hosted
API backbones with locally served **Qwen3.5-4B** and **Qwen3.5-9B**.

Started 2026-10-28.

| Doc | Contents |
|---|---|
| [`CONVENTIONS.md`](CONVENTIONS.md) | Doc rules for this project |
| [`upstream-anatomy.md`](upstream-anatomy.md) | What the upstream code actually does, with `file:line` |
| [`reproduction-plan.md`](reproduction-plan.md) | Scope, deltas from the paper, phases, cost |
| [`session-log.md`](session-log.md) | Dated record of what was run and found |

## Layout

| Path | What |
|---|---|
| `pmpa/repo` | Clean clone of `hsh754/PMPA`. Upstream only. |
| `pmpa/bin` | Our launchers, serving scripts, parallel driver |
| `pmpa/docs/ai` | This record |
| `pmpa/experiments/<slug>` | Per-experiment README, results, `log/` |
| `$PMPA_ENV` | uv venv (vLLM + upstream requirements) |
| `$PMPA_SANDBOX` | Isolated `CLAUDE_CONFIG_DIR`, agent cwd and `OPENCLAW_STATE_DIR` per worker. Scope this per served model so two runs never rebuild each other's workers. |
| `$PMPA_RUNS/<slug>/<model>/<modality>/<scenario>/<timestamp>` | Run output. C-ASR omits the modality level (`repo/jobscripts/CASR.py:1398`). |
| `$HF_HOME` | Qwen3.5 weights (shared cache) |

## The one rule that matters

The upstream evaluator **deletes every file** under
`$CLAUDE_CONFIG_DIR/projects/<sanitized-cwd>/memory/` and every `*.jsonl`
beside it, between sessions (`repo/eval/injection/memory.py:102-138`).
With the default config dir that is the *real* Claude Code memory and session
transcripts. Every invocation must set `CLAUDE_CONFIG_DIR` into
`$PMPA_SANDBOX/...`. Never run the evaluator with the
default config dir.
