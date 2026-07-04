# Mythos Complete Architecture Manual

This file is the single source of truth for the current Mythos system. A person
should be able to read this file and understand what exists, why it exists, when
it runs, how it works, and what to inspect after it runs.

Rule for future work: every new production feature must update this file with:

- what the feature does
- why it exists
- when it should run
- how it is triggered
- what files, APIs, tables, or artifacts it uses
- how to verify that it works

The old numbered phase architecture has been removed. Production code belongs
under `src/mythos`.

## Current Status

Mythos is now a consolidated agentic coding and defensive cybersecurity AI
architecture. It has:

- a FastAPI gateway
- JWT auth and quota middleware
- repository indexing and context packing
- durable TaskGraph execution
- tool execution and Docker sandbox contracts
- patch preview/apply/rollback/verify flows
- defensive verification and benchmark harnesses
- scratch-only model foundation contracts
- tokenizer, token-sharding, and scratch pretraining loops
- production data platform for approved cyber/coding data collection
- Postgres/Redis/MinIO/Kubernetes deployment assets
- data quality, contamination, review, and benchmark feedback loops

Mythos is scratch-only. Borrowed-model training and borrowed-model quality
baselines are not part of the architecture. The `mock` backend exists only as a
plumbing test double.

## Architecture Map

```text
User / Client
  |
  v
Gateway Layer
  |
  +--> Identity & Quota
  +--> Project / Session APIs
  +--> Repository Indexing
  +--> Context Builder
  +--> Agent Runtime
  +--> TaskGraph Runtime
  +--> Tool / Sandbox Runtime
  +--> Patch Manager
  +--> Verifier
  +--> Evaluation
  +--> Model Foundation Status
  +--> Data Platform Status

Learning / Data Platform
  |
  +--> Approved Source Registry
  +--> Crawl Frontier: SQLite local, Postgres distributed
  +--> Async Workers
  +--> Quality Gate
  +--> Contamination Gate
  +--> Quality Inspection
  +--> Source Quality Scoring
  +--> Task Extraction
  +--> Automated/Human Review Queue
  +--> Benchmark Feedback
  +--> Dataset Version Manifest
  +--> Object Storage: local or S3/MinIO
  +--> Tokenizer + Token Shards
  +--> Scratch Pretraining Loop
```

## Source Layout

```text
src/mythos/
  agents/        Agent routing helpers.
  context/       Workspace context helpers.
  db/            SQLite local store, Postgres schema, migrations.
  evaluation/    Benchmarks, release gate, scorecard hooks.
  gateway/       FastAPI application and HTTP endpoints.
  guardrails/    Critic/security/verifier policy contracts.
  identity/      JWT auth and quota/rate limiting.
  indexing/      Repository indexer, AST facts, vector search, context builder.
  learning/      Data collection, quality, review, versioning, training data platform.
  memory/        Verified fix memory retrieval/promotion.
  model_ops/     Scratch model specs, tokenizer, shards, pretraining, GPU smoke.
  patches/       Patch preview/apply/rollback/verify.
  planning/      Intent and planning engine.
  runtime/       Agent runtime and durable TaskGraph.
  shared/        Strict schemas and shared contracts.
  tools/         Local command runner and Docker sandbox runner.
  verifier/      Verification runtime and secret scan.
deploy/
  dev/           Local development compose.
  prod/          Production compose with Postgres, Redis, MinIO, crawler workers.
  k8s/           Kubernetes API, Postgres, Redis, MinIO, workers, HPA, policies.
  gpu/           GPU smoke/pretraining profile scripts.
config/
  data_sources.defensive.sample.json
  data_sources.production.sample.json
scripts/
  runtime checks, Docker repair, security tools install, data platform helpers.
tests/
  release-gated unit and smoke tests.
```

## Gateway Layer

Files:

- `src/mythos/gateway/api.py`

Purpose:

The gateway is the single HTTP entrypoint. It exposes health, metrics, auth,
model status, data-platform status, project/session APIs, indexing APIs,
TaskGraph APIs, tool execution, sandbox execution, patch operations, verifier
operations, and agent run operations.

When it runs:

- during local API development
- in Docker Compose production
- in Kubernetes deployment

How to run:

```powershell
python -m uvicorn src.mythos.gateway.api:app --host 127.0.0.1 --port 8090
```

Important endpoints:

- `GET /health/ready`: readiness status
- `GET /metrics`: Prometheus-style metrics
- `GET /v1/auth/status`: auth settings status
- `POST /v1/auth/token`: token issue endpoint, gated in production
- `POST /v1/projects`: register a repository
- `POST /v1/projects/{project_id}/index`: index repository files
- `GET /v1/projects/{project_id}/symbols`: inspect extracted symbols
- `POST /v1/context/build`: build ranked context pack
- `POST /v1/context/vector-search`: local vector search
- `POST /v1/agent/runs`: create a durable agent run
- `GET /v1/taskgraphs/{task_graph_id}`: inspect TaskGraph
- `POST /v1/taskgraphs/{task_graph_id}/advance`: advance next ready task
- `POST /v1/tools/execute`: bounded command execution
- `POST /v1/sandbox/run`: Docker sandbox run
- `POST /v1/patches/preview`: preview file changes
- `POST /v1/patches/verify`: preview/apply/verify/rollback loop
- `POST /v1/verifier/run`: run verifier checks
- `GET /v1/model/foundation/status`: scratch model foundation status
- `GET /v1/data/platform/status`: latest local dataset version/dashboard status

Why it exists:

The rest of the system should not expose random scripts as product APIs. The
gateway gives one stable surface for UI, CLI, tests, and future services.

How to verify:

```powershell
python -m src.mythos.evaluation.release_gate
```

## Identity And Quota

Files:

- `src/mythos/identity/auth.py`
- `src/mythos/identity/quota.py`

Purpose:

Protect API routes with JWT auth and rate-limit users with a regenerative token
bucket. Redis is used when `MYTHOS_REDIS_URL` is set. Local in-memory quota is
development-only fallback.

When it runs:

- every protected API request
- before gateway handlers execute

Important environment:

```bash
MYTHOS_AUTH_ENABLED=1
MYTHOS_JWT_SECRET=<long-random-secret>
MYTHOS_ALLOW_TOKEN_ISSUE=0
MYTHOS_TOKEN_ISSUE_KEY=<only-if-token-issue-enabled>
MYTHOS_QUOTA_ENABLED=1
MYTHOS_REDIS_URL=redis://redis:6379/0
```

Why it exists:

Without auth, anyone can call the AI backend. Without quota, one user can
exhaust the system. Token issuance is deliberately blocked in production unless
explicitly enabled.

How to verify:

- `tests/test_mythos_production_hardening.py`
- `GET /v1/auth/status`

## Observability

Files:

- `src/mythos/observability.py`

Purpose:

Provide structured JSON request logs and Prometheus-style metrics. Every request
records method, path, status, duration, and user id when available.

When it runs:

- middleware around every gateway request

How to inspect:

```powershell
Invoke-RestMethod http://127.0.0.1:8090/metrics
```

Why it exists:

For production, the system must show live health and request behavior. Debugging
large agent/data jobs without metrics is too slow.

## Repository Intelligence

Files:

- `src/mythos/indexing/repository_indexer.py`
- `src/mythos/indexing/context_builder.py`
- `src/mythos/indexing/vector_index.py`

Purpose:

Turn a repository into searchable, structured context. The indexer walks files,
chunks code, extracts Python AST facts, extracts import/dependency hints for
multiple languages, and stores chunks in the local store.

What it extracts:

- file path
- language
- content hash
- chunk boundaries
- token estimate
- Python functions/classes
- signatures
- imports
- calls
- decorators
- docstrings
- state mutations
- generic dependency hints for JS/TS/Go/Rust/Java/C/C++/Bash

When it runs:

- after a project is created
- before context building or agent work

Command path:

```powershell
python -m uvicorn src.mythos.gateway.api:app --host 127.0.0.1 --port 8090
```

Then call:

```powershell
Invoke-RestMethod -Method Post http://127.0.0.1:8090/v1/projects/<project_id>/index
Invoke-RestMethod http://127.0.0.1:8090/v1/projects/<project_id>/symbols
```

Why it exists:

Agentic coding quality depends heavily on repo understanding. The model should
not rely only on raw file text; it needs ranked, structured code context.

## Context Builder

Files:

- `src/mythos/indexing/context_builder.py`
- `src/mythos/context/builder.py`

Purpose:

Build compact ranked context packs for a user query. It scores chunks by query
relevance, file pins, symbol metadata, and rough token budget.

When it runs:

- before agent planning
- when the user asks a repo-specific coding/debugging/security question

API:

- `POST /v1/context/build`
- `POST /v1/context/vector-search`

Why it exists:

Long context is expensive. The system must choose the most relevant repo
context instead of dumping all files.

## Planning And TaskGraph Runtime

Files:

- `src/mythos/planning/engine.py`
- `src/mythos/runtime/taskgraph.py`
- `src/mythos/runtime/engine.py`

Purpose:

Create and execute durable task graphs for agentic work. Current default graph:

```text
understand -> retrieve_context -> edit -> test -> verify -> summarize
```

When it runs:

- when an agent run is created
- when the client advances tasks
- when a task is completed or failed

APIs:

- `POST /v1/agent/runs`
- `GET /v1/taskgraphs/{task_graph_id}`
- `POST /v1/taskgraphs/{task_graph_id}/advance`
- `POST /v1/tasks/{task_id}/complete`
- `POST /v1/tasks/{task_id}/fail`

Why it exists:

Agent work must be durable and inspectable. A hidden single prompt loop is hard
to debug. TaskGraph state shows what failed: planning, context, editing,
testing, verification, or summary.

## Tool Runtime And Sandbox

Files:

- `src/mythos/tools/`

Purpose:

Run bounded commands and sandboxed code execution. The Docker sandbox contract
uses hardened defaults such as no network, memory cap, read-only root, tmpfs,
and dropped capabilities when Docker is available.

When it runs:

- for tests
- for compile commands
- for patch verification
- for defensive sandbox checks

APIs:

- `POST /v1/tools/execute`
- `POST /v1/sandbox/run`

Why it exists:

Coding agents need tools. They should not execute arbitrary commands without
limits.

## Patch Manager

Files:

- `src/mythos/patches/service.py`

Purpose:

Preview, apply, verify, and rollback file edits. It protects project root
boundaries and rejects writes into `.git`.

Patch acceptance standard:

- preview before apply
- path must stay inside project root
- `.git` writes are blocked
- patch applies cleanly
- verification commands pass
- secret scan is clean
- rollback remains possible

APIs:

- `POST /v1/patches/preview`
- `POST /v1/patches/verify`
- `POST /v1/patches/{patch_id}/apply`
- `POST /v1/patches/{patch_id}/rollback`

Why it exists:

Coding AI should not blindly overwrite a repository. Every edit needs a
reversible lifecycle.

## Verifier And Guardrails

Files:

- `src/mythos/verifier/runtime.py`
- `src/mythos/guardrails/service.py`

Purpose:

Verifier runs configured commands and secret scans. Guardrails provide simple
critic/security policy contracts for defensive review.

When it runs:

- after patch preview/apply
- before patch acceptance
- during release tests

Why it exists:

The model can be wrong. Verification is the system’s practical truth source.

## Evaluation Service

Files:

- `src/mythos/evaluation/benchmarks.py`
- `src/mythos/evaluation/release_gate.py`
- `src/mythos/evaluation/service.py`

Purpose:

Run local benchmark and smoke tests. The release gate is the native "can we
ship this code?" check.

Command:

```powershell
python -m src.mythos.evaluation.release_gate
```

Current release gate covers:

- gateway flows
- project/session/index/context APIs
- TaskGraph lifecycle
- patch verify/rollback
- security benchmark harness
- production hardening checks
- data platform pipeline
- scratch decoder smoke paths

Why it exists:

Without a release gate, architecture quality is only a claim. The gate makes it
measurable.

## Database Layer

Files:

- `src/mythos/db/local_store.py`
- `src/mythos/db/schema.sql`
- `src/mythos/db/migrations/`
- `src/mythos/db/migration_runner.py`

Purpose:

Local SQLite is used for development. Postgres schema and migrations define the
production contract.

Main application tables:

- projects
- sessions
- runs
- task_graphs
- tasks
- workspace_files
- code_chunks
- patches
- evaluations
- memory_entries
- learning_candidates

Data platform tables:

- data_sources
- dataset_versions
- data_quality_events

When it runs:

- local store runs during local API/tests
- migrations run during production bootstrap

Command:

```powershell
python -m src.mythos.db.migration_runner --database-url $env:MYTHOS_DATABASE_URL
```

## Unified Memory

Files:

- `src/mythos/memory/system.py`

Purpose:

Store and retrieve verified fixes so the agent can avoid repeating solved
mistakes. Memory is intentionally conservative: only verified fixes should be
promoted.

When it runs:

- after a bug is fixed and verified
- before similar future tasks

Why it exists:

Repeated failures are expensive. Verified memory creates compounding
engineering improvement.

## Model Foundation And Scratch Training

Files:

- `src/mythos/model_ops/foundation.py`
- `src/mythos/model_ops/torch_decoder.py`
- `src/mythos/model_ops/tokenizer_pipeline.py`
- `src/mythos/model_ops/data_loader.py`
- `src/mythos/model_ops/pretrain_loop.py`
- `deploy/gpu/`

Purpose:

Define Mythos-owned scratch model contracts and executable training primitives.
The system supports architecture planning for 7B, 32B, 70B, and 100B-class
decoder models, but local tests use tiny smoke configs.

What exists:

- scratch decoder architecture specs
- parameter estimates
- pretraining readiness contract
- tokenizer contract
- BPE tokenizer training
- token shard creation
- streaming dataloader
- checkpoint-resumable pretraining loop
- gradient accumulation
- mixed precision on CUDA
- validation loss support
- checkpoint manifests with hashes
- GPU smoke scripts

Commands:

```bash
pip install -r requirements-linux-gpu.txt
python deploy/gpu/run_scratch_gpu_smoke.py --device cuda --steps 2 --sequence-length 64
python -m src.mythos.model_ops.tokenizer_pipeline --input data/training/clean.jsonl --tokenizer-out artifacts/mythos/tokenizer/tokenizer.json --shards-out artifacts/mythos/shards --vocab-size 64000 --sequence-length 128
python -m src.mythos.model_ops.pretrain_loop --device cuda --manifest artifacts/mythos/shards/manifest.json --steps 100 --batch-size 2 --sequence-length 128 --gradient-accumulation-steps 4 --dtype bf16
python deploy/gpu/run_pretraining_pipeline.py --input data/training/clean.jsonl --device cuda --steps 100 --sequence-length 128
```

Why it exists:

The user wants scratch training, not borrowed external checkpoints. These
modules prepare that path without pretending a laptop can train a large model.

## Data Platform Overview

The data platform is the most important current subsystem for future model
quality. It exists to collect, filter, review, version, and prepare approved
coding/security data.

Full flow:

```text
Approved source registry
  -> production readiness check
  -> capacity planning
  -> crawl frontier
  -> async workers
  -> raw JSONL shards
  -> quality gate
  -> clean JSONL shards
  -> contamination gate
  -> quality inspection
  -> source quality scoring
  -> task extraction
  -> automated/human review queue
  -> approved task JSONL
  -> benchmark/data feedback
  -> tokenizer training
  -> token shard creation
  -> dataset version manifest
  -> object storage upload
  -> dashboard
```

Safety boundary:

The data platform is for approved public sources, licensed repositories,
defensive security references, documentation, benchmark corpora, and approved
mirrors. It does not run exploits or collect unauthorized targets.

## Approved Source Registry

Files:

- `src/mythos/learning/source_registry.py`
- `config/data_sources.defensive.sample.json`
- `config/data_sources.production.sample.json`

Purpose:

Define which sources may be crawled. Every source declares:

- name
- seed URLs
- allowed domains
- license
- category

When it runs:

- before any crawl
- before run-plan generation
- before production readiness checks

What it blocks:

- URL outside allowed domain
- unsupported URL scheme
- empty domain allowlist
- unapproved license warnings
- duplicate seed warnings

Commands:

```bash
python -m src.mythos.learning.source_registry --sources config/data_sources.production.sample.json
python -m src.mythos.learning.source_registry --sources registry1.json registry2.json --output artifacts/mythos/sources.merged.json
```

Why it exists:

Best data starts with best sources. Crawling random internet pages creates
noise, legal risk, and model contamination.

## Crawl Frontier And Data Engine

Files:

- `src/mythos/learning/data_engine.py`

Purpose:

Perform large allowlisted crawls with persistent state.

Local mode:

- SQLite frontier
- good for laptop/dev/small runs

Production mode:

- Postgres frontier
- row locks with `FOR UPDATE SKIP LOCKED`
- many workers can claim URLs without duplicate work

What it does:

- seeds URLs from registry
- claims queued URLs
- respects robots rules
- enforces per-domain throttling
- fetches pages
- extracts text from HTML
- writes raw JSONL shards
- evaluates quality
- writes clean JSONL shards
- records provenance
- deduplicates by content hash
- discovers new links inside allowed domains
- retries failed URLs
- tracks done/failed/queued states

Commands:

```bash
python -m src.mythos.learning.data_engine --sources config/data_sources.defensive.sample.json --frontier artifacts/mythos/data-engine/frontier.sqlite3 --raw-output-dir artifacts/mythos/data-engine/raw --clean-output-dir artifacts/mythos/data-engine/clean --max-docs 1000 --workers 8 --max-depth 1
python -m src.mythos.learning.data_engine --sources config/data_sources.production.sample.json --frontier-backend postgres --postgres-dsn "$MYTHOS_DATABASE_URL" --raw-output-dir artifacts/mythos/data-engine/raw --clean-output-dir artifacts/mythos/data-engine/clean --max-docs 1000000 --workers 64
```

Why it exists:

Million-scale data requires resume, retry, dedup, and distributed URL claiming.
A simple script cannot safely collect large corpora.

## Quality Gate

Files:

- `src/mythos/learning/quality.py`

Purpose:

Reject bad rows and add metadata to accepted rows.

Checks:

- minimum text length
- maximum text length
- allowed license
- secret-like content
- email-like PII
- duplicate content

Adds:

- labels such as `defensive_security`, `code`, `tests`
- quality score
- language hint
- data type
- content hash

When it runs:

- inside the data engine
- during standalone JSONL filtering

Why it exists:

Low-quality data trains low-quality behavior. The quality gate is the first
hard filter.

## Contamination Gate

Files:

- `src/mythos/learning/contamination.py`

Purpose:

Block benchmark and holdout leakage before tokenizer/shard creation.

Default patterns include:

- HumanEval
- MBPP
- SWE-bench
- CyberSecEval
- common benchmark marker strings

When it runs:

- after clean shards are written
- before tokenizer training
- before dataset version promotion

Why it exists:

If benchmark prompts leak into training, evaluation becomes fake.

## Quality Inspection

Files:

- `src/mythos/learning/quality_inspector.py`

Purpose:

Summarize clean data quality after filtering.

Reports:

- row count
- average/min/max quality score
- distribution by label
- distribution by language
- distribution by data type
- distribution by license
- distribution by source

When it runs:

- inside `data_pipeline`
- manually after a dataset run

Command:

```bash
python -m src.mythos.learning.quality_inspector --input artifacts/mythos/data-pipeline/clean/clean-000000.jsonl --output artifacts/mythos/data-pipeline/reports/quality_report.json
```

Why it exists:

After crawling, you need to know what you actually collected. Counts alone are
not enough.

## Source Quality Scoring

Files:

- `src/mythos/learning/source_quality.py`

Purpose:

Score each source based on accepted rows, quality score, code coverage, and
defensive security coverage.

Actions:

- `promote`: source is strong
- `watch`: source is usable but needs monitoring
- `demote`: source is noisy or low value

When it runs:

- inside `data_pipeline`
- after quality inspection

Why it exists:

Large crawls should improve over time. Good sources should get more crawl
budget; noisy sources should lose budget.

## Task Extraction

Files:

- `src/mythos/learning/task_extraction.py`

Purpose:

Convert clean corpus rows into task candidates for agentic coding and defensive
security training/evaluation.

Task types:

- `agentic_coding`
- `security_finding`
- `security_patch_generation`
- `technical_reasoning`

How it works:

- extracts fenced code blocks when present
- infers task type from security/code terms
- builds prompts that preserve source URL/provenance
- deduplicates task prompts
- writes JSONL task candidates

When it runs:

- after contamination and quality inspection

Output:

- `tasks/tasks.jsonl`

Why it exists:

Raw documents are not enough for agentic learning. The model needs tasks,
prompts, context, and verifiable work patterns.

## Automated And Human Review Queue

Files:

- `src/mythos/learning/review.py`

Purpose:

Review extracted tasks before promotion.

Outputs:

- review decisions JSONL
- approved task JSONL

Decision statuses:

- `approved`
- `needs_human_review`
- `rejected`

Automated policy checks:

- prompt length
- defensive/safe wording
- source URL present
- language present
- useful task type
- high-risk action terms

Command:

```bash
python -m src.mythos.learning.review --input artifacts/mythos/data-pipeline/tasks/tasks.jsonl --decisions-out artifacts/mythos/data-pipeline/reports/task_review_decisions.jsonl --approved-out artifacts/mythos/data-pipeline/tasks/approved_tasks.jsonl
```

Why it exists:

Task extraction can create noisy prompts. Training should use approved tasks,
not every generated candidate.

## Benchmark And Data Feedback

Files:

- `src/mythos/learning/feedback.py`

Purpose:

Combine benchmark results, quality reports, and review reports into
recommendations.

Recommendations can say:

- quality is too low
- task extraction is too noisy
- benchmark score is below promotion threshold
- dataset can be promoted

Command:

```bash
python -m src.mythos.learning.feedback --output artifacts/mythos/data-pipeline/reports/feedback_report.json --quality-report artifacts/mythos/data-pipeline/reports/quality_report.json --review-report artifacts/mythos/data-pipeline/reports/task_review_report.json
```

Why it exists:

Data quality must be tied to model/evaluation outcomes. If benchmark score drops
or task review approval is weak, the dataset should not be promoted blindly.

## Dataset Versioning And Ledger

Files:

- `src/mythos/learning/versioning.py`

Purpose:

Create immutable dataset version manifests and append them to a ledger.

Manifest includes:

- dataset id
- version id
- source registry report
- crawl report
- contamination report
- quality report
- source quality report
- task extraction report
- review report
- feedback report
- tokenizer path
- token shard manifest
- artifact hashes
- uploaded object URIs

Outputs:

- `versions/<version_id>.json`
- `versions/ledger.jsonl`

Why it exists:

You need to know exactly which data created which training run. Without
versioning, model regressions cannot be traced.

## Object Storage

Files:

- `src/mythos/learning/storage.py`

Purpose:

Upload dataset artifacts to local storage or S3/MinIO.

Supported:

- `local://...`
- `file://...`
- `s3://bucket/prefix`

Production:

- use S3/MinIO
- upload clean shards, task files, reports, tokenizer, shard manifest, token
  shards, and version manifest
- S3 uploads retry with backoff

Why it exists:

Large datasets cannot live only on a laptop filesystem. Object storage is the
durable artifact layer.

## Data Dashboard

Files:

- `src/mythos/learning/dashboard.py`

Purpose:

Render a simple HTML dashboard for a dataset run.

Shows:

- dataset id
- version id
- sources
- seed URLs
- fetched/accepted/rejected rows
- contamination hits
- average quality score
- source score count
- extracted tasks
- approved tasks
- feedback item count
- uploaded object count

Output:

- `dashboard.html`

Why it exists:

A dataset run should be inspectable without reading raw JSON files.

## Production Readiness Gate

Files:

- `src/mythos/learning/production_check.py`

Purpose:

Fail fast before a serious production data run if unsafe local-only settings are
used.

Checks:

- source registry is valid
- production run uses Postgres frontier
- production run uses S3/MinIO object storage
- contamination gate is enabled
- data-platform migration exists
- worker scale is high enough

Command:

```bash
python -m src.mythos.learning.production_check --sources config/data_sources.production.sample.json --frontier-backend postgres --postgres-dsn "$MYTHOS_DATABASE_URL" --object-store-uri s3://mythos-datasets/pretraining --production --worker-replicas 8 --async-workers 64
```

Why it exists:

Production data jobs are expensive. The system should block obviously unsafe
configuration before crawling begins.

## Capacity Planner

Files:

- `src/mythos/learning/capacity.py`

Purpose:

Estimate storage, bandwidth, days to completion, and recommended worker replica
count.

Command:

```bash
python -m src.mythos.learning.capacity --target-documents 1000000000 --target-days 30 --worker-replicas 32 --async-workers-per-replica 32
```

Example meaning:

If 1B documents at 64KB average are targeted, raw storage is about 64TB. If
workers are too few, the planner recommends how many replicas are needed for
the target schedule.

Why it exists:

"Billion-scale" is not just code. It is storage, bandwidth, workers, and time.

## First Serious Run Planner

Files:

- `src/mythos/learning/run_plan.py`

Purpose:

Prepare a serious 100k-1M run before executing it.

It does:

- merge one or more source registries
- validate the merged registry
- run production readiness checks
- calculate capacity plan
- write `run_plan.json`
- write `commands.ps1`

Command:

```bash
python -m src.mythos.learning.run_plan --sources config/data_sources.production.sample.json --output-dir artifacts/mythos/data-runs/first-serious-run --target-documents 1000000 --target-days 7 --postgres-dsn "$MYTHOS_DATABASE_URL" --object-store-uri s3://mythos-datasets/pretraining --worker-replicas 8 --async-workers 64
```

Why it exists:

Before collecting a real dataset, you need a reproducible plan and exact
commands. This prevents ad hoc data runs.

## End-To-End Data Pipeline

Files:

- `src/mythos/learning/data_pipeline.py`

Purpose:

Run the full data pipeline in one command.

Order:

1. Load and validate source registry.
2. Build SQLite or Postgres frontier.
3. Crawl approved URLs.
4. Write raw and clean JSONL shards.
5. Run contamination gate.
6. Write quality report.
7. Write source quality report.
8. Extract tasks.
9. Review tasks.
10. Write approved tasks.
11. Build benchmark/data feedback report.
12. Train tokenizer.
13. Build token shards.
14. Optionally run scratch pretraining loop.
15. Write dataset version manifest.
16. Append dataset ledger.
17. Upload artifacts to object storage.
18. Write dashboard.

Command:

```bash
python -m src.mythos.learning.data_pipeline --sources config/data_sources.production.sample.json --dataset-id mythos-defensive-coding-corpus --work-dir artifacts/mythos/data-pipeline --frontier-backend postgres --postgres-dsn "$MYTHOS_DATABASE_URL" --object-store-uri s3://mythos-datasets/pretraining --object-store-endpoint-url "$S3_ENDPOINT_URL" --max-docs 1000000 --workers 64 --max-depth 2 --vocab-size 64000 --sequence-length 2048 --shard-token-count 1000000 --skip-train
```

When to use `--skip-train`:

- during collection
- during quality inspection
- before GPU is available
- before final data approval

When to remove `--skip-train`:

- only after dataset quality, contamination, review, and feedback reports are
  acceptable
- only when GPU training hardware is ready

## Distributed Data Workers

Files:

- `src/mythos/learning/worker.py`
- `deploy/k8s/data-worker.yaml`
- `deploy/k8s/data-worker-hpa.yaml`

Purpose:

Run long-lived distributed crawlers against the same Postgres frontier.

Why Postgres matters:

Multiple workers can claim URLs safely using row locks. This prevents many
machines from crawling the same URL.

Local start:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\start_data_platform_local.ps1 -WorkerScale 2
```

Kubernetes:

```bash
kubectl apply -f deploy/k8s/data-worker.yaml
kubectl apply -f deploy/k8s/data-worker-hpa.yaml
```

Why it exists:

Million/billion-scale collection needs multiple machines and long-running
workers.

## Production Deployment

Files:

- `deploy/prod/docker-compose.yml`
- `deploy/k8s/api.yaml`
- `deploy/k8s/postgres-redis.yaml`
- `deploy/k8s/minio.yaml`
- `deploy/k8s/data-worker.yaml`
- `deploy/k8s/data-worker-hpa.yaml`
- `deploy/k8s/data-network-policy.yaml`
- `deploy/k8s/data-pipeline-job.yaml`
- `deploy/k8s/secrets.example.yaml`

Local production-like data platform:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\start_data_platform_local.ps1 -WorkerScale 4
```

Kubernetes deployment order:

```bash
kubectl apply -f deploy/k8s/secrets.example.yaml
kubectl apply -f deploy/k8s/postgres-redis.yaml
kubectl apply -f deploy/k8s/minio.yaml
kubectl apply -f deploy/k8s/api.yaml
kubectl apply -f deploy/k8s/data-worker.yaml
kubectl apply -f deploy/k8s/data-worker-hpa.yaml
kubectl apply -f deploy/k8s/data-network-policy.yaml
kubectl apply -f deploy/k8s/data-pipeline-job.yaml
```

What each piece does:

- Postgres: distributed frontier and production database.
- Redis: quota backend.
- MinIO: S3-compatible dataset artifact storage.
- Data workers: long-running crawler workers.
- HPA: scales workers from 4 to 64 replicas.
- NetworkPolicy: restricts data platform network flows.
- Data pipeline job: batch job for full pipeline processing.

## Recommended Next Operational Step

Do not start with 1M documents immediately. Run a 100-500 document real smoke
first:

1. Start local data platform.
2. Run DB migrations.
3. Run production readiness check.
4. Generate run plan.
5. Run `data_pipeline` with `--max-docs 200 --skip-train`.
6. Inspect:
   - `dashboard.html`
   - `reports/quality_report.json`
   - `reports/source_quality_report.json`
   - `reports/contamination_report.json`
   - `reports/task_review_report.json`
   - `reports/feedback_report.json`
   - `tasks/approved_tasks.jsonl`
7. If clean, scale to 5k, then 100k, then 1M.

## Verification Commands

Use these after major changes:

```powershell
python -m compileall -q src\mythos tests deploy\gpu
python -m unittest tests.test_mythos_data_engine tests.test_mythos_production_hardening
python -m src.mythos.evaluation.release_gate
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\run_mythos_consolidated_smoke.ps1
```

## Final Rule

Do not reintroduce numbered legacy folders. If a feature is needed, add it to
the correct final module under `src/mythos` and update this manual with enough
detail that the system can be understood without reading all source code.
