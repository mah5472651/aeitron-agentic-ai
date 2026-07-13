# Aeitron Agentic AI

Aeitron is an AI coding-agent backend for repository understanding, code editing,
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
- scratch-only tokenizer, sharding, and pretraining control plane
- safety, security, and regression evaluation gates
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

## Aeitron Scratch Model Serving

Set:

```powershell
$env:MYTHOS_MODEL_BACKEND = "aeitron_serving"
$env:MYTHOS_MODEL_ENDPOINT = "http://127.0.0.1:8000/v1"
$env:MYTHOS_MODEL_NAME = "aeitron-scratch"
```

Then serve a Aeitron-owned scratch checkpoint on GPU hardware.

## Scratch Model Foundation

Aeitron is scratch-only. Borrowed-model training and borrowed-model quality
baselines are not part of the architecture. The `mock` backend is only a test
double for plumbing checks.

```powershell
Invoke-RestMethod http://127.0.0.1:8090/v1/model/foundation/status
```

## Training Control Plane

Aeitron is scratch-training only. The control plane supports checkpoint eval,
token-level data mixing, tokenizer/shard preparation, and pretraining gates.
It does not include post-training adaptation or external foundation-model
training paths. Protected benchmarks stay eval/holdout and are not mixed into
training.

```powershell
python -m src.mythos.learning.mixer --inputs data\training\clean.jsonl --config config\mix_ratios.json --experiment baseline_70_15_15 --output-dir artifacts\\aeitron\mix-baseline

python -m src.mythos.evaluation.eval_runner --checkpoint-manifest artifacts\\aeitron\train\checkpoint_manifest.json --schedule config\eval_schedule.json --output-dir artifacts\\aeitron\eval --tokenizer-path artifacts\\aeitron\tokenizer\tokenizer.json --device cpu
```

Reports:

- `eval_report.json` and `eval_report.md`
- `mix_manifest.json`
- `ablation_report.json`

## Production Hardening Gates

Local deterministic gates:

```powershell
python -m src.mythos.db.migration_runner --database-url postgresql://aeitron:pass@localhost:5432/aeitron --dry-run
python -m src.mythos.deployment.k8s_validate --output-dir artifacts\\aeitron\k8s-validation
python -m src.mythos.learning.storage --uri local://artifacts/aeitron/object-store --work-dir artifacts\\aeitron\object-store-lifecycle
python -m src.mythos.learning.dataset_validation --inputs data\training\clean.jsonl --output-dir artifacts\\aeitron\dataset-validation --min-records 100000
python -m src.mythos.evaluation.benchmark_suites --suite swe swe_bench_style data\eval\swe_style.jsonl --suite cyber cyberseceval_style data\eval\cyber.jsonl --output-dir artifacts\\aeitron\benchmark-suites
python -m src.mythos.security.audit --no-bandit --output-dir artifacts\\aeitron\security-audit
```

Real production commands:

```powershell
alembic upgrade head
python -m src.mythos.deployment.k8s_validate --kubectl-dry-run --output-dir artifacts\\aeitron\k8s-validation
python -m src.mythos.learning.storage --uri s3://aeitron-datasets/pretraining --endpoint-url http://localhost:9000 --work-dir artifacts\\aeitron\s3-lifecycle
python deploy\gpu\run_10k_training_validation.py --manifest artifacts\\aeitron\shards\manifest.json --device cuda --steps 10000
```

The production stack includes Prometheus, Grafana, and optional OpenTelemetry:

```powershell
docker compose --env-file deploy\prod\.env.example -f deploy\prod\docker-compose.yml --profile monitoring up
```

## Colab/Kaggle GPU Smoke

Run a real scratch-decoder forward/backward/checkpoint smoke test:

```bash
pip install -r requirements-kaggle-smoke.txt
python deploy/gpu/run_scratch_gpu_smoke.py --device cuda --steps 2 --sequence-length 64
```

Long Kaggle runs now emit live structured progress lines and write
`progress.jsonl` inside the run directory:

```bash
PYTHONUNBUFFERED=1 python -u deploy/gpu/run_real_data_training_pipeline.py \
  --sources config/data_sources.ultimate.json \
  --work-dir artifacts/aeitron/kaggle-real-data-smoke \
  --target-records 1000 \
  --max-docs 3000 \
  --steps 200 \
  --device cuda \
  --dtype fp16 \
  --progress-every-docs 10 \
  --progress-every-steps 10
```

Watch the file from another Kaggle cell:

```bash
tail -n 80 artifacts/aeitron/kaggle-real-data-smoke/progress.jsonl
```

Kaggle validation preset, designed to prove the full data -> tokenizer -> shard
-> GPU train -> eval path without pretending to be production scale:

```bash
PYTHONUNBUFFERED=1 python -u deploy/gpu/run_real_data_training_pipeline.py \
  --sources config/data_sources.ultimate.json \
  --work-dir artifacts/aeitron/real-data-validation-v1 \
  --kaggle-validation \
  --steps 1000 \
  --sequence-length 128 \
  --batch-size 2 \
  --gradient-accumulation-steps 8 \
  --validation-interval 100 \
  --validation-batches 4 \
  --early-stopping-patience 5 \
  --progress-to-stdout
```

Strict 10k-step real-data validation:

```bash
PYTHONUNBUFFERED=1 python -u deploy/gpu/run_real_data_training_pipeline.py \
  --sources config/data_sources.ultimate.json \
  --work-dir artifacts/aeitron/real-data-10k-strict-v1 \
  --target-records 10000 \
  --min-training-rows 5000 \
  --min-train-tokens 2000000 \
  --max-docs 16000 \
  --max-bytes-per-doc 250000 \
  --workers 6 \
  --max-depth 2 \
  --delay-seconds 0.35 \
  --steps 10000 \
  --sequence-length 128 \
  --batch-size 2 \
  --gradient-accumulation-steps 8 \
  --validation-interval 250 \
  --validation-batches 8 \
  --early-stopping-patience 12 \
  --min-training-quality-score 0.62 \
  --min-training-average-quality-score 0.62 \
  --min-source-reputation-score 0.50 \
  --eval-holdout-fraction 0.02 \
  --max-source-fraction 0.25 \
  --progress-path artifacts/aeitron/real-data-10k-strict-v1/progress.jsonl \
  --progress-to-stdout \
  --progress-every-docs 10 \
  --progress-every-steps 25

python deploy/gpu/run_checkpoint_comparison.py \
  --training-report artifacts/aeitron/real-data-10k-strict-v1/reports/real_data_training_report.json \
  --output-dir artifacts/aeitron/real-data-10k-strict-v1/reports/checkpoint_compare \
  --device cuda
```

Inspect any Kaggle/Colab run and get the next recommended action:

```bash
python deploy/gpu/inspect_real_data_run.py \
  --work-dir artifacts/aeitron/real-data-validation-v1
```

Run the longer scratch pretraining loop:

```bash
python -m src.mythos.model_ops.tokenizer_pipeline \
  --input data/training/clean.jsonl \
  --tokenizer-out artifacts/aeitron/tokenizer/tokenizer.json \
  --shards-out artifacts/aeitron/shards \
  --vocab-size 64000 \
  --sequence-length 128

python -m src.mythos.model_ops.pretrain_loop \
  --device cuda \
  --manifest artifacts/aeitron/shards/manifest.json \
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

- `artifacts/aeitron/gpu-smoke/gpu_smoke_report.json`
- `artifacts/aeitron/gpu-smoke/checkpoint/model.pt`
- `artifacts/aeitron/gpu-smoke/checkpoint_manifest.json`

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
  --frontier artifacts/aeitron/data-engine/frontier.sqlite3 \
  --raw-output-dir artifacts/aeitron/data-engine/raw \
  --clean-output-dir artifacts/aeitron/data-engine/clean \
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
  --raw-output-dir artifacts/aeitron/data-engine/raw \
  --clean-output-dir artifacts/aeitron/data-engine/clean \
  --max-docs 1000000 \
  --workers 64
```

One command for `crawl -> clean -> shard -> train`:

```bash
python -m src.mythos.learning.data_pipeline \
  --sources config/data_sources.ultimate.json \
  --dataset-id aeitron-defensive-coding-corpus \
  --work-dir artifacts/aeitron/data-pipeline \
  --frontier-backend postgres \
  --postgres-dsn "$MYTHOS_DATABASE_URL" \
  --object-store-uri s3://aeitron-datasets/pretraining \
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
  --raw-output-dir artifacts/aeitron/data-engine/raw \
  --clean-output-dir artifacts/aeitron/data-engine/clean \
  --object-store-uri s3://aeitron-datasets/pretraining \
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
  --object-store-uri s3://aeitron-datasets/pretraining \
  --production \
  --worker-replicas 8 \
  --async-workers 64
```

Prepare the first serious 100k-1M data run:

```bash
python -m src.mythos.learning.run_plan \
  --sources config/data_sources.ultimate.json \
  --output-dir artifacts/aeitron/data-runs/first-serious-run \
  --target-documents 1000000 \
  --target-days 7 \
  --postgres-dsn "$MYTHOS_DATABASE_URL" \
  --object-store-uri s3://aeitron-datasets/pretraining \
  --worker-replicas 8 \
  --async-workers 64
```

Training resource priority catalog:

```bash
python -m src.mythos.learning.resource_catalog \
  --catalog config/data_sources.ultimate.json \
  --output artifacts/aeitron/resource_catalog_report.json
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
- local HTML dashboard at `artifacts/aeitron/data-pipeline/dashboard.html`
- optional S3/MinIO uploads

Manual/automated review and feedback:

```bash
python -m src.mythos.learning.governance --store artifacts/aeitron/governance report

python -m src.mythos.learning.governance --store artifacts/aeitron/governance submit-source \
  --source-name portswigger-web-security-academy \
  --category authorized_security_testing_labs \
  --url https://portswigger.net/web-security \
  --license review-required \
  --evidence-url https://portswigger.net/web-security \
  --requested-by security-team \
  --justification "High-value authorized web security education source"

python -m src.mythos.learning.review \
  --input artifacts/aeitron/data-pipeline/tasks/tasks.jsonl \
  --decisions-out artifacts/aeitron/data-pipeline/reports/task_review_decisions.jsonl \
  --approved-out artifacts/aeitron/data-pipeline/tasks/approved_tasks.jsonl

python -m src.mythos.learning.feedback \
  --output artifacts/aeitron/data-pipeline/reports/feedback_report.json \
  --quality-report artifacts/aeitron/data-pipeline/reports/quality_report.json \
  --review-report artifacts/aeitron/data-pipeline/reports/task_review_report.json
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


