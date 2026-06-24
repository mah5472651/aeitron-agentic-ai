#!/usr/bin/env bash
set -euo pipefail
PROFILE="${MODEL_PROFILE:-qwen2.5-coder-7b}"
python src/phase17/gpu_readiness.py --print-grpo-command --profile "$PROFILE" | tee artifacts/phase17/last_grpo_command.txt
exec $(python src/phase17/gpu_readiness.py --print-grpo-command --profile "$PROFILE")
