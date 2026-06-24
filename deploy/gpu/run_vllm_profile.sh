#!/usr/bin/env bash
set -euo pipefail
PROFILE="${MODEL_PROFILE:-qwen2.5-coder-7b}"
PORT="${VLLM_PORT:-8000}"
python src/phase17/gpu_readiness.py --print-vllm-command --profile "$PROFILE" --port "$PORT" | tee artifacts/phase17/last_vllm_command.txt
exec $(python src/phase17/gpu_readiness.py --print-vllm-command --profile "$PROFILE" --port "$PORT")
