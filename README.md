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
- config-driven checkpoint eval reports
- token-level cybersecurity/code/general/agentic data mixer
- scratch-checkpoint SFT and native PyTorch DPO-style alignment loops
- refusal and over-refusal safety evaluation
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

## Training Control Plane

Checkpoint eval, data mixing, and alignment are all scratch-checkpoint only.
Protected benchmarks stay eval/holdout and are not mixed into training.

```powershell
python -m src.mythos.learning.mixer --inputs data\training\clean.jsonl --config config\mix_ratios.json --experiment baseline_70_15_15 --output-dir artifacts\mythos\mix-baseline

python -m src.mythos.evaluation.eval_runner --checkpoint-manifest artifacts\mythos\train\checkpoint_manifest.json --schedule config\eval_schedule.json --output-dir artifacts\mythos\eval --tokenizer-path artifacts\mythos\tokenizer\tokenizer.json --device cpu

python -m src.mythos.alignment.build_sft_dataset --input-tasks data\training\tasks.jsonl --policy config\alignment_policy.json --output artifacts\mythos\alignment\sft.jsonl

python -m src.mythos.alignment.generate_preferences --prompts data\training\prompts.jsonl --candidate-outputs data\training\candidates.jsonl --output artifacts\mythos\alignment\pairs.jsonl

python -m src.mythos.alignment.train_sft --checkpoint-manifest artifacts\mythos\train\checkpoint_manifest.json --dataset artifacts\mythos\alignment\sft.jsonl --tokenizer-path artifacts\mythos\tokenizer\tokenizer.json --output-dir artifacts\mythos\alignment\sft-run --device cpu --steps 10

python -m src.mythos.alignment.train_dpo --policy-checkpoint artifacts\mythos\alignment\sft-run\checkpoint_manifest.json --reference-checkpoint artifacts\mythos\train\checkpoint_manifest.json --pairs artifacts\mythos\alignment\pairs.jsonl --tokenizer-path artifacts\mythos\tokenizer\tokenizer.json --output-dir artifacts\mythos\alignment\dpo-run --device cpu --steps 10

python -m src.mythos.alignment.safety_eval --checkpoint-manifest artifacts\mythos\alignment\dpo-run\checkpoint_manifest.json --tokenizer-path artifacts\mythos\tokenizer\tokenizer.json --policy config\alignment_policy.json --output-dir artifacts\mythos\alignment\safety --device cpu
```

Reports:

- `eval_report.json` and `eval_report.md`
- `mix_manifest.json`
- `ablation_report.json`
- `sft_dataset_report.json`
- `preference_pair_report.json`
- `alignment_training_report.json`
- `safety_eval_report.json`

## Production Hardening Gates

Local deterministic gates:

```powershell
python -m src.mythos.db.migration_runner --database-url postgresql://mythos:pass@localhost:5432/mythos --dry-run
python -m src.mythos.deployment.k8s_validate --output-dir artifacts\mythos\k8s-validation
python -m src.mythos.learning.storage --uri local://artifacts/mythos/object-store --work-dir artifacts\mythos\object-store-lifecycle
python -m src.mythos.learning.dataset_validation --inputs data\training\clean.jsonl --output-dir artifacts\mythos\dataset-validation --min-records 100000
python -m src.mythos.evaluation.benchmark_suites --suite swe swe_bench_style data\eval\swe_style.jsonl --suite cyber cyberseceval_style data\eval\cyber.jsonl --output-dir artifacts\mythos\benchmark-suites
python -m src.mythos.security.audit --no-bandit --output-dir artifacts\mythos\security-audit
```

Real production commands:

```powershell
alembic upgrade head
python -m src.mythos.deployment.k8s_validate --kubectl-dry-run --output-dir artifacts\mythos\k8s-validation
python -m src.mythos.learning.storage --uri s3://mythos-datasets/pretraining --endpoint-url http://localhost:9000 --work-dir artifacts\mythos\s3-lifecycle
python deploy\gpu\run_10k_training_validation.py --manifest artifacts\mythos\shards\manifest.json --device cuda --steps 10000
```

The production stack includes Prometheus, Grafana, and optional OpenTelemetry:

```powershell
docker compose --env-file deploy\prod\.env.example -f deploy\prod\docker-compose.yml --profile monitoring up
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

Allowlisted one-shot ingestion:

```bash
python -m src.mythos.learning.web_ingest \
  --sources config/data_sources.ultimate.json \
  --output data/training/raw_web.jsonl \
  --max-docs 1000 \
  --delay-seconds 1.0
```

Persistent million-scale ingestion with resume/retry, URL discovery, provenance,
content deduplication, per-domain throttling, and clean JSONL sharding:

```bash
python -m src.mythos.learning.data_engine \
  --sources config/data_sources.ultimate.json \
  --frontier artifacts/mythos/data-engine/frontier.sqlite3 \
  --raw-output-dir artifacts/mythos/data-engine/raw \
  --clean-output-dir artifacts/mythos/data-engine/clean \
  --max-docs 1000000 \
  --workers 64 \
  --max-depth 2 \
  --delay-seconds 1.0 \
  --shard-rows 10000
```

Postgres-backed distributed frontier:

```bash
python -m src.mythos.learning.data_engine \
  --sources config/data_sources.ultimate.json \
  --frontier-backend postgres \
  --postgres-dsn "$MYTHOS_DATABASE_URL" \
  --raw-output-dir artifacts/mythos/data-engine/raw \
  --clean-output-dir artifacts/mythos/data-engine/clean \
  --max-docs 1000000 \
  --workers 64
```

One command for `crawl -> clean -> shard -> train`:

```bash
python -m src.mythos.learning.data_pipeline \
  --sources config/data_sources.ultimate.json \
  --dataset-id mythos-defensive-coding-corpus \
  --work-dir artifacts/mythos/data-pipeline \
  --frontier-backend postgres \
  --postgres-dsn "$MYTHOS_DATABASE_URL" \
  --object-store-uri s3://mythos-datasets/pretraining \
  --object-store-endpoint-url "$S3_ENDPOINT_URL" \
  --max-docs 1000000 \
  --workers 64 \
  --max-depth 2 \
  --vocab-size 64000 \
  --sequence-length 2048 \
  --shard-token-count 1000000 \
  --train-device cuda \
  --train-steps 10000 \
  --train-batch-size 2 \
  --gradient-accumulation-steps 16 \
  --dtype bf16
```

Distributed crawler workers:

```bash
docker compose -f deploy/prod/docker-compose.yml --profile data up --scale crawler-worker=8 crawler-worker
```

Supervised long-running data collection:

```bash
docker compose -f deploy/prod/docker-compose.yml --profile data up crawler-supervisor
python -m src.mythos.learning.supervisor \
  --sources config/data_sources.ultimate.json \
  --postgres-dsn "$MYTHOS_DATABASE_URL" \
  --raw-output-dir artifacts/mythos/data-engine/raw \
  --clean-output-dir artifacts/mythos/data-engine/clean \
  --object-store-uri s3://mythos-datasets/pretraining \
  --worker-replicas 8 \
  --async-workers 64
```

Monitoring dashboard:

```bash
docker compose -f deploy/prod/docker-compose.yml --profile monitoring up prometheus
```

Production readiness gate:

```bash
python -m src.mythos.learning.production_check \
  --sources config/data_sources.ultimate.json \
  --frontier-backend postgres \
  --postgres-dsn "$MYTHOS_DATABASE_URL" \
  --object-store-uri s3://mythos-datasets/pretraining \
  --production \
  --worker-replicas 8 \
  --async-workers 64
```

Prepare the first serious 100k-1M data run:

```bash
python -m src.mythos.learning.run_plan \
  --sources config/data_sources.ultimate.json \
  --output-dir artifacts/mythos/data-runs/first-serious-run \
  --target-documents 1000000 \
  --target-days 7 \
  --postgres-dsn "$MYTHOS_DATABASE_URL" \
  --object-store-uri s3://mythos-datasets/pretraining \
  --worker-replicas 8 \
  --async-workers 64
```

Training resource priority catalog:

```bash
python -m src.mythos.learning.resource_catalog \
  --catalog config/data_sources.ultimate.json \
  --output artifacts/mythos/resource_catalog_report.json
```

The catalog keeps all 45 external cybersecurity/agentic-coding resources in one
place. The top six priority groups are surfaced first, while protected benchmark
resources such as SWE-bench Verified, HumanEval, MBPP, and CTF benchmarks stay
as evaluation/contamination holdouts instead of raw pretraining rows.

Cluster capacity planning:

```bash
python -m src.mythos.learning.capacity \
  --target-documents 1000000000 \
  --target-days 30 \
  --worker-replicas 32 \
  --async-workers-per-replica 32
```

Kubernetes production data platform:

```bash
kubectl apply -f deploy/k8s/secrets.example.yaml
kubectl apply -f deploy/k8s/postgres-redis.yaml
kubectl apply -f deploy/k8s/minio.yaml
kubectl apply -f deploy/k8s/data-worker.yaml
kubectl apply -f deploy/k8s/data-supervisor.yaml
kubectl apply -f deploy/k8s/data-worker-hpa.yaml
kubectl apply -f deploy/k8s/data-network-policy.yaml
kubectl apply -f deploy/k8s/data-pipeline-job.yaml
```

Pipeline outputs include:

- contamination report
- quality inspection report
- source quality report
- extracted task JSONL
- automated review decisions
- approved task JSONL
- benchmark/data feedback report
- tokenizer and token-shard manifest
- dataset version manifest
- append-only dataset ledger
- local HTML dashboard at `artifacts/mythos/data-pipeline/dashboard.html`
- optional S3/MinIO uploads

Manual/automated review and feedback:

```bash
python -m src.mythos.learning.governance --store artifacts/mythos/governance report

python -m src.mythos.learning.governance --store artifacts/mythos/governance submit-source \
  --source-name portswigger-web-security-academy \
  --category authorized_security_testing_labs \
  --url https://portswigger.net/web-security \
  --license review-required \
  --evidence-url https://portswigger.net/web-security \
  --requested-by security-team \
  --justification "High-value authorized web security education source"

python -m src.mythos.learning.review \
  --input artifacts/mythos/data-pipeline/tasks/tasks.jsonl \
  --decisions-out artifacts/mythos/data-pipeline/reports/task_review_decisions.jsonl \
  --approved-out artifacts/mythos/data-pipeline/tasks/approved_tasks.jsonl

python -m src.mythos.learning.feedback \
  --output artifacts/mythos/data-pipeline/reports/feedback_report.json \
  --quality-report artifacts/mythos/data-pipeline/reports/quality_report.json \
  --review-report artifacts/mythos/data-pipeline/reports/task_review_report.json
```

The data engine is defensive and allowlist-first. It is for public documentation,
licensed code, security guidance, benchmark corpora, and approved repository
mirrors; it does not perform exploit execution or unauthorized collection.

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

