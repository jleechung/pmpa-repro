# Session log

## 2026-10-28 — scoping and phase 0 setup

Cloned `hsh754/PMPA` and read the evaluator end to end. Findings recorded in
[`upstream-anatomy.md`](upstream-anatomy.md); plan in
[`reproduction-plan.md`](reproduction-plan.md).

### Verified facts

| Claim | Evidence |
|---|---|
| Claude Code CLI present | `2.1.220` at `~/.local/bin/claude` |
| `node` / `npm` absent by default | needed only for the OpenClaw harness (Node ≥24) |
| `Qwen/Qwen3.5-4B` and `-9B` exist and are downloadable | 8.8 GB and 18 GB in `$HF_HOME/hub` |
| Both are `Qwen3_5ForConditionalGeneration` | hybrid linear/full attention, vision tower, `max_position_embeddings` 262144 |
| vLLM latest is `0.32.0` | PyPI |
| vLLM 0.32.0 has the Anthropic endpoint | `vllm/entrypoints/anthropic/api_router.py` |
| vLLM 0.32.0 supports Qwen3.5 | `vllm/model_executor/models/qwen3_5.py` |
| Correct parsers are `qwen3_coder` + `qwen3` | registry keys in `vllm/tool_parsers/__init__.py:193` and `vllm/reasoning/__init__.py:155`; the model's `chat_template.jinja:53` emits `<tool_call><function=…><parameter=…>` XML and `<think>` |
| `vllm serve --help` fails without a GPU | `RuntimeError: Failed to infer device type` — parser detection must read the registry, not the help text |
| png modality wants OCR, not vision | `prompts/injection_phase_prompts/benign_image.txt` is `/image-reader` + "using ocr, …" |
| OCR path works end to end | `tesseract 5.5.3` from conda-forge; `image-reader/scripts/cli.py ocr` on `download/c_messages/png/0.png` returns the question **and** the injected memory instruction intact |
| OpenClaw needs no daemon | evaluator builds `openclaw agent --local --json --session-id X --message P` (`eval/openclaw_injection/runner.py:90`); `--local` runs the embedded agent |
| OpenClaw state is fully relocatable | every path derives from `OPENCLAW_STATE_DIR` (`eval/openclaw_injection/utils.py:110-132`) |
| OpenClaw provider config shape | `models.providers.<id>.{baseUrl,api,apiKey,models[]}`; `models` is an **array** whose items require `id` and `name`; `api` enum includes `openai-completions`. Selected with `agents.defaults.model = <provider>/<model>` |
| Loopback must bypass HTTP proxies | `no_proxy` may list `localhost` but **not** `127.0.0.1`; `pmpa-env.sh` adds `127.0.0.1,localhost,::1` |

### Built

- `pmpa/` ops layout; `pmpa/repo` is the clean upstream clone (`git status` clean)
- `$PMPA_ENV` — uv venv, Python 3.12.10, vLLM 0.32.0, torch
  2.13.0, transformers 5.19.0, plus upstream `requirements.txt`
- `$PMPA_TOOLS` — conda-forge prefix: `tesseract` 5.5.3,
  Node v26.12.1, npm 11.20.0, and `openclaw` 2026.10.8 installed globally into it
- `bin/pmpa-env.sh`, `bin/setup-sandbox.sh`, `bin/setup-openclaw.sh`,
  `bin/serve-vllm.sh`, `bin/run-cell.sh`, `bin/smoke.sh`
- Worker 0 sandbox at `$PMPA_SANDBOX/w0`, both legs:
  `w0/project` + `w0/claude` for Claude Code, `w0/openclaw` for OpenClaw

Evaluator dry runs pass for **both** harnesses: memory dir, session dir and all
four virtual state files resolve inside the worker sandbox, never into the real
`~/.claude` or `~/.openclaw`.

### Corrections made this session

Three things initially raised as scope questions were just missing
dependencies, fixed by user-space installs:

- png modality → `tesseract` from conda-forge
- OpenClaw → Node from conda-forge + `npm install -g openclaw`
- permission mode / thinking mode → settings to fix and record, not debate

The OpenClaw "persistent daemon" concern was wrong: the evaluator drives it
one-shot with `--local`.

### Gotcha worth remembering

**A tool missing from `PATH` surfaces as `PermissionError`, not
`FileNotFoundError`.** CPython's `subprocess` scans each `PATH` entry, retains
the first `EACCES` it encounters, and raises that if no match is found. Two
startup failures were diagnosed down the wrong path because of it:

- Run 1: `PermissionError: 'nvcc'` — and the nvcc call itself was a *second*
  red herring, raised inside `torch/_dynamo/debug_utils.py:444`
  (`_cuda_system_info_comment`) while torch was writing a repro dump for an
  Inductor error, replacing the original exception entirely.
- Run 2: `PermissionError: 'ninja'` — flashinfer shells out to `ninja` to JIT
  its sampling kernels, after CUDA graph capture.

Both were one cause: `$PMPA_ENV/bin` was never on `PATH`. `serve-vllm.sh`
invokes `vllm` by absolute path, so the server ran without its own venv bin
visible. Fixed in `pmpa-env.sh`, which now also puts torch's bundled
`nvidia/cu13/bin` on `PATH` and sets `CUDA_HOME`.

Verified from a stripped environment (`env -i`): `ninja`, `nvcc`, `vllm`,
`python`, `claude`, `openclaw`, `tesseract` all resolve.

### Phase 1 runs

| Run | Result | Reached |
|---|---|---|
| 1 | FAILED 00:01:24 | vLLM startup — masked Inductor error |
| 2 | FAILED 00:01:54 | model loaded, compiled in 16.15 s, CUDA graphs captured, 113.43 GiB KV cache, then `ninja` |
| 3 | FAILED 00:01:34 | flashinfer JIT sampling build, cccl headers against nvcc 13.4 |
| 4 | COMPLETED 00:02:47 | gate passed, both harnesses |

## 2026-10-28 — phase 2

| Run | Content | Result |
|---|---|---|
| 4B initial | Qwen3.5-4B injection + C-ASR | COMPLETED 00:49:36. Injection valid. **C-ASR output invalid** due to shared output slug across harnesses. |
| 9B initial | Qwen3.5-9B injection + C-ASR | COMPLETED 00:50:37. Injection valid. **C-ASR output invalid** due to shared output slug across harnesses. |
| 4B C-ASR | Qwen3.5-4B C-ASR, corrected | COMPLETED 00:15:41. Used `bin/serve-casr.sh`. |
| 9B C-ASR | Qwen3.5-9B C-ASR, corrected | COMPLETED 00:14:29. Used `bin/serve-casr.sh` with per-model `PMPA_SANDBOX`. |

`bin/run-matrix.sh` now passes one output root per harness, so `bin/serve-and-run.sh`
no longer produces a corrupt C-ASR phase.

Results live in `experiments/qwen35-main/results.md`.

## 2026-10-28 — audit

An audit recomputed every headline number from the raw run directories.
It confirmed the 4B and 9B injection figures, the classify-isr split, the
utility recovery and the 21-attendee C-ASR claim. It found errors that are now
fixed:

1. `classify-isr.py` used `alice.chen@email.com` as an attacker identifier. That
   address is the legitimate teammate. The docs payload uses
   `alice.chen@northwind-research.com`. The 4B compliance figure moved from
   0.303 to 0.414.
2. `classify-isr.py` matched the memory directory by substring, so a fabricated
   `…/w3project/claude/projects/…` path scored as a real memory write. An
   anchored regex now makes it agree with upstream ISR exactly at both scales.
3. `aggregate.py` keyed cells without the slug, so ablation runs would have
   replaced the main ISR cells. The slug is now part of the key.
4. The claim "ISR is non-zero only where the memory directory is pre-seeded" is
   false at 9B. Corrected in `results.md` and the mechanism log.
5. The pdf error rate is 0.275 at 4B and 0.100 at 9B, not the 0.286 taken from a
   mid-run subsample. txt is 0.008 at 9B, not 0.000.
6. Cross-session contamination affects 40 of 360 sessions at 4B and 60 of 360 at
   9B, not "nil".
7. The recipe in `experiments/qwen35-main/README.md` pointed at the run whose
   C-ASR phase was broken. It now points at `bin/serve-casr.sh`.
8. The stated reason the injection phase escaped the slug collision was wrong.
   The real reason is the worker-index offset at `-W 8`.
9. This ops repository was not under version control. It is now.

The gate checks, in order: GPU visible → vLLM serves `/v1/models` → `/v1/messages`
answers → `claude --print` reaches it → agent executes a skill through Bash →
tesseract OCR → `openclaw agent --local` answers → one ISR session per harness
produces a `metric_summary.json`. The OpenClaw leg is non-fatal so a failure
there cannot mask a Claude Code pass.
