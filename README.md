# Mythos Agentic AI

Mythos is an AI coding-agent backend for repository understanding, code editing,
patch verification, and model-agnostic serving.

The final architecture lives under `src/mythos`. The old numbered
architecture has been removed.

## What Works Now

- FastAPI gateway
- JWT auth middleware
- quota enforcement middleware
- Prometheus-style `/metrics` and structured JSON logs
- Model-agnostic backend adapter
- Scratch-first model foundation contracts for 7B/32B/70B/100B planning
- Project and session APIs
- Repository indexing
- AST-aware Python symbol, call, import, and mutation metadata
- local vector search for repository chunks
- Context building
- Durable TaskGraph runtime
- TaskGraph state machine: advance, complete, fail
- Tool command execution
- Defensive Semgrep/CodeQL verifier hooks
- hardened Docker sandbox contract
- Patch preview/apply/rollback
- preview/apply/verify/rollback patch loop
- Verifier runtime
- benchmark harness for coding/security tasks
- Native MVP tests

## Repository Layout

```text
src/mythos/
  agents/
  context/
  db/
  evaluation/
  gateway/
  guardrails/
  identity/
  indexing/
  learning/
  memory/
  model_ops/
  patches/
  planning/
  runtime/
  shared/
  tools/
  verifier/
tests/
scripts/
deploy/
docs/
```

## Quick Check

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\run_mythos_mvp_foundation.ps1
```

## Start Gateway

```powershell
python -m uvicorn src.mythos.gateway.api:app --host 127.0.0.1 --port 8090
```

## Run CLI

```powershell
python -m src.mythos.cli --prompt "fix auth bug" --workspace . --agent-backend-mode mock --no-verifier --no-security
```

## Repository Intelligence API

After indexing a project, inspect symbols and dependencies:

```powershell
Invoke-RestMethod http://127.0.0.1:8090/v1/projects/<project_id>/symbols
```

## TaskGraph Execution API

```powershell
Invoke-RestMethod -Method Post http://127.0.0.1:8090/v1/taskgraphs/<task_graph_id>/advance
Invoke-RestMethod -Method Post http://127.0.0.1:8090/v1/tasks/<task_id>/complete -Body '{"outputs":{}}' -ContentType 'application/json'
Invoke-RestMethod -Method Post http://127.0.0.1:8090/v1/tasks/<task_id>/fail -Body '{"error":"reason"}' -ContentType 'application/json'
```

## Mythos Scratch Model Serving

Set:

```powershell
$env:MYTHOS_MODEL_BACKEND = "mythos_serving"
$env:MYTHOS_MODEL_ENDPOINT = "http://127.0.0.1:8000/v1"
$env:MYTHOS_MODEL_NAME = "mythos-scratch"
```

Then serve a Mythos-owned scratch checkpoint on GPU hardware.

## Scratch Model Foundation

Mythos is scratch-only. Borrowed-model training and borrowed-model quality
baselines are not part of the architecture. The `mock` backend is only a test
double for plumbing checks.

```powershell
Invoke-RestMethod http://127.0.0.1:8090/v1/model/foundation/status
```

## Colab/Kaggle GPU Smoke

Run a real scratch-decoder forward/backward/checkpoint smoke test:

```bash
pip install -r requirements-linux-gpu.txt
python deploy/gpu/run_scratch_gpu_smoke.py --device cuda --steps 2 --sequence-length 64
```

Run the longer scratch pretraining loop:

```bash
python -m src.mythos.model_ops.tokenizer_pipeline \
  --input data/training/clean.jsonl \
  --tokenizer-out artifacts/mythos/tokenizer/tokenizer.json \
  --shards-out artifacts/mythos/shards \
  --vocab-size 64000 \
  --sequence-length 128

python -m src.mythos.model_ops.pretrain_loop \
  --device cuda \
  --manifest artifacts/mythos/shards/manifest.json \
  --steps 100 \
  --batch-size 2 \
  --sequence-length 128 \
  --gradient-accumulation-steps 4 \
  --dtype bf16
```

Or one command:

```bash
python deploy/gpu/run_pretraining_pipeline.py \
  --input data/training/clean.jsonl \
  --device cuda \
  --steps 100 \
  --sequence-length 128
```

Output:

- `artifacts/mythos/gpu-smoke/gpu_smoke_report.json`
- `artifacts/mythos/gpu-smoke/checkpoint/model.pt`
- `artifacts/mythos/gpu-smoke/checkpoint_manifest.json`

## Defensive Data Pipeline

Allowlisted internet ingestion:

```bash
python -m src.mythos.learning.web_ingest \
  --sources config/data_sources.defensive.sample.json \
  --output data/training/raw_web.jsonl \
  --max-docs 1000 \
  --delay-seconds 1.0
```

Quality gate:

```bash
python - <<'PY'
from src.mythos.learning.quality import DatasetQualityGate
print(DatasetQualityGate().filter_jsonl("data/training/raw_web.jsonl", "data/training/clean.jsonl"))
PY
```

## Production Checks

```powershell
python -m src.mythos.evaluation.release_gate
python -m src.mythos.db.migration_runner --database-url $env:MYTHOS_DATABASE_URL --dry-run
```

Production API hardening requires:

```powershell
$env:MYTHOS_AUTH_ENABLED = "1"
$env:MYTHOS_JWT_SECRET = "<long-random-secret>"
$env:MYTHOS_ALLOW_TOKEN_ISSUE = "0"
$env:MYTHOS_QUOTA_ENABLED = "1"
$env:MYTHOS_REDIS_URL = "redis://redis:6379/0"
```

## Final Rule

All new production code belongs under `src/mythos`.
