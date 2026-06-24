#!/usr/bin/env bash
set -euo pipefail
PROFILE="${MODEL_PROFILE:-qwen2.5-coder-7b}"
python src/phase17/gpu_readiness.py --print-sft-command --profile "$PROFILE" | tee artifacts/phase17/last_sft_command.txt
exec $(python src/phase17/gpu_readiness.py --print-sft-command --profile "$PROFILE")
