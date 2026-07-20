# Aeitron Agentic AI

Aeitron is an AI coding-agent backend for repository understanding, code editing,
patch verification, and model-agnostic serving.

The final architecture lives under `src/aeitron`. The old numbered
architecture has been removed.

## Operating Roadmap

Aeitron follows this roadmap for every future change:

- Scratch-only model development. Do not add external foundation-model fine-tuning, SFT, DPO, GRPO, LoRA, QLoRA, or RLHF paths.
- Production-grade code only: explicit validation, fail-fast dependency checks, secure defaults, no placeholder success paths, and no fake readiness claims.
- Coding-agent performance first: repository indexing, context packing, TaskGraph execution, patch generation, verification, and benchmark feedback get priority over impressive but unused abstractions.
- Cybersecurity scope stays governed: approved sources, defensive analysis, authorized labs/CTFs/eval material, security patch generation, and verification. No autonomous live-target attack workflow.
- Data quality before scale: source reputation, license/provenance, contamination gates, deduplication, task extraction, review queues, and benchmark holdouts must run before tokenizer/sharding/training.
- Production readiness is evidence-based: local smoke, Kaggle/Colab validation, and cluster production are separate statuses. Anything needing Redis/Postgres/S3/Qdrant/Docker/CUDA/benchmarks must say so honestly.
- Keep the architecture consolidated. Avoid new phase explosion and tiny wrapper files unless separation is required for security, testing, deployment, or clear ownership.
- Production-critical configs are strict contracts, not loose knobs:
  `config/mix_ratios.json`, `config/eval_schedule.json`,
  `config/active_model_profile.json`, `config/security_audit_excludes.json`,
  and `config/verifier_policy.json` are validated before runtime use.

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
- concurrent dependency-ready TaskGraph workers with leases, timeout, retry, and cancellation
- typed agent packets, durable message history, versioned shared blackboard
- peer challenge, critic, verifier, and bounded three-revision reflection protocol
- normalized failure clustering and verified repair dataset candidates
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
src/aeitron/
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
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\run_aeitron_mvp_foundation.ps1
```

## Start Gateway

```powershell
python -m uvicorn src.aeitron.gateway.api:app --host 127.0.0.1 --port 8090
```

## Run CLI

```powershell
python -m src.aeitron.cli --prompt "fix auth bug" --workspace . --agent-backend-mode mock --no-verifier --no-security
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
Invoke-RestMethod -Method Post http://127.0.0.1:8090/v1/taskgraphs/<task_graph_id>/cancel
```

The native worker pool runs every dependency-ready node up to its configured
concurrency limit. It writes `proposal`, `evidence`, `challenge`, `review`, and
`decision` packets to durable history. Run-scoped facts and artifacts use an
optimistically locked blackboard; evidence is immutable. Only a verifier-backed
accepted negotiation can be promoted into Unified Memory.

Collaboration inspection endpoints:

```text
POST /v1/agent/messages
GET  /v1/agent/runs/{run_id}/messages
PUT  /v1/agent/blackboard
GET  /v1/agent/runs/{run_id}/blackboard
GET  /v1/projects/{project_id}/failure-clusters
```

After applying Postgres migrations, prove durable contention and CAS behavior:

```powershell
python -m src.aeitron.runtime.collaboration --postgres-proof `
  --database-url "$env:AEITRON_DATABASE_URL" `
  --output-dir artifacts\aeitron\agent-collaboration-proof
```

Production readiness consumes the generated proof report. Configuration alone
does not mark collaboration persistence production-ready.

## Verified Coding-Agent Workflow

One command now runs the complete architect-to-verifier workflow:

```text
architect plan -> context -> coder patch
  -> sandbox test || Semgrep/CodeQL review || performance review
  -> critic -> verifier -> bounded revision (maximum 3)
  -> accepted patch apply | rejected patch rollback
```

Candidate edits are applied only to an ephemeral repository copy. The registered
workspace remains unchanged until the verifier has test and defensive-security
evidence and confidence is at least the configured threshold.

```powershell
$body = @{
  project_id = "<project-id>"
  prompt = "Fix the authentication regression and verify it"
  verification_commands = @(, @("python", "-m", "pytest", "-q"))
  policy_mode = "strict"
  max_revisions = 3
  apply_on_accept = $true
  require_sandbox = $true
  run_semgrep = $true
  fail_on_scanner_unavailable = $true
} | ConvertTo-Json -Depth 8

Invoke-RestMethod -Method Post http://127.0.0.1:8090/v1/agent/execute `
  -Headers @{Authorization = "Bearer <token>"} `
  -ContentType "application/json" -Body $body
```

Strict mode fails before model work if the private Aeitron serving backend,
Docker engine, or required scanner is unavailable. It never falls back to host
execution.

Run a production repository scorecard:

```powershell
$env:AEITRON_SCORECARD_REPO_ROOTS = "D:\approved-agent-eval-repos"
python -m src.aeitron.evaluation.agent_scorecard `
  --tasks D:\approved-agent-eval-repos\tasks.jsonl `
  --repository-root D:\approved-agent-eval-repos `
  --output-dir artifacts\aeitron\agent-scorecard `
  --policy-mode strict --concurrency 4
```

Strict scorecards require 50-100 repository tasks, at least 10 tasks in each of
coding, debugging, security, patch, and long-context categories, at least 10
short prompts, executable verification commands, and file/content oracles.
Reports are evidence-backed JSON/Markdown; missing tasks, scanners, Docker, or a
real scratch model block the run.

Build the governed 50-task historical repository qualification pack:

```powershell
python -m src.aeitron.evaluation.qualification_campaign approval-template `
  --source-root D:\benchmarks\SecRepoBench `
  --output config\local\secrepobench-approval.json

# An authorized reviewer must change decision=pending only after legal/license review.
python -m src.aeitron.evaluation.qualification_campaign build-pack `
  --source-root D:\benchmarks\SecRepoBench `
  --approval config\local\secrepobench-approval.json `
  --output-dir artifacts\aeitron\qualification-pack
```

The pack is exactly 50 pinned historical tasks: 10 coding, 10 debugging, 10
defensive security, 10 patch generation, and 10 long-context tasks. Reference
fixes are sealed from model prompts and the pack is permanently evaluation-only.
Pending approval, changed benchmark files, commit drift, or missing official
results fail closed.

Measure the current scratch checkpoint, then run the gated defensive ladder:

```powershell
python -m src.aeitron.evaluation.qualification_campaign baseline `
  --pack-manifest artifacts\aeitron\qualification-pack\qualification_pack_manifest.json `
  --checkpoint-manifest artifacts\aeitron\train\best_checkpoint_manifest.json `
  --tokenizer artifacts\aeitron\tokenizer\tokenizer.json `
  --output-dir artifacts\aeitron\qualification-baseline --device cuda

python -m src.aeitron.evaluation.qualification_campaign run-stage `
  --target-steps 1000 `
  --campaign-dir artifacts\aeitron\defensive-qualification `
  --pack-manifest artifacts\aeitron\qualification-pack\qualification_pack_manifest.json `
  --dataset-manifest artifacts\aeitron\defensive-data\shards\manifest.json `
  --dataset-version-manifest artifacts\aeitron\defensive-data\versions\<version-id>.json `
  --tokenizer artifacts\aeitron\tokenizer\tokenizer.json `
  --tokenizer-audit-corpus artifacts\aeitron\defensive-data\promoted.jsonl `
  --initial-checkpoint-manifest artifacts\aeitron\train\best_checkpoint_manifest.json `
  --device cuda
```

Repeat `run-stage` with `10000`, `20000`, `50000`, and `100000`. Each stage is
locked behind the previous promotion. From 10k onward, measured checkpoint
improvement is mandatory; tokenizer warning, generation collapse,
hallucination, validation failure, task regression, or incompatible immutable
inputs stops progression.

## Aeitron Scratch Model Serving

Set:

```powershell
$env:AEITRON_MODEL_BACKEND = "aeitron_serving"
$env:AEITRON_MODEL_ENDPOINT = "http://127.0.0.1:8000/v1"
$env:AEITRON_MODEL_NAME = "aeitron-scratch"
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
python -m src.aeitron.learning.mixer --inputs data\training\clean.jsonl --config config\mix_ratios.json --experiment baseline_70_15_15 --output-dir artifacts\\aeitron\mix-baseline

python -m src.aeitron.evaluation.eval_runner --checkpoint-manifest artifacts\\aeitron\train\checkpoint_manifest.json --schedule config\eval_schedule.json --output-dir artifacts\\aeitron\eval --tokenizer-path artifacts\\aeitron\tokenizer\tokenizer.json --device cpu
```

Reports:

- `eval_report.json` and `eval_report.md`
- `mix_manifest.json`
- `ablation_report.json`

## Production Hardening Gates

Local deterministic gates:

```powershell
python -m src.aeitron.db.migration_runner --database-url postgresql://aeitron:pass@localhost:5432/aeitron --dry-run
python -m src.aeitron.deployment.k8s_validate --output-dir artifacts\\aeitron\k8s-validation
python -m src.aeitron.learning.storage --uri local://artifacts/aeitron/object-store --work-dir artifacts\\aeitron\object-store-lifecycle
python -m src.aeitron.learning.dataset_validation --inputs data\training\clean.jsonl --output-dir artifacts\\aeitron\dataset-validation --min-records 100000
python -m src.aeitron.evaluation.benchmark_suites --suite swe swe_bench_style data\eval\swe_style.jsonl --suite cyber cyberseceval_style data\eval\cyber.jsonl --output-dir artifacts\\aeitron\benchmark-suites
python -m src.aeitron.security.audit --no-bandit --output-dir artifacts\\aeitron\security-audit
```

Real production commands:

```powershell
alembic upgrade head
python -m src.aeitron.deployment.k8s_validate --kubectl-dry-run --output-dir artifacts\\aeitron\k8s-validation
python -m src.aeitron.learning.storage --uri s3://aeitron-datasets/pretraining --endpoint-url http://localhost:9000 --work-dir artifacts\\aeitron\s3-lifecycle
python deploy\gpu\run_10k_training_validation.py --manifest artifacts\\aeitron\shards\manifest.json --device cuda --steps 10000
```

Live production proof gate:

```powershell
python -m src.aeitron.deployment.production_proof `
  --strict `
  --postgres-url "$env:AEITRON_DATABASE_URL" `
  --apply-postgres-migrations `
  --redis-url "$env:AEITRON_REDIS_URL" `
  --object-store-uri "$env:AEITRON_OBJECT_STORE_URI" `
  --object-store-endpoint-url "$env:AEITRON_OBJECT_STORE_ENDPOINT_URL" `
  --qdrant-url "$env:AEITRON_QDRANT_URL" `
  --serving-url "$env:AEITRON_SERVING_URL" `
  --serving-api-key "$env:AEITRON_MODEL_API_KEY" `
  --load-test-requests 100 `
  --load-test-streaming-requests 20 `
  --executable-benchmark-report artifacts\aeitron\executable-eval\benchmark_suites_report.json `
  --scorecard-report artifacts\aeitron\agent-scorecard\agent_scorecard.json `
  --active-model-profile C:\AeitronGovernance\active-model-profile.json `
  --run-security-audit `
  --strict-security-tools `
  --output-dir artifacts\\aeitron\production-proof
```

For local or Kaggle validation without live services, omit `--strict`; missing
Postgres/Redis/Qdrant/serving/benchmark inputs are marked as skipped, never as
production-ready.

Strict proof applies real Postgres migrations, performs a temporary Qdrant
create/upsert/query/delete transaction, verifies authenticated scratch-model
identity, exercises normal and SSE responses, and replays the
checkpoint/tokenizer/evaluation/scorecard hash chain. Internal HTTP service
hosts must be individually allowlisted with `--allow-insecure-service-host`;
remote services otherwise require HTTPS.

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
  --checkpoint-compare-prompt-suite artifacts/aeitron/learning-validation-v1/expanded_eval_suite.jsonl \
  --checkpoint-compare-min-score 0.20 \
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
  --checkpoint-compare-prompt-suite artifacts/aeitron/learning-validation-v1/expanded_eval_suite.jsonl \
  --checkpoint-compare-min-score 0.20 \
  --progress-path artifacts/aeitron/real-data-10k-strict-v1/progress.jsonl \
  --progress-to-stdout \
  --progress-every-docs 10 \
  --progress-every-steps 25

python deploy/gpu/run_checkpoint_comparison.py \
  --training-report artifacts/aeitron/real-data-10k-strict-v1/reports/real_data_training_report.json \
  --output-dir artifacts/aeitron/real-data-10k-strict-v1/reports/checkpoint_compare \
  --device cuda
```

The real-data training pipeline now converts promoted rows into scratch
instruction records before tokenizer/sharding. The default token mix target is
40% security/coding instruction examples, 30% verified patch/test examples,
20% high-quality docs/code, and 10% debugging/error logs. Reports are written
to `reports/instruction_mix_report.json` and included in the dataset version
manifest. If `--checkpoint-compare-prompt-suite` is supplied, the best
checkpoint is scored against that suite and the run is blocked when the score is
below `--checkpoint-compare-min-score` or the checkpoint regresses.

Inspect any Kaggle/Colab run and get the next recommended action:

```bash
python deploy/gpu/inspect_real_data_run.py \
  --work-dir artifacts/aeitron/real-data-validation-v1
```

Run the longer scratch pretraining loop:

```bash
python -m src.aeitron.model_ops.tokenizer_pipeline \
  --input data/training/clean.jsonl \
  --tokenizer-out artifacts/aeitron/tokenizer/tokenizer.json \
  --shards-out artifacts/aeitron/shards \
  --vocab-size 64000 \
  --sequence-length 128

python -m src.aeitron.model_ops.pretrain_loop \
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
python -m src.aeitron.learning.web_ingest \
  --sources config/data_sources.ultimate.json \
  --output data/training/raw_web.jsonl \
  --max-docs 1000 \
  --delay-seconds 1.0
```

Persistent million-scale ingestion with resume/retry, URL discovery, provenance,
content deduplication, per-domain throttling, and clean JSONL sharding:

```bash
python -m src.aeitron.learning.data_engine \
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
python -m src.aeitron.learning.data_engine \
  --sources config/data_sources.ultimate.json \
  --frontier-backend postgres \
  --postgres-dsn "$AEITRON_DATABASE_URL" \
  --raw-output-dir artifacts/aeitron/data-engine/raw \
  --clean-output-dir artifacts/aeitron/data-engine/clean \
  --max-docs 1000000 \
  --workers 64
```

One command for `crawl -> clean -> shard -> train`:

```bash
python -m src.aeitron.learning.data_pipeline \
  --sources config/data_sources.ultimate.json \
  --dataset-id aeitron-defensive-coding-corpus \
  --work-dir artifacts/aeitron/data-pipeline \
  --frontier-backend postgres \
  --postgres-dsn "$AEITRON_DATABASE_URL" \
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
python -m src.aeitron.learning.supervisor \
  --sources config/data_sources.ultimate.json \
  --postgres-dsn "$AEITRON_DATABASE_URL" \
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
python -m src.aeitron.learning.production_check \
  --sources config/data_sources.ultimate.json \
  --frontier-backend postgres \
  --postgres-dsn "$AEITRON_DATABASE_URL" \
  --object-store-uri s3://aeitron-datasets/pretraining \
  --production \
  --worker-replicas 8 \
  --async-workers 64
```

Prepare the first serious 100k-1M data run:

```bash
python -m src.aeitron.learning.run_plan \
  --sources config/data_sources.ultimate.json \
  --output-dir artifacts/aeitron/data-runs/first-serious-run \
  --target-documents 1000000 \
  --target-days 7 \
  --postgres-dsn "$AEITRON_DATABASE_URL" \
  --object-store-uri s3://aeitron-datasets/pretraining \
  --worker-replicas 8 \
  --async-workers 64
```

Export the blind-review evidence from the configured Dataset Authority before
building a production dataset:

```bash
python -m src.aeitron.learning.dataset_authority review-report \
  --output artifacts/aeitron/review/review_evidence_report.json
```

Promote a governed 100k-1M production dataset pack into `data/production`:

```bash
python -m src.aeitron.learning.production_dataset \
  --input artifacts/aeitron/data-runs/first-serious-run/clean/*.jsonl \
  --output-dir data/production/aeitron-corpus-v1 \
  --dataset-id aeitron-corpus-v1 \
  --advancement-decision artifacts/aeitron/calibration-5k-v1/calibration_decision.json \
  --source-registry config/data_sources.governed.json \
  --trust-policy config/dataset_trust_policy.json \
  --legal-evidence-dir governance/source-approvals \
  --reviewer-roster config/data_reviewers.json \
  --protected-config config/protected_benchmarks.json \
  --protected-manifest data/eval/protected/protected_benchmark_manifest.json \
  --source-review-report artifacts/aeitron/review/review_evidence_report.json \
  --benchmark-holdout data/eval/humaneval.jsonl \
  --benchmark-holdout data/eval/mbpp.jsonl \
  --verified-patch artifacts/aeitron/verified-patches/verified_patch_tasks.jsonl \
  --human-review-approved artifacts/aeitron/review/approved_high_value.jsonl \
  --min-promoted-records 100000 \
  --min-verified-patch-records 100 \
  --min-human-review-approved-records 100 \
  --min-train-records 90000
```

The checked-in ultimate registry is intentionally quarantine-only. Production
promotion requires a legally reviewed `data_sources.governed.json` with
immutable revisions and real evidence hashes. This command writes
`final/train.jsonl`, `final/val.jsonl`,
`final/test.jsonl`, `final/holdout.jsonl`, `dataset_version_manifest.json`,
license/quality/contamination/dedup/source/gate/split/validation reports, and
`review/human_review_queue.jsonl`. Production mode fails if required row counts,
verified patch evidence, two-reviewer coverage, protected holdouts, source caps,
or quality thresholds are missing. Production success is `promoted`; use
`--dev-smoke` only for local plumbing checks.

One-million-record bounded-memory dedup proof:

```bash
python -m src.aeitron.learning.near_dedup \
  --scale-dry-records 1000000 \
  --scale-output-dir artifacts/aeitron/dedup-scale
```

Training resource priority catalog:

```bash
python -m src.aeitron.learning.resource_catalog \
  --catalog config/data_sources.ultimate.json \
  --output artifacts/aeitron/resource_catalog_report.json
```

The catalog keeps all 45 external cybersecurity/agentic-coding resources in one
place. The top six priority groups are surfaced first, while protected benchmark
resources such as SWE-bench Verified, HumanEval, MBPP, and CTF benchmarks stay
as evaluation/contamination holdouts instead of raw pretraining rows.

Cluster capacity planning:

```bash
python -m src.aeitron.learning.capacity \
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
- automated policy decisions
- automated-pass task candidates (not human-approved training data)
- benchmark/data feedback report
- tokenizer and token-shard manifest
- dataset version manifest
- append-only dataset ledger
- local HTML dashboard at `artifacts/aeitron/data-pipeline/dashboard.html`
- optional S3/MinIO uploads

Manual/automated review and feedback:

```bash
python -m src.aeitron.learning.governance --store artifacts/aeitron/governance report

python -m src.aeitron.learning.governance --store artifacts/aeitron/governance submit-source \
  --source-name portswigger-web-security-academy \
  --category authorized_security_testing_labs \
  --url https://portswigger.net/web-security \
  --license review-required \
  --evidence-url https://portswigger.net/web-security \
  --requested-by security-team \
  --justification "High-value authorized web security education source"

python -m src.aeitron.learning.review \
  --input artifacts/aeitron/data-pipeline/tasks/tasks.jsonl \
  --decisions-out artifacts/aeitron/data-pipeline/reports/task_review_decisions.jsonl \
  --automated-pass-out artifacts/aeitron/data-pipeline/tasks/automated_pass_tasks.jsonl \
  --report-out artifacts/aeitron/data-pipeline/reports/task_review_report.json

python -m src.aeitron.learning.feedback \
  --output artifacts/aeitron/data-pipeline/reports/feedback_report.json \
  --quality-report artifacts/aeitron/data-pipeline/reports/quality_report.json \
  --review-report artifacts/aeitron/data-pipeline/reports/task_review_report.json
```

Automated task screening is not human approval and cannot promote data. A
missing benchmark report now blocks promotion feedback. Source-budget planning
also fails closed: a source without measured reputation evidence receives zero
documents, and the plan reports the unallocated budget explicitly.

The data engine is defensive and allowlist-first. It is for public documentation,
licensed code, security guidance, benchmark corpora, and approved repository
mirrors; it does not perform exploit execution or unauthorized collection.

## Scratch Learning Validation

After a real-data run, Aeitron must prove that the model can learn before larger
GPU time is spent. Run the controlled validation gate first:

```bash
python -m src.aeitron.model_ops.learning_validation \
  --output-dir artifacts/aeitron/learning-validation-v1 \
  --instruction-count 200 \
  --overfit-steps 300 \
  --device cuda \
  --dtype fp16
```

This writes:

- `instruction_corpus.jsonl` with prompt, context, answer, code patch, tests, and verification
- `expanded_eval_suite.jsonl` with 50-200 coding/security/debugging prompts
- `tokenizer_dominance_report.json` and `.md` checking token frequency, dot/quote/space/newline dominance, unknown/single-character token rates, special-token coverage, and code/security pattern efficiency
- `overfit/overfit_sanity_report.json` proving whether the scratch model can memorize a controlled corpus
- a T4 validation command using `--model-profile t4_validation`

Fast local command without expensive training:

```bash
python -m src.aeitron.model_ops.learning_validation \
  --output-dir artifacts/aeitron/learning-validation-smoke \
  --instruction-count 50 \
  --skip-overfit \
  --device cpu
```

Use the expanded suite for checkpoint comparison:

```bash
python deploy/gpu/run_checkpoint_comparison.py \
  --training-report artifacts/aeitron/real-data-validation-v1/reports/real_data_training_report.json \
  --prompt-suite artifacts/aeitron/learning-validation-v1/expanded_eval_suite.jsonl \
  --output-dir artifacts/aeitron/real-data-validation-v1/reports/checkpoint_compare_expanded \
  --device cuda \
  --repetition-penalty 1.18 \
  --no-repeat-ngram-size 4 \
  --max-repetition-ratio 0.72
```

The comparison gate now fails on generation collapse as well as regression.
Collapse is detected from repetitive word/ngram output, and deterministic
evaluation supports stop tokens, repetition penalty, and no-repeat ngram
blocking.

Curriculum-first scratch training:

```bash
python -m src.aeitron.model_ops.learning_validation \
  --output-dir artifacts/aeitron/defensive-learning-validation-v1 \
  --instruction-count 100 \
  --curriculum-mode defensive_security_only \
  --overfit-steps 300 \
  --device cuda \
  --dtype fp16
```

Available curriculum modes:

- `fundamentals_only`
- `defensive_security_only`
- `debug_patch_only`
- `agentic_coding_only`
- `balanced`

Defensive-only mode applies an offensive-misuse row filter and uses a
defensive eval suite with hallucination checks:

- state uncertainty when evidence is missing
- do not invent CVE IDs
- do not claim tests passed without verification evidence
- do not output exploit steps

Kaggle 1k defensive validation:

```bash
PYTHONUNBUFFERED=1 python -u deploy/gpu/run_real_data_training_pipeline.py \
  --sources config/data_sources.ultimate.json \
  --work-dir artifacts/aeitron/defensive-validation-1k-v1 \
  --kaggle-validation \
  --curriculum-mode defensive_security_only \
  --model-profile t4_validation \
  --checkpoint-compare-prompt-suite artifacts/aeitron/defensive-learning-validation-v1/expanded_eval_suite.jsonl \
  --checkpoint-compare-repetition-penalty 1.18 \
  --checkpoint-compare-no-repeat-ngram-size 4 \
  --checkpoint-compare-max-repetition-ratio 0.72 \
  --steps 1000 \
  --sequence-length 128 \
  --batch-size 1 \
  --gradient-accumulation-steps 8 \
  --validation-interval 100 \
  --validation-batches 8 \
  --early-stopping-patience 5 \
  --gradient-checkpointing \
  --progress-to-stdout
```

After the overfit sanity and 1k validation pass, use the same command with
`--work-dir artifacts/aeitron/defensive-validation-10k-v1`, `--steps 10000`,
`--sequence-length 256`, `--validation-interval 250`, and
`--early-stopping-patience 12`.

Quality gate:

```bash
python - <<'PY'
from src.aeitron.learning.quality import DatasetQualityGate
print(DatasetQualityGate().filter_jsonl("data/training/raw_web.jsonl", "data/training/clean.jsonl"))
PY
```

## Production Checks

```powershell
python -m src.aeitron.evaluation.release_gate
python -m src.aeitron.db.migration_runner --database-url $env:AEITRON_DATABASE_URL --dry-run
```

Production API hardening requires:

```powershell
$env:AEITRON_AUTH_ENABLED = "1"
$env:AEITRON_JWT_SECRET = "<long-random-secret>"
$env:AEITRON_ALLOW_TOKEN_ISSUE = "0"
$env:AEITRON_QUOTA_ENABLED = "1"
$env:AEITRON_REDIS_URL = "redis://redis:6379/0"
```

## Training Workspace

The same governed control path now covers direct-kernel notebook validation,
single-node jobs, Kubernetes Jobs, Kubeflow PyTorchJobs, and Slurm. Clients can
select immutable profiles and bounded overrides only; arbitrary shell commands
are never accepted.

```text
SDK / CLI / React UI
  -> FastAPI + short-lived JWT
  -> Postgres job/audit state
  -> Redis Streams live events
  -> S3/MinIO logs, reports, and checkpoints
  -> controller -> notebook | Kubernetes | PyTorchJob | Slurm
```

Notebook secrets:

```text
AEITRON_WORKSPACE_URL=https://workspace.example.com
AEITRON_BOOTSTRAP_TOKEN=<secret-manager-value>
```

Direct-kernel Kaggle/Colab validation with immediate output:

```python
%run -i deploy/gpu/run_workspace_validation.py --profile defensive-1k
```

SDK and CLI:

```python
from aeitron_client import Workspace

workspace = Workspace.from_environment()
run = await workspace.train(profile="defensive-1k", follow=True)
```

```bash
python -m pip install -e . --no-deps
aeitron train --profile defensive-1k --follow
aeitron jobs list
aeitron jobs inspect JOB_ID
aeitron jobs cancel JOB_ID
aeitron jobs resume JOB_ID
```

If a local Python installation does not add its Scripts directory to `PATH`,
`python -m src.aeitron.training_client ...` is the equivalent fallback.

Local production-service proof:

```bash
docker compose -f deploy/prod/docker-compose.yml --profile training up --build
```

Immutable qualification campaign and measured infrastructure proofs:

```powershell
python -m src.aeitron.training_workspace campaigns
docker compose -p aeitron-proof -f deploy\proof\docker-compose.yml up -d
python -m src.aeitron.training_proofs `
  --output-dir artifacts\aeitron\production-proofs\local-docker
```

The `defensive-staircase-v1` campaign contains 37 ordered milestones: 1k
increments through 20k, 10k increments through 100k, then 100k increments
through 1M. A milestone cannot be submitted until the previous job succeeded,
its checkpoint reloaded, its evaluation passed, validation did not regress by
more than 3%, and dataset/tokenizer hashes are unchanged. The campaign is a
qualification ladder, not a substitute for token-budget planning or parameter
scaling.

The full event and soak proofs are explicit long-running operations:

```powershell
python -m src.aeitron.training_proofs --event-count 1000000 `
  --skip-disaster-recovery --output-dir artifacts\aeitron\production-proofs\million-events
python -m src.aeitron.training_proofs --soak-seconds 86400 `
  --output-dir artifacts\aeitron\production-proofs\24-hour-soak
```

The measured local run accepted and sequence-verified 1,000,000 events at
1,285.64 events/second. Cluster schedulers, multi-node GPU recovery, the full
24-hour soak, and 60B checkpoint resume remain blocked until those real targets
are connected; the proof report never substitutes a local mock pass.

Missing Kubernetes, Slurm, CUDA, DeepSpeed, or Megatron dependencies are
recorded as `blocked`; they are never converted to a passing proof.

Real scheduler and recovery proofs use immutable dataset/tokenizer bindings:

```powershell
$common = @(
  "--skip-infrastructure", "--skip-disaster-recovery", "--skip-capability-probes",
  "--dataset-manifest-uri", $env:AEITRON_PROOF_DATASET_URI,
  "--dataset-manifest-sha256", $env:AEITRON_PROOF_DATASET_SHA256,
  "--tokenizer-uri", $env:AEITRON_PROOF_TOKENIZER_URI,
  "--tokenizer-sha256", $env:AEITRON_PROOF_TOKENIZER_SHA256,
  "--git-commit", $env:AEITRON_TRAINING_GIT_COMMIT,
  "--container-digest", $env:AEITRON_TRAINING_IMAGE_DIGEST,
  "--inject-worker-loss"
)
python -m src.aeitron.training_proofs --live-profile aeitron-7b-fsdp @common
python -m src.aeitron.training_proofs --live-profile aeitron-32b-zero3 @common
python -m src.aeitron.training_proofs --live-profile aeitron-60b-hybrid @common
```

The disruptive self-hosted Kubernetes recovery drill is opt-in. It writes
durable Postgres, Redis, and S3 markers, restarts the declared workloads, then
requires all markers to survive:

```powershell
python -m src.aeitron.training_proofs --skip-infrastructure `
  --skip-disaster-recovery --skip-capability-probes `
  --inject-kubernetes-disaster-recovery
```

Redis is deployed as an authenticated persistent StatefulSet. The API accepts
repository paths only beneath `AEITRON_PROJECT_ROOTS`; the production manifest
binds that root to a shared workspace PVC.

The workspace UI is exposed at `http://localhost:8088`. Production ingress must
terminate HTTPS. Profile status remains `built_not_cluster_proven` until its
actual scheduler, topology, checkpoint reload, and scale gate pass.

## Governed Data Calibration

GPU training and 5k/100k crawls are blocked until the governed 200-record gate
passes. First materialize the pinned eval-only holdouts:

```powershell
python -m src.aeitron.evaluation.benchmark_pack --materialize-protected `
  --protected-config config\protected_benchmarks.json `
  --target-dir data\eval\protected
```

Then run the preflight. It writes one legal approval request per source and
reports every missing source/reviewer/holdout dependency without starting a
crawler:

```powershell
python -m src.aeitron.learning.calibration_gate prepare `
  --sources config\data_sources.ultimate.json `
  --output-dir artifacts\aeitron\calibration-preflight
```

For the first balanced foundation calibration, select the exact eight-source
batch before requesting approvals. Selection is deterministic, hash-bound, and
does not modify the ultimate catalog:

```powershell
python -m src.aeitron.learning.source_registry `
  --sources config\data_sources.ultimate.json `
  --select-source owasp-cheat-sheet-series `
  --select-source nist-secure-engineering `
  --select-source python-core-secure-coding `
  --select-source rust-core-secure-systems `
  --select-source go-core-secure-coding `
  --select-source postgresql-secure-data-layer `
  --select-source docker-production-builds `
  --select-source kubernetes-production-security `
  --expect-source-count 8 `
  --selection-manifest artifacts\aeitron\governance\day-1-3-balanced-foundation-8\source_selection_manifest.json `
  --prepare-approval-dir artifacts\aeitron\governance\day-1-3-balanced-foundation-8\approval-requests `
  --output artifacts\aeitron\governance\day-1-3-balanced-foundation-8\sources.pending.json
```

The pending artifact is not a governed registry and cannot start production
collection. The first real approval reads this eight-source file and writes
`config/data_sources.governed.json`; every subsequent approval must read and
rewrite that governed file. Atomic writes reject removal or alteration of an
already approved source.

`config/data_reviewers.json` intentionally contains no invented identities.
Governance operators must configure two independent reviewers and a separate
adjudicator. Legal operators must approve every immutable source contract using
the generated request hashes. Only a `ready` preflight permits:

```powershell
python -m src.aeitron.learning.calibration_gate run `
  --stage calibration_200 `
  --sources config\data_sources.governed.json `
  --reviewer-roster C:\AeitronGovernance\data_reviewers.json `
  --reviewer-qualification-report C:\AeitronGovernance\qualification-result\reviewer_qualification_report.json `
  --legal-evidence-dir C:\AeitronGovernance\source-approvals `
  --work-dir artifacts\aeitron\calibration-200-v1
```

The run binds its deterministic human-review sample to the Dataset Authority.
`finalize` unlocks 5k only when all sampled records have two decisions,
acceptance is at least 95%, Cohen's kappa is at least 0.80, average automated
quality is at least 0.80, no source exceeds 20%, and protected contamination is
zero. Run 5k only with the passed 200 decision:

```powershell
python -m src.aeitron.learning.calibration_gate run `
  --stage calibration_5k `
  --prior-decision artifacts\aeitron\calibration-200-v1\calibration_decision.json `
  --sources config\data_sources.governed.json `
  --reviewer-roster C:\AeitronGovernance\data_reviewers.json `
  --reviewer-qualification-report C:\AeitronGovernance\qualification-result\reviewer_qualification_report.json `
  --legal-evidence-dir C:\AeitronGovernance\source-approvals `
  --work-dir artifacts\aeitron\calibration-5k-v1
```

A passed 5k decision emits `100k_dataset_build_allowed`; production dataset
construction requires that exact hash-bound decision. Custom row counts are
accepted only with `--dev-test --dev-test-target-records N` and can never
authorize a governed next stage. Missing real approvals remain `blocked`; they
are never synthesized.

## Final Rule

All new production code belongs under `src/aeitron`.

## Governed Data-to-Serving Qualification Chain

The production sequence is evidence-gated. It cannot be reordered and no
stage infers success from file presence alone.

1. Evaluate the two independent reviewer submissions:

```powershell
python -m src.aeitron.learning.dataset_authority evaluate-reviewer-qualification `
  --governance-dir C:\AeitronGovernance `
  --response C:\AeitronGovernance\reviewer-1\reviewer-responses.jsonl `
  --response C:\AeitronGovernance\reviewer-2\reviewer-responses.jsonl `
  --output-dir C:\AeitronGovernance\qualification-result
```

The report must pass minimum reviewer accuracy `0.95` and Cohen's kappa `0.80`.
It contains aggregate scores and hashes only; answer-key content, rationales,
OIDC subjects, and per-row correctness are not emitted.

2. The tracked `config/data_sources.governed.staging.json` contains exactly
eight selected sources in quarantine. Approval requests are generated under
the ignored `artifacts/aeitron/source-approval-requests` directory. An
authorized human must provide the official license text, immutable upstream
revision, and signed decision for each source. Sequential approvals produce
`config/data_sources.governed.json`; the CLI refuses missing, stale, rolling,
or hash-mismatched evidence.

3. Run and finalize `calibration_200`, then run and finalize
`calibration_5k`. Only its passed, recursively verified decision can authorize
the exactly 100,000-row production dataset. Custom counts are dev-only and
cannot authorize advancement.

4. Train the production tokenizer only from the promoted dataset:

```powershell
python -m src.aeitron.model_ops.tokenizer_pipeline `
  --input data\production\aeitron-foundation-v1\train.jsonl `
  --output-dir artifacts\aeitron\tokenizer-64k-v1 `
  --dataset-id aeitron-foundation-v1 `
  --vocab-size 64000 `
  --real-corpus-audit
```

The audit now fails unless the actual vocabulary is exactly 64,000 and every
required control token is present. A small corpus that cannot supply enough
valid BPE merges is rejected instead of being called a 64k tokenizer.

5. Run executable HumanEval/MBPP evaluation against a scratch checkpoint:

```powershell
python -m src.aeitron.evaluation.benchmark_suites `
  --mode executable-model `
  --suite humaneval human_eval_style data\eval\protected\humaneval.jsonl `
  --suite mbpp mbpp_style data\eval\protected\mbpp.jsonl `
  --checkpoint-manifest CHECKPOINT_MANIFEST.json `
  --tokenizer-path TOKENIZER.json `
  --candidates-per-task 10 `
  --pass-k 1,5,10 `
  --output-dir artifacts\aeitron\executable-eval
```

Candidates are generated by the selected Aeitron checkpoint and executed in
the hardened Docker sandbox. The older static adapter is explicitly labeled
`dataset_validation`; it is not a model capability score.

6. T4 `1k` and `10k` runs remain external GPU qualifications. After the
governed 50-task repository scorecard passes, create an immutable active
profile:

```powershell
python -m src.aeitron.model_ops.backends promote-checkpoint `
  --checkpoint-manifest CHECKPOINT_MANIFEST.json `
  --tokenizer-path TOKENIZER.json `
  --evaluation-report artifacts\aeitron\executable-eval\benchmark_suites_report.json `
  --scorecard-report artifacts\aeitron\agent-scorecard\agent_scorecard.json `
  --promotion-mode production `
  --endpoint https://serving.internal.example/v1 `
  --output C:\AeitronGovernance\active-model-profile.json
```

Set `AEITRON_ACTIVE_MODEL_PROFILE_PATH` to that external immutable profile.
Remote endpoints must use HTTPS. Checkpoint files, tokenizer, executable eval,
and scorecard are hash-verified before profile creation. The scorecard must
declare the exact checkpoint, tokenizer, and executable-evaluation hashes; a
report from another model is rejected.

The defensive qualification campaign runs the governed executable HumanEval
and MBPP holdouts automatically from `10k` onward. Both artifacts must replay
against `protected_benchmark_manifest.json`, every measured suite must pass,
and the minimum suite pass@1 must meet the configured threshold. The `1k`
stage remains a technical pipeline proof, not a model-quality claim.

7. Qdrant indexing is explicit and idempotent:

```text
POST /v1/context/vector-sync
{"project_id":"PROJECT_ID","backend":"qdrant","batch_size":64}
```

Production Qdrant requires both `AEITRON_QDRANT_URL` and a real
`AEITRON_EMBEDDING_URL`. Local hashing is a development fallback and never
qualifies as production semantic retrieval.



