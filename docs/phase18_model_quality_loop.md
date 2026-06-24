# Phase 18 Real Model Quality Loop

Phase 18 evaluates the connected real backend using the exact scorecard logic
from Phase 14, analyzes failures, and exports reviewed training candidates.

## What It Builds

- Real Qwen scorecard runner.
- Failure analyzer by category, phase, and issue type.
- Training candidate promotion gate.
- SFT candidate JSONL.
- GRPO preference candidate JSONL.
- Dashboard report for the chat API.
- Chat API endpoint: `/v1/model-quality/latest`.
- Architecture audit dry-run check.

## Run Quick Balanced Suite

```powershell
.\scripts\run_phase18_model_quality.ps1
```

Defaults:

- endpoint: `http://127.0.0.1:8016/v1`
- model: `Qwen/Qwen2.5-Coder-0.5B-Instruct`
- max tasks: `5`
- max new tokens per task: `180` for CPU-friendly local checks
- one task per scorecard category
- sandbox smoke enabled unless `PHASE18_RUN_SANDBOX=0`

Override the local backend:

```powershell
$env:PHASE18_MODEL_ENDPOINT = "http://127.0.0.1:8000/v1"
$env:PHASE18_MODEL_NAME = "Qwen/Qwen2.5-Coder-7B-Instruct"
.\scripts\run_phase18_model_quality.ps1
```

Validate wiring without calling the model:

```powershell
$env:PHASE18_DRY_RUN = "1"
.\scripts\run_phase18_model_quality.ps1
```

## Run Full 90-Task Scorecard

```powershell
$env:PHASE18_FULL = "1"
.\scripts\run_phase18_model_quality.ps1
```

The full run is slow on CPU. It is intended for a stronger local GPU backend or
remote vLLM endpoint.

## Outputs

```text
artifacts/phase18/model-quality-latest.json
artifacts/phase18/phase18-qwen-local.md
artifacts/phase18/phase18-qwen-local-reviewed-sft-candidates.jsonl
artifacts/phase18/phase18-qwen-local-reviewed-grpo-candidates.jsonl
```

Candidate rows are not automatically approved training data. They are marked as:

```text
candidate_needs_human_or_verifier_review
```

That keeps the data-quality gate intact.

## Why This Matters

This closes the loop between real model behavior and architecture improvement:

1. Run real/local/vLLM backend.
2. Score exact golden tasks.
3. Cluster failures by category, phase, and issue type.
4. Export review-required SFT and GRPO candidates.
5. Feed approved rows into later SFT/GRPO training.

The current CPU Qwen model is only for plumbing and local behavior checks. The
same script is intended to target future 7B-32B or larger vLLM backends by
changing the endpoint and model name.
