# Reproduction plan

Written 2026-10-28.

## Goal

Reproduce the PMPA main results (ISR, S-ASR, C-ASR, utility) for the **Claude Code**
harness with **locally served Qwen3.5-4B**, then **Qwen3.5-9B**. The paper reports
66.9% / 81.7% average ISR / C-ASR on Claude Code across its three hosted backbones
(`deepseek-v4-flash`, `deepseek-v4-pro`, `qwen3-max`).

## Architecture

```
vLLM 0.32.0  --served-model-name qwen3.5-4b   127.0.0.1:8000
├─ POST /v1/messages           (Anthropic Messages API)  ← Claude Code
└─ POST /v1/chat/completions   (OpenAI API)              ← OpenClaw

N parallel evaluator workers, each isolated under sandbox/w<k>/:

Claude Code leg                       OpenClaw leg
  cwd=w<k>/project                      OPENCLAW_STATE_DIR=w<k>/openclaw
  CLAUDE_CONFIG_DIR=w<k>/claude         (workspace/, environment/, download/,
  ANTHROPIC_BASE_URL=http://…:8000       agents/main/sessions/ all derive from it)
  ANTHROPIC_MODEL=qwen3.5-4b            model=vllm-local/qwen3.5-4b
  claude --print                        openclaw agent --local --json

NO_PROXY += 127.0.0.1,localhost
```

One server, both harnesses, many workers: vLLM batches concurrent requests, so
throughput comes from worker parallelism, not from more GPUs. Each worker needs
its own project cwd **and** its own config/state dir, because the evaluator
wipes the memory directory between sessions and workers would otherwise race.

## Deltas from the paper (record these in results)

| Dimension | Paper | Here |
|---|---|---|
| Backbone | `deepseek-v4-flash`, `deepseek-v4-pro`, `qwen3-max` (hosted APIs) | `Qwen/Qwen3.5-4B`, `Qwen/Qwen3.5-9B` (local, bf16, vLLM) |
| Serving | vendor APIs | vLLM `/v1/messages` shim; tool-call formatting differs from Anthropic's native serving |
| Harness | Claude Code (version unstated) + OpenClaw (version unstated) | Claude Code 2.1.220 + OpenClaw 2026.10.8 |
| Permissions | not documented (`.claude/` gitignored) | `--dangerously-skip-permissions` + `defaultMode: bypassPermissions`; see "Fixed choices" |
| Modalities | txt, pdf, png | all three; `tesseract` installed from conda-forge into `$PMPA_TOOLS` |

Absolute numbers are **not** expected to match. A 4B open model is a much weaker
agent than `deepseek-v4-pro`; the interesting claims to test are the qualitative
ones: that ISR is high, that C-ASR exceeds ISR on Claude Code, that utility is
preserved, and that the prompt-level defense cuts injection but not C-ASR.

## Phases

| # | Phase | GPUs | Est. wall |
|---|---|---|---|
| 0 | Env, weights, tools, sandboxes, wrapper scripts | 0 | ~2 h |
| 1 | Smoke: vLLM serves Qwen3.5-4B; both harnesses reach it; one calendar/txt ISR session each, end-to-end | 1 | ≤1 h |
| 2 | ISR + S-ASR, Qwen3.5-4B, both harnesses × 4 scenarios × 3 modalities | 1 | 4–8 h |
| 3 | C-ASR, Qwen3.5-4B, both harnesses × 4 scenarios × 30 | 1 | 2–4 h |
| 4 | Phases 2–3 repeated for Qwen3.5-9B | 1 | 6–12 h |
| 5 | Ablations (position, style, multi-interaction) + defense | 1 | TBD |

Phase 1 gates everything after it.

## Sizing

Both models are `Qwen3_5ForConditionalGeneration` — hybrid linear/full attention
(full attention every 4th layer) with a vision tower, 262144 max positions.

| Model | hidden | bf16 weights |
|---|---|---|
| Qwen3.5-4B | 2560 | ~8 GB |
| Qwen3.5-9B | 4096 | ~18 GB |

Cap `--max-model-len` well below 262144 (Claude Code's system prompt + skills is
large but nowhere near that).

## Fixed choices

These are settings, not open questions. They are recorded because they are
deltas from the paper that move the numbers, not because they need debate.

1. **Tool permissions — permissive.** `claude --print` has no human to approve
   anything, so a tool call needing approval is auto-denied. Every metric is
   defined on tool calls actually executing: calendar S-ASR is a `Bash` tool_use
   running `calendar.py create … --attendee` (`metrics.py:862,918`), email S-ASR
   is a `mailbox.json` diff, ISR is a memory-dir sha256 diff. Denied tools would
   score 0 for reasons unrelated to the attack, making "the model resisted"
   indistinguishable from "the harness blocked it". Set via
   `--dangerously-skip-permissions` (`run-cell.sh`) and
   `"defaultMode": "bypassPermissions"` (`setup-sandbox.sh`). Safe because the
   cwd is a throwaway sandbox whose only side effects are local JSON files.
2. **Thinking mode — on**, held fixed across both model sizes and both
   harnesses. Declared to OpenClaw as `"reasoning": true` on the model entry;
   vLLM parses `<think>` blocks with the `qwen3` reasoning parser.
3. **png modality — in scope, via OCR.** The prompt is literally
   `/image-reader` + "using ocr, …"
   (`prompts/injection_phase_prompts/benign_image.txt`), so OCR is the intended
   path, not vision. `tesseract 5.5.3` installed from conda-forge into
   `$PMPA_TOOLS`; verified on `download/c_messages/png/0.png` — the injected
   instruction survives the OCR round-trip intact.
4. **OpenClaw — in scope.** Driven one-shot as
   `openclaw agent --local --json --session-id X --message P`
   (`eval/openclaw_injection/runner.py:90`), so there is no gateway daemon to
   manage and nothing listening on a socket. All state derives from
   `OPENCLAW_STATE_DIR` (`eval/openclaw_injection/utils.py:110-132`), giving the
   same per-worker isolation as Claude Code. It reaches the **same** vLLM server
   at `/v1/chat/completions` while Claude Code uses `/v1/messages`, so both
   harnesses share one GPU.
