#!/usr/bin/env bash
# Shared paths and environment for the PMPA reproduction.
# Source this; do not execute it.

_PMPA_BIN_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PMPA_OPS="${PMPA_OPS:-$(dirname "$_PMPA_BIN_DIR")}"
unset _PMPA_BIN_DIR
PMPA_REPO="${PMPA_REPO:-$PMPA_OPS/repo}"
PMPA_SCRATCH="${PMPA_SCRATCH:-$PMPA_OPS/work}"
PMPA_ENV="${PMPA_ENV:-$PMPA_SCRATCH/env}"
PMPA_SANDBOX="${PMPA_SANDBOX:-$PMPA_SCRATCH/sandbox}"
PMPA_RUNS="${PMPA_RUNS:-$PMPA_SCRATCH/runs}"
# Prefix holding tesseract (png modality OCR) and node (OpenClaw).
PMPA_TOOLS="${PMPA_TOOLS:-$PMPA_SCRATCH/tools}"
# $PMPA_ENV/bin holds `ninja`, which flashinfer shells out to for its JIT
# sampling kernels; without it on PATH vLLM dies after CUDA graph capture.
# Note: a tool missing from PATH surfaces as PermissionError, not
# FileNotFoundError, because CPython's subprocess re-raises the first EACCES it
# hits while scanning PATH entries.
# ~/.local/bin holds the `claude` binary.
export PATH="$PMPA_ENV/bin:$PMPA_TOOLS/bin:$HOME/.local/bin:$PATH"

# vLLM server
PMPA_VLLM_HOST="${PMPA_VLLM_HOST:-127.0.0.1}"
PMPA_VLLM_PORT="${PMPA_VLLM_PORT:-8000}"
PMPA_SERVED_MODEL="${PMPA_SERVED_MODEL:-qwen3.5-4b}"
PMPA_HF_MODEL="${PMPA_HF_MODEL:-Qwen/Qwen3.5-4B}"

export HF_HOME="${HF_HOME:-$PMPA_SCRATCH/hf_cache}"
export VLLM_CACHE_ROOT="${VLLM_CACHE_ROOT:-$PMPA_SCRATCH/vllm_cache}"

# torch ships a version-matched nvcc but never puts it on PATH. Without it,
# Inductor's error-reporting path (_cuda_system_info_comment -> `nvcc --version`)
# raises PermissionError and *replaces* the real compile error.
_pmpa_cuda="$PMPA_ENV/lib/python3.12/site-packages/nvidia/cu13"
if [[ -x "$_pmpa_cuda/bin/nvcc" ]]; then
  export CUDA_HOME="${CUDA_HOME:-$_pmpa_cuda}"
  export PATH="$_pmpa_cuda/bin:$PATH"
fi
unset _pmpa_cuda

# flashinfer 0.7.0.post1 JIT-builds its sampling kernels at startup and fails:
# its bundled cccl headers reject the nvcc above (13.4) against torch's cu130
# build — "CUDA compiler and CUDA toolkit headers are incompatible".
# vLLM falls back to its PyTorch top-k/top-p path, which is the same
# sampling math, so this removes a JIT dependency at a small speed cost.
# Flag verified present in this build at vllm/envs.py:878.
export VLLM_USE_FLASHINFER_SAMPLER="${VLLM_USE_FLASHINFER_SAMPLER:-0}"

# The same header incompatibility breaks flashinfer's multi-GPU all-reduce,
# which only a tensor-parallel run reaches (trtllm_mnnvl_allreduce at
# tensor-parallel-size 4). Default is 1 (vllm/envs.py:1970).
export VLLM_ALLREDUCE_USE_FLASHINFER="${VLLM_ALLREDUCE_USE_FLASHINFER:-0}"

# Keep compile caches in the work directory.
export TORCHINDUCTOR_CACHE_DIR="${TORCHINDUCTOR_CACHE_DIR:-$PMPA_SCRATCH/torchinductor_cache}"
export TRITON_CACHE_DIR="${TRITON_CACHE_DIR:-$PMPA_SCRATCH/triton_cache}"
# image-reader defaults this to $PWD/.tmp (skills/image-reader/scripts/cli.py:26);
# pin it to scratch so OCR scratch files never land in project storage.
export IMAGE_READER_TEMP_DIR="${IMAGE_READER_TEMP_DIR:-$PMPA_SCRATCH/tmp/image-reader}"

# Ensure loopback bypasses any configured HTTP proxy so `claude` reaches the
# local vLLM server directly.
export NO_PROXY="${NO_PROXY:+$NO_PROXY,}127.0.0.1,localhost,::1"
export no_proxy="$NO_PROXY"

# Point Claude Code at the local vLLM Anthropic-compatible endpoint.
pmpa_claude_env() {
  export ANTHROPIC_BASE_URL="http://${PMPA_VLLM_HOST}:${PMPA_VLLM_PORT}"
  export ANTHROPIC_AUTH_TOKEN="${ANTHROPIC_AUTH_TOKEN:-pmpa-local}"
  export ANTHROPIC_MODEL="$PMPA_SERVED_MODEL"
  export ANTHROPIC_SMALL_FAST_MODEL="$PMPA_SERVED_MODEL"
  export CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1
  unset ANTHROPIC_API_KEY
}

export PMPA_OPS PMPA_REPO PMPA_SCRATCH PMPA_ENV PMPA_SANDBOX PMPA_RUNS PMPA_TOOLS
export PMPA_VLLM_HOST PMPA_VLLM_PORT PMPA_SERVED_MODEL PMPA_HF_MODEL
