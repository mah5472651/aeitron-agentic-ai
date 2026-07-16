# Aeitron Complete Architecture Manual

This file is the single source of truth for the current Aeitron system. A person
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
under `src/aeitron`.

## Operating Roadmap

This roadmap is the default rule for future Aeitron work:

- Scratch-only model development. Aeitron must not add external foundation-model
  fine-tuning, SFT, DPO, GRPO, LoRA, QLoRA, or RLHF paths.
- Production-grade implementation only. New code must use explicit validation,
  fail-fast dependency checks, secure defaults, durable artifacts, and real
  tests. It must not claim readiness without evidence.
- Coding-agent performance comes first. Repository indexing, context packing,
  TaskGraph execution, patch generation, hardened tool execution, verifier
  loops, and benchmark feedback are higher priority than decorative
  architecture.
- Cybersecurity data and tooling stay governed. Allowed work includes approved
  sources, defensive analysis, authorized labs/CTFs/evaluation material,
  vulnerability detection, patch generation, and verification. Aeitron must not
  add autonomous live-target attack workflows.
- Data quality comes before data scale. Source reputation, license/provenance,
  contamination filtering, deduplication, task extraction, human-review queues,
  and eval holdouts must happen before tokenizer/sharding/training.
- Production status must be evidence-based. Local smoke, Kaggle/Colab
  validation, and real cluster production are different statuses. Dependencies
  such as Redis, Postgres, S3/MinIO, Qdrant, Docker, CUDA, benchmark files, and
  scanner CLIs must be checked explicitly.
- Keep the architecture consolidated. Do not reintroduce phase explosion or many
  tiny wrapper files unless separation is required for security, testing,
  deployment, or clear ownership.

## Current Status

Aeitron is now a consolidated agentic coding and defensive cybersecurity AI
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

Aeitron is scratch-only. Borrowed-model training and borrowed-model quality
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
src/aeitron/
  agents/        Agent routing helpers.
  context/       Workspace context helpers.
  db/            SQLite local store, Postgres schema, migrations.
  evaluation/    Benchmarks, release gate, scorecard hooks.
  gateway/       FastAPI application and HTTP endpoints.
  guardrails/    Critic/security/verifier policy contracts.
  identity/      JWT auth and quota/rate limiting.
  indexing/      Repository indexer, AST facts, vector search, context builder.
  learning/      Data collection, quality, review, versioning, training data platform.
  memory/        Unified working/project/episodic/semantic/user/fix memory.
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
  data_sources.ultimate.json
scripts/
  runtime checks, Docker repair, security tools install, data platform helpers.
tests/
  release-gated unit and smoke tests.
```

## Gateway Layer

Files:

- `src/aeitron/gateway/api.py`

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
python -m uvicorn src.aeitron.gateway.api:app --host 127.0.0.1 --port 8090
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
- `POST /v1/context/vector-search`: vector search with backend selector
- `GET /v1/context/vector-capabilities`: vector backend readiness
- `POST /v1/memory/ingest`: ingest verified memory
- `POST /v1/memory/retrieve`: retrieve ranked memory
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
python -m src.aeitron.evaluation.release_gate
```

## Identity And Quota

Files:

- `src/aeitron/identity/auth.py`
- `src/aeitron/identity/quota.py`

Purpose:

Protect API routes with JWT auth and rate-limit users with a regenerative token
bucket. Redis is used when `AEITRON_REDIS_URL` is set. Local in-memory quota is
development-only fallback.

When it runs:

- every protected API request
- before gateway handlers execute

Important environment:

```bash
AEITRON_AUTH_ENABLED=1
AEITRON_JWT_SECRET=<long-random-secret>
AEITRON_ALLOW_TOKEN_ISSUE=0
AEITRON_TOKEN_ISSUE_KEY=<only-if-token-issue-enabled>
AEITRON_QUOTA_ENABLED=1
AEITRON_REDIS_URL=redis://redis:6379/0
```

Why it exists:

Without auth, anyone can call the AI backend. Without quota, one user can
exhaust the system. Token issuance is deliberately blocked in production unless
explicitly enabled.

How to verify:

- `tests/test_aeitron_production_hardening.py`
- `GET /v1/auth/status`

## Observability

Files:

- `src/aeitron/observability.py`

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

- `src/aeitron/indexing/repository_indexer.py`
- `src/aeitron/indexing/context_builder.py`
- `src/aeitron/indexing/vector_index.py`

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
python -m uvicorn src.aeitron.gateway.api:app --host 127.0.0.1 --port 8090
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

- `src/aeitron/indexing/context_builder.py`
- `src/aeitron/context/builder.py`

Purpose:

Build compact ranked context packs for a user query. It scores chunks by query
relevance, file pins, symbol metadata, and rough token budget.

When it runs:

- before agent planning
- when the user asks a repo-specific coding/debugging/security question

API:

- `POST /v1/context/build`
- `POST /v1/context/vector-search`
- `GET /v1/context/vector-capabilities`

Vector backend details:

- `local_hashing`: built-in deterministic hashed embedding backend. It scans
  indexed chunks exactly and is the default for local development, tests, and
  small/medium repositories.
- `faiss`: explicit FAISS adapter contract. Use when `faiss` is installed and a
  large local ANN sidecar index is desired.
- `hnsw`: explicit HNSW adapter contract. Use when `hnswlib` is installed and a
  fast local approximate index is desired.
- `qdrant`: production distributed vector database contract. Requires
  `AEITRON_QDRANT_URL` or `qdrant_url`.
- `pgvector`: Postgres-native vector search contract. Requires
  `AEITRON_DATABASE_URL` or `postgres_dsn`.

Current production path:

- local/dev/smoke: `local_hashing`
- many projects or large memory: `qdrant`
- relational + vector in one database: `pgvector`
- single-node large repo: `faiss` or `hnsw`

The API rejects unavailable production backends with explicit configuration or
dependency errors instead of silently pretending they are active.

Why it exists:

Long context is expensive. The system must choose the most relevant repo
context instead of dumping all files.

## Planning And TaskGraph Runtime

Files:

- `src/aeitron/planning/engine.py`
- `src/aeitron/runtime/taskgraph.py`
- `src/aeitron/runtime/engine.py`

Purpose:

Create and execute durable task graphs for agentic work. Current default graph:

```text
understand
  -> planner
  -> retrieve_context
  -> edit
  -> test
  -> critic_review
  -> security_review
  -> performance_review
  -> verify
  -> summarize
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
to debug. TaskGraph state shows what failed: intent understanding, planning,
context retrieval, editing, testing, critic review, security review,
performance review, verification, or summary.

## Tool Runtime And Sandbox

Files:

- `src/aeitron/tools/`

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

- `src/aeitron/patches/service.py`

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

- `src/aeitron/verifier/runtime.py`
- `src/aeitron/guardrails/service.py`

Purpose:

Verifier runs configured commands and secret scans. Guardrails provide simple
critic/security policy contracts for defensive review.

When it runs:

- after patch preview/apply
- before patch acceptance
- during release tests

Why it exists:

The model can be wrong. Verification is the systemâ€™s practical truth source.

## Evaluation Service

Files:

- `src/aeitron/evaluation/benchmarks.py`
- `src/aeitron/evaluation/release_gate.py`
- `src/aeitron/evaluation/service.py`

Purpose:

Run local benchmark and smoke tests. The release gate is the native "can we
ship this code?" check.

Command:

```powershell
python -m src.aeitron.evaluation.release_gate
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

- `src/aeitron/db/local_store.py`
- `src/aeitron/db/schema.sql`
- `src/aeitron/db/migrations/`
- `src/aeitron/db/migration_runner.py`

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
python -m src.aeitron.db.migration_runner --database-url $env:AEITRON_DATABASE_URL
```

## Unified Memory

Files:

- `src/aeitron/memory/system.py`

Purpose:

Store and retrieve typed, ranked memory without polluting future context. The
memory manager supports six layers:

- `working`: current session/task context only; in-process TTL.
- `project`: repository facts, architecture decisions, module paths, stack.
- `episodic`: solved workflow traces and successful run outcomes.
- `semantic`: durable technical concepts and reusable knowledge.
- `user`: durable user preferences and operating constraints.
- `verified_fix`: failure -> fix -> verified outcome records.

Anti-pollution policy:

- allowed: verified fixes, passed benchmarks, security findings, successful
  plans, project facts, user preferences
- rejected: raw thoughts, failed guesses, transient outputs

Retrieval ranking:

```text
Final Score =
  0.4 * vector_similarity
  + 0.3 * success_rate
  + 0.2 * recency_weight
  + 0.1 * usage_count_weight
```

This formula is implemented in `memory_rank_score`.

When it runs:

- after a bug is fixed and verified
- after a benchmark or security finding is confirmed
- when project facts or user preferences should persist
- before similar future tasks

APIs:

- `POST /v1/memory/ingest`
- `POST /v1/memory/retrieve`

Why it exists:

Repeated failures are expensive, but bad memory is worse than no memory. The
layered manager preserves useful evidence while preventing context pollution
from guesses and transient outputs.

## Model Foundation And Scratch Training

Files:

- `src/aeitron/model_ops/foundation.py`
- `src/aeitron/model_ops/torch_decoder.py`
- `src/aeitron/model_ops/tokenizer_pipeline.py`
- `src/aeitron/model_ops/data_loader.py`
- `src/aeitron/model_ops/pretrain_loop.py`
- `deploy/gpu/`

Purpose:

Define Aeitron-owned scratch model contracts and executable training primitives.
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
python -m src.aeitron.model_ops.tokenizer_pipeline --input data/training/clean.jsonl --tokenizer-out artifacts/aeitron/tokenizer/tokenizer.json --shards-out artifacts/aeitron/shards --vocab-size 64000 --sequence-length 128
python -m src.aeitron.model_ops.pretrain_loop --device cuda --manifest artifacts/aeitron/shards/manifest.json --steps 100 --batch-size 2 --sequence-length 128 --gradient-accumulation-steps 4 --dtype bf16
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

- `src/aeitron/learning/source_registry.py`
- `config/data_sources.ultimate.json`

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
python -m src.aeitron.learning.source_registry --sources config/data_sources.ultimate.json
```

Why it exists:

Best data starts with best sources. Crawling random internet pages creates
noise, legal risk, and model contamination.

Canonical registry structure:

- `sources`: crawl-ready, license-gated sources used directly by the crawler.
- `vulnerability_adapters`: official API-backed vulnerability feeds consumed by
  adapter code, not by the web crawler.
- `review_required_sources`: valuable sources that must be license-approved
  before being promoted into `sources`.

The ultimate registry currently contains 17 crawl-ready sources, 76 seed URLs,
and covers:

- OWASP Cheat Sheets, Top 10, ASVS, WSTG, API Security
- CISA KEV catalog
- NIST SSDF and cryptographic standards
- Python, Rust, Go, Node.js, TypeScript
- FastAPI, Django, pytest, PostgreSQL
- Docker, Kubernetes, Git, OpenSSF Scorecard

High-value sources kept in `review_required_sources` include MITRE CWE/CAPEC,
GitHub CodeQL docs, Semgrep docs/rules, RustSec, Go vulnerability database,
PyPA advisory database, and real permissive-license security patch repositories.

Use the ultimate registry for every local, Kaggle, Colab, and production data
run. Keep source balancing enabled, because large documentation sources can
otherwise dominate training rows.

## Data Source Governance

Files:

- `src/aeitron/learning/governance.py`
- `src/aeitron/learning/resource_catalog.py`
- `config/data_sources.ultimate.json`

Purpose:

Provide an auditable legal/source approval workflow before high-value but
license-sensitive sources become training data. The ultimate registry separates
three categories:

- `sources`: directly crawlable and license-gated.
- `vulnerability_adapters`: official API-backed vulnerability feeds.
- `review_required_sources`: valuable sources that need approval first.
- `training_resources`: 45 external cybersecurity, security-evaluation, and
  agentic-coding resources supplied by the project owner.
- `training_priority_groups`: top six priority groups used to order serious
  data work.

What it stores:

- source approval requests
- approval/rejection decisions
- human-review queue items
- reviewer decisions and reasons

Commands:

```bash
python -m src.aeitron.learning.governance --store artifacts/aeitron/governance report
python -m src.aeitron.learning.governance --store artifacts/aeitron/governance submit-source --source-name portswigger-web-security-academy --category authorized_security_testing_labs --url https://portswigger.net/web-security --license review-required --evidence-url https://portswigger.net/web-security --requested-by security-team --justification "High-value authorized web security education source"
python -m src.aeitron.learning.resource_catalog --catalog config/data_sources.ultimate.json --output artifacts/aeitron/resource_catalog_report.json
```

Why it exists:

Cybersecurity data can be high-value and legally sensitive at the same time.
This module prevents accidental ingestion of unclear sources while still giving
the project a path to approve excellent security-testing education, advisory
databases, and real patch repositories.

## Vulnerability Database Adapters

Files:

- `src/aeitron/learning/vulnerability_adapters.py`

Supported adapters:

- CISA KEV
- NVD CVE 2.0
- OSV
- Go vulnerability database
- GitHub Advisory Database

What they output:

Normalized defensive JSONL records with source, vulnerability ID, summary,
details, affected packages, CWE IDs, references, severity, license, provenance,
content hash, and text.

Commands:

```bash
python -m src.aeitron.learning.vulnerability_adapters --adapter cisa-kev --output artifacts/aeitron/vulns/cisa-kev.jsonl --max-records 100
python -m src.aeitron.learning.vulnerability_adapters --adapter nvd-cve --output artifacts/aeitron/vulns/nvd.jsonl --max-records 100
python -m src.aeitron.learning.vulnerability_adapters --adapter go-vuln --output artifacts/aeitron/vulns/go.jsonl --max-records 100
```

Why it exists:

Official vulnerability feeds produce cleaner cybersecurity training data than
random scraping because they include structured IDs, references, affected
packages, timestamps, and provenance.

## Crawl Frontier And Data Engine

Files:

- `src/aeitron/learning/data_engine.py`
- `src/aeitron/learning/supervisor.py`

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
python -m src.aeitron.learning.data_engine --sources config/data_sources.ultimate.json --frontier artifacts/aeitron/data-engine/frontier.sqlite3 --raw-output-dir artifacts/aeitron/data-engine/raw --clean-output-dir artifacts/aeitron/data-engine/clean --max-docs 1000 --workers 8 --max-depth 1
python -m src.aeitron.learning.data_engine --sources config/data_sources.ultimate.json --frontier-backend postgres --postgres-dsn "$AEITRON_DATABASE_URL" --raw-output-dir artifacts/aeitron/data-engine/raw --clean-output-dir artifacts/aeitron/data-engine/clean --max-docs 1000000 --workers 64
python -m src.aeitron.learning.supervisor --sources config/data_sources.ultimate.json --postgres-dsn "$AEITRON_DATABASE_URL" --raw-output-dir artifacts/aeitron/data-engine/raw --clean-output-dir artifacts/aeitron/data-engine/clean --object-store-uri s3://aeitron-datasets/pretraining --worker-replicas 8 --async-workers 64
```

The supervisor repeatedly launches bounded crawl cycles, writes heartbeat and
status JSON files, applies readiness checks before starting, and stops after too
many failures. In production it runs beside distributed workers and object
storage, not as a local-only script.

Why it exists:

Million-scale data requires resume, retry, dedup, and distributed URL claiming.
A simple script cannot safely collect large corpora.

## Quality Gate

Files:

- `src/aeitron/learning/quality.py`

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

- `src/aeitron/learning/benchmark_contamination_filter.py`

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

- `src/aeitron/learning/quality_inspector.py`

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
python -m src.aeitron.learning.quality_inspector --input artifacts/aeitron/data-pipeline/clean/clean-000000.jsonl --output artifacts/aeitron/data-pipeline/reports/quality_report.json
```

Why it exists:

After crawling, you need to know what you actually collected. Counts alone are
not enough.

## Source Quality Scoring

Files:

- `src/aeitron/learning/quality.py`
- `src/aeitron/learning/quality_inspector.py`
- `src/aeitron/learning/source_quality.py`

Purpose:

Score each source based on accepted rows, quality score, code coverage, and
defensive security coverage.

The row-level quality classifier now scores more than length. It records:

- `component_scores.length`
- `component_scores.security_signal`
- `component_scores.agentic_signal`
- `component_scores.code_signal`
- `component_scores.test_signal`
- `component_scores.structure`
- `component_scores.low_noise`
- `component_scores.lexical_diversity`
- `risk_flags`
- inferred `language_hint`
- inferred `data_type`

Supported language and artifact signals include Python, Rust, Go, JavaScript,
TypeScript, Java, C/C++, Bash, Solidity, Docker/Kubernetes/config material,
patches, tests, debug traces, CVE/CWE references, and defensive security
documentation.

Hard rejects are reserved for unsafe or unusable rows: too short, too large,
disallowed license, secret-like content, email-like PII, duplicate content, very
low text signal, or extremely degenerate repetition. Weaker signals such as
boilerplate or low lexical diversity become risk flags so useful technical
references are not thrown away too aggressively.

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

- `src/aeitron/learning/task_extraction.py`

Purpose:

Convert clean corpus rows into task candidates for agentic coding and defensive
security training/evaluation.

Task types:

- `security_vulnerability_identification`
- `security_patch_generation`
- `secure_code_review`
- `regression_test_generation`
- `debugging_from_error_trace`
- `implementation_planning`

How it works:

- extracts fenced code blocks and diff blocks when present
- detects vulnerability categories such as SQL injection, XSS, SSRF, command
  injection, deserialization, weak crypto, path traversal, hardcoded secrets,
  buffer overflow, and vulnerability taxonomy references
- converts runtime traces and compile errors into debugging tasks
- converts code artifacts into secure code-review tasks
- converts test-heavy rows into regression-test generation tasks
- builds prompts that preserve source URL/provenance
- attaches `success_criteria` and `negative_constraints` to each task
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

- `src/aeitron/learning/review.py`

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
python -m src.aeitron.learning.review --input artifacts/aeitron/data-pipeline/tasks/tasks.jsonl --decisions-out artifacts/aeitron/data-pipeline/reports/task_review_decisions.jsonl --approved-out artifacts/aeitron/data-pipeline/tasks/approved_tasks.jsonl
```

Why it exists:

Task extraction can create noisy prompts. Training should use approved tasks,
not every generated candidate.

## Benchmark And Data Feedback

Files:

- `src/aeitron/learning/feedback.py`

Purpose:

Combine benchmark results, quality reports, and review reports into
recommendations.

Recommendations can say:

- quality is too low
- task extraction is too noisy
- approved task diversity is too narrow
- security/agentic component scores are weak
- benchmark score is below promotion threshold
- dataset can be promoted

Command:

```bash
python -m src.aeitron.learning.feedback --output artifacts/aeitron/data-pipeline/reports/feedback_report.json --quality-report artifacts/aeitron/data-pipeline/reports/quality_report.json --review-report artifacts/aeitron/data-pipeline/reports/task_review_report.json
```

Why it exists:

Data quality must be tied to model/evaluation outcomes. If benchmark score drops
or task review approval is weak, the dataset should not be promoted blindly.

## Production Dataset Pack

Files:

- `src/aeitron/learning/production_dataset.py`

Purpose:

Turn crawled and cleaned JSONL rows into a governed production training corpus
inside `data/production`. This is the final gate before tokenizer training,
sharding, and scratch pretraining.

It runs these stages:

1. License allowlist filtering.
2. Dataset quality scoring and metadata injection.
3. Benchmark contamination filtering.
4. Exact and near-duplicate removal.
5. Source quality scoring.
6. Source reputation scoring.
7. Source budget planning for the next crawl.
8. Training data gate promotion.
9. Verified patch/task row normalization.
10. Human-review approved high-value row promotion.
11. Benchmark holdout separation.
12. Train/validation/test split.
13. Dataset validation.
14. Dataset version manifest writing.

Required production evidence:

- 100k to 1M+ promoted clean records.
- explicit `license` per row.
- provenance metadata per row.
- contamination-clean report.
- source reputation and source budget reports.
- near-duplicate report.
- train/validation/test split manifest.
- benchmark holdout separation report.
- verified patch/task dataset report.
- human-review approved high-value row report.
- dataset version manifest.

Command:

```bash
python -m src.aeitron.learning.production_dataset \
  --input artifacts/aeitron/data-runs/first-serious-run/clean/*.jsonl \
  --output-dir data/production/aeitron-corpus-v1 \
  --dataset-id aeitron-corpus-v1 \
  --source-registry config/data_sources.ultimate.json \
  --benchmark-holdout data/eval/humaneval.jsonl \
  --benchmark-holdout data/eval/mbpp.jsonl \
  --verified-patch artifacts/aeitron/verified-patches/verified_patch_tasks.jsonl \
  --human-review-approved artifacts/aeitron/review/approved_high_value.jsonl \
  --min-promoted-records 100000 \
  --min-verified-patch-records 100 \
  --min-human-review-approved-records 100 \
  --min-train-records 90000
```

Outputs:

- `data/production/<dataset>/final/train.jsonl`
- `data/production/<dataset>/final/val.jsonl`
- `data/production/<dataset>/final/test.jsonl`
- `data/production/<dataset>/final/holdout.jsonl`
- `data/production/<dataset>/review/human_review_queue.jsonl`
- `data/production/<dataset>/reports/*.json`
- `data/production/<dataset>/dataset_version_manifest.json`
- `data/production/<dataset>/dataset_version_manifest.md`

Production behavior:

- Missing or insufficient real data fails the command.
- Benchmark leakage is removed before final split.
- Dev-smoke mode is allowed only for code-path validation and cannot be treated
  as production data proof.

## Dataset Versioning And Ledger

Files:

- `src/aeitron/learning/versioning.py`

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

- `src/aeitron/learning/storage.py`

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

- `src/aeitron/learning/dashboard.py`

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

- `src/aeitron/learning/production_check.py`

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
python -m src.aeitron.learning.production_check --sources config/data_sources.ultimate.json --frontier-backend postgres --postgres-dsn "$AEITRON_DATABASE_URL" --object-store-uri s3://aeitron-datasets/pretraining --production --worker-replicas 8 --async-workers 64
```

Why it exists:

Production data jobs are expensive. The system should block obviously unsafe
configuration before crawling begins.

## Capacity Planner

Files:

- `src/aeitron/learning/capacity.py`

Purpose:

Estimate storage, bandwidth, days to completion, and recommended worker replica
count.

Command:

```bash
python -m src.aeitron.learning.capacity --target-documents 1000000000 --target-days 30 --worker-replicas 32 --async-workers-per-replica 32
```

Example meaning:

If 1B documents at 64KB average are targeted, raw storage is about 64TB. If
workers are too few, the planner recommends how many replicas are needed for
the target schedule.

Why it exists:

"Billion-scale" is not just code. It is storage, bandwidth, workers, and time.

## First Serious Run Planner

Files:

- `src/aeitron/learning/run_plan.py`

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
python -m src.aeitron.learning.run_plan --sources config/data_sources.ultimate.json --output-dir artifacts/aeitron/data-runs/first-serious-run --target-documents 1000000 --target-days 7 --postgres-dsn "$AEITRON_DATABASE_URL" --object-store-uri s3://aeitron-datasets/pretraining --worker-replicas 8 --async-workers 64
```

Why it exists:

Before collecting a real dataset, you need a reproducible plan and exact
commands. This prevents ad hoc data runs.

## End-To-End Data Pipeline

Files:

- `src/aeitron/learning/data_pipeline.py`

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
python -m src.aeitron.learning.data_pipeline --sources config/data_sources.ultimate.json --dataset-id aeitron-defensive-coding-corpus --work-dir artifacts/aeitron/data-pipeline --frontier-backend postgres --postgres-dsn "$AEITRON_DATABASE_URL" --object-store-uri s3://aeitron-datasets/pretraining --object-store-endpoint-url "$S3_ENDPOINT_URL" --max-docs 1000000 --workers 64 --max-depth 2 --vocab-size 64000 --sequence-length 2048 --shard-token-count 1000000 --skip-train
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

- `src/aeitron/learning/worker.py`
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

## Real Approved-Source GPU Training Run

Files:

- `deploy/gpu/run_real_data_training_pipeline.py`
- `src/aeitron/learning/data_pipeline.py`
- `src/aeitron/evaluation/checkpoint_eval.py`

Purpose:

Run the first serious end-to-end training path from approved internet sources:

1. Load approved source registry.
2. Crawl only allowlisted domains.
3. Respect robots policy by default.
4. Deduplicate by content hash.
5. Run quality filtering.
6. Run contamination detection.
7. Extract coding/security tasks.
8. Review extracted tasks.
9. Train Aeitron tokenizer.
10. Build token shards.
11. Run scratch-only GPU pretraining.
12. Save checkpoint manifest.
13. Evaluate checkpoint integrity and training stability.
14. Run built-in defensive/security/coding benchmark harness.
15. Write JSON and Markdown reports.

Why this exists:

Toy data proves plumbing. Real approved-source data proves that the architecture
can ingest web data, filter it, convert it into training shards, train on GPU,
and produce auditable evidence after the checkpoint.

Kaggle T4 smoke command:

```bash
python deploy/gpu/run_real_data_training_pipeline.py \
  --sources config/data_sources.ultimate.json \
  --output-dir artifacts/aeitron/real-data-smoke \
  --max-docs 200 \
  --min-clean-records 25 \
  --workers 8 \
  --max-depth 1 \
  --delay-seconds 0.2 \
  --vocab-size 8000 \
  --sequence-length 64 \
  --validation-fraction 0.05 \
  --train-steps 200 \
  --train-batch-size 4 \
  --gradient-accumulation-steps 4 \
  --dtype fp16 \
  --device cuda
```

First 10k-record command:

```bash
python deploy/gpu/run_real_data_training_pipeline.py \
  --sources config/data_sources.ultimate.json \
  --output-dir artifacts/aeitron/real-data-10k \
  --max-docs 10000 \
  --min-clean-records 10000 \
  --workers 16 \
  --max-depth 2 \
  --delay-seconds 0.5 \
  --vocab-size 64000 \
  --sequence-length 128 \
  --validation-fraction 0.02 \
  --train-steps 1000 \
  --train-batch-size 4 \
  --gradient-accumulation-steps 8 \
  --early-stopping-patience 8 \
  --max-source-fraction 0.35 \
  --dtype fp16 \
  --device cuda
```

Top-class balanced 20k-record command:

```bash
python deploy/gpu/run_real_data_training_pipeline.py \
  --sources config/data_sources.ultimate.json \
  --work-dir artifacts/aeitron/real-data-20k-top-class-balanced \
  --target-records 20000 \
  --max-docs 50000 \
  --max-bytes-per-doc 300000 \
  --workers 24 \
  --max-depth 2 \
  --delay-seconds 0.35 \
  --vocab-size 64000 \
  --sequence-length 256 \
  --validation-fraction 0.02 \
  --steps 5000 \
  --batch-size 4 \
  --gradient-accumulation-steps 8 \
  --validation-interval 100 \
  --early-stopping-patience 10 \
  --max-source-fraction 0.30 \
  --dtype fp16 \
  --device cuda
```

Kaggle memory safety:

- Exit code `137` means the Kaggle/Linux runtime killed the process, usually
  because host RAM was exhausted.
- The tokenizer and token-shard builders stream JSONL line by line and should
  not load full corpus shards into memory.
- Source balancing is a two-pass streaming process: first count source rows,
  then write capped rows without keeping the full corpus in memory.
- `deploy/gpu/run_real_data_training_pipeline.py` defaults to
  `--max-bytes-per-doc 300000` so huge documentation pages do not dominate RAM.
- If Kaggle still kills the run, reduce `--workers`, `--max-docs`,
  `--sequence-length`, or `--batch-size`, and use a fresh `--work-dir`.

100k-record command:

```bash
python deploy/gpu/run_real_data_training_pipeline.py \
  --sources config/data_sources.ultimate.json \
  --output-dir artifacts/aeitron/real-data-100k \
  --max-docs 100000 \
  --min-clean-records 100000 \
  --workers 32 \
  --max-depth 3 \
  --delay-seconds 0.5 \
  --vocab-size 64000 \
  --sequence-length 128 \
  --validation-fraction 0.02 \
  --train-steps 10000 \
  --train-batch-size 4 \
  --gradient-accumulation-steps 8 \
  --dtype fp16 \
  --device cuda
```

Important:

- Kaggle is good for a real smoke or small training run.
- Production-scale 10k-100k+ collection is better on a long-running VM or
  Kubernetes worker setup.
- The crawler is defensive and allowlist-based. It is not a general web
  scraper for unauthorized collection.
- `--min-clean-records` intentionally blocks the run if the crawl does not
  produce enough accepted records.
- For short smoke runs, validation must be scheduled inside the run length.
  `deploy/gpu/run_real_data_training_pipeline.py` defaults to
  `--validate-every 25`, and clamps validation cadence to `--train-steps` so a
  50-step run can produce validation losses.
- Use a fresh `--output-dir` for each run. The pipeline now overwrites its own
  `raw-*.jsonl` and `clean-*.jsonl` shards at startup to prevent stale partial
  JSONL lines from interrupted runs contaminating the next run.
- JSONL readers report the exact file and line number if a malformed row is
  encountered.
- JSONL readers stream by physical newline instead of Python `splitlines()` so
  Unicode line separators inside JSON strings do not split valid records.
- Non-text HTTP responses such as `image/png`, fonts, archives, media, and PDFs
  are rejected before raw/clean JSONL writing.
- A `.pipeline.lock` file prevents two processes from writing to the same
  `--output-dir` at the same time. If a lock error appears, use a fresh output
  directory or wait for the existing run to finish.

Primary outputs:

- `reports/real_data_training_report.json`
- `reports/pipeline_report.json`
- `reports/quality_report.json`
- `reports/training_quality_report.json`
- `reports/source_quality_report.json`
- `reports/source_balance_report.json`
- `reports/contamination_report.json`
- `reports/task_review_report.json`
- `reports/feedback_report.json`
- `reports/checkpoint_eval/checkpoint_eval_report.json`
- `reports/checkpoint_eval/benchmarks/built_in_security_benchmark.md`
- `tokenizer/tokenizer.json`
- `shards/manifest.json`
- `train/checkpoint_manifest.json`
- `train/best_checkpoint_manifest.json`

Checkpoint evaluation:

`src/aeitron/evaluation/checkpoint_eval.py` verifies:

- checkpoint files exist
- checkpoint file hashes match the checkpoint manifest
- validation-best checkpoint selection is being used when available
- training loss is finite and non-exploding
- validation loss is finite when validation batches exist
- built-in defensive/security/coding benchmark harness passes

Best checkpoint and early stopping:

- `src/aeitron/model_ops/pretrain_loop.py` writes both:
  - `train/checkpoint_manifest.json` for the final checkpoint
  - `train/best_checkpoint_manifest.json` for the best validation checkpoint
- `training.best_validation_loss` and `training.best_validation_step` record the
  selected checkpoint.
- `--early-stopping-patience` stops training after repeated validation checks
  fail to improve by `--early-stopping-min-delta`.
- The real-data GPU runner defaults to `--early-stopping-patience 8`.
- Checkpoint evaluation uses `best_checkpoint_manifest` when present.

Source balancing:

- `src/aeitron/learning/source_balancing.py` creates
  `balanced/balanced-clean-000000.jsonl` before tokenizer and shard training.
- The default `--max-source-fraction 0.35` prevents a single source such as
  `git-documentation` from dominating model training.
- `reports/source_balance_report.json` records input rows, output rows, and
  capped sources.
- Disable only for diagnostic runs with `--no-source-balancing`.

Scratch instruction mix:

- `src/aeitron/learning/mixer.py` now owns the production scratch data mixer.
- The real-data pipeline runs it after license filtering, benchmark
  contamination filtering, near-deduplication, training-data promotion, and
  source balancing.
- Raw rows are converted into explicit scratch training records with
  `<|thought_start|>`, analysis target, correct answer, `<|patch_start|>`,
  code patch or patch plan, tests, and verification result.
- Default token ratio target:
  - 40% `instruction_security_coding`
  - 30% `verified_patch_tests`
  - 20% `high_quality_docs_code`
  - 10% `debugging_error_logs`
- Verified patch/test examples are classified before general security/coding
  rows, so real fixes and regression evidence get priority.
- `reports/instruction_mix_report.json` records input paths, output JSONL,
  bucket rows, estimated token ratios, rejected rows, and recommendations for
  under-supplied buckets.
- The dataset version manifest includes both the instruction mix report and
  `mixed/scratch-instruction-mix.jsonl`.
- Disable only for diagnosis with `--no-instruction-mix`; production mode
  requires instruction mixing.

Checkpoint pass/fail gate:

- `--checkpoint-compare-prompt-suite` connects the expanded prompt suite
  directly into the real-data pipeline.
- The pipeline evaluates the best checkpoint with deterministic generation and
  writes `reports/checkpoint_compare/checkpoint_comparison_report.json`.
- `--checkpoint-compare-min-score` blocks a run when the checkpoint output
  quality is below the required minimum.
- A regressed checkpoint also blocks the run. This keeps "training completed"
  separate from "checkpoint should be promoted."

Expanded built-in benchmark:

- `src/aeitron/evaluation/benchmarks.py` includes SQL injection, hardcoded
  secrets, command injection, path traversal, XSS, weak crypto, insecure random,
  unsafe deserialization, C buffer copy, SSRF, unsafe YAML loading, unsafe JWT
  settings, open redirect, Node.js command execution, TypeScript DOM XSS, Go SQL
  injection, Rust command execution, Java deserialization, Solidity reentrancy
  shape, Docker hardening, Kubernetes privileged containers, GitHub Actions
  script injection, debugging trace shape, regression-test shape, and patch-shape
  checks.
- This is still a lightweight built-in gate, not a replacement for SWE-Bench or
  a full security benchmark suite.

Training safety preflight:

- the pretraining loop checks that shards can produce at least one batch before
  allocating the model
- the scratch model vocabulary is automatically expanded to match the tokenizer
  vocabulary and the highest token ID found in train/validation shards
- this prevents CUDA device-side asserts from tokenizer/model vocabulary
  mismatch
- the pretraining report includes `model_config` so the exact executable model
  shape is auditable after each run

Standalone checkpoint eval:

```bash
python -m src.aeitron.evaluation.checkpoint_eval \
  --checkpoint-manifest artifacts/aeitron/real-data-smoke/train/checkpoint_manifest.json \
  --training-report artifacts/aeitron/real-data-smoke/train/pretrain_report.json \
  --output-dir artifacts/aeitron/real-data-smoke/reports/checkpoint_eval
```

## Checkpoint Learning Comparison

Files:

- `src/aeitron/model_ops/checkpoint_compare.py`
- `deploy/gpu/run_checkpoint_comparison.py`

Purpose:

Measure whether a newer scratch checkpoint is behaviorally better than an older
checkpoint on the same deterministic prompt suite. This answers whether the
model learned more than before, not just whether training loss moved.

How it works:

1. Load a baseline checkpoint manifest.
2. Load a candidate checkpoint manifest.
3. Load the exact tokenizer used by the training run.
4. Run the same fixed coding/security/debugging prompts through both models.
5. Score outputs locally with deterministic heuristics:
   - expected security/coding terms found
   - forbidden unsafe terms avoided
   - output is non-empty
   - output has basic structure signals
   - repetition is not excessive
6. Write JSON and Markdown reports with per-task deltas.

Default prompt categories:

- SQL injection finding and safe patch
- XSS review and safe fix
- Python traceback debugging
- FastAPI JWT middleware planning
- empty-password patch and regression tests

Compare final vs best checkpoint from a real data run:

```bash
python deploy/gpu/run_checkpoint_comparison.py \
  --training-report artifacts/aeitron/real-data-20k-v3-kaggle-safe/reports/real_data_training_report.json \
  --output-dir artifacts/aeitron/real-data-20k-v3-kaggle-safe/reports/checkpoint_compare \
  --device cuda \
  --max-new-tokens 96
```

Compare two explicit checkpoint manifests:

```bash
python deploy/gpu/run_checkpoint_comparison.py \
  --baseline-manifest artifacts/aeitron/run-a/train/best_checkpoint_manifest.json \
  --candidate-manifest artifacts/aeitron/run-b/train/best_checkpoint_manifest.json \
  --tokenizer artifacts/aeitron/run-b/tokenizer/tokenizer.json \
  --output-dir artifacts/aeitron/checkpoint-compare/run-a-vs-run-b \
  --device cuda
```

Outputs:

- `checkpoint_comparison_report.json`
- `checkpoint_comparison_report.md`

Interpretation:

- `status=improved`: candidate scored higher without meaningful regressions.
- `status=neutral`: candidate is not clearly better yet.
- `status=regressed`: candidate should not be promoted without inspection.
- `score_delta`: average score movement across the prompt suite.
- `pass_delta`: number of tasks crossing the pass threshold.

## Scratch Training Control Plane

Files:

- `config/eval_schedule.json`
- `config/mix_ratios.json`
- `src/aeitron/evaluation/eval_runner.py`
- `src/aeitron/learning/mixer.py`
- `src/aeitron/learning/ablation_runner.py`
- `src/aeitron/model_ops/pretrain_loop.py`
- `src/aeitron/model_ops/tokenizer_pipeline.py`
- `src/aeitron/model_ops/sharding.py`

Purpose:

This layer controls checkpoint promotion, data composition, tokenizer/shard
preparation, and scratch pretraining. Aeitron does not include any post-training
adaptation path. Every model weight update must come from the scratch
pretraining stack using Aeitron-owned checkpoints and governed datasets. This
keeps the architecture simple, auditable, and consistent with the no-borrowed-
model policy.

Checkpoint eval loop:

1. Load a scratch checkpoint manifest.
2. Load `config/eval_schedule.json`.
3. Run deterministic evaluation with `temperature=0` and a fixed seed.
4. Execute built-in defensive security checks, optional JSONL benchmark
   adapters, MCQ-style scored rows, static benchmark rows, and generation
   suites when a tokenizer is supplied.
5. Required missing benchmark files fail the report.
6. Optional missing benchmark files are marked `skipped`.
7. Compare aggregate scores against a previous report when supplied.
8. Flag score drops over 3 percent as warnings and over 5 percent as failures.
9. Write `eval_report.json` and `eval_report.md`.

Command:

```powershell
python -m src.aeitron.evaluation.eval_runner `
  --checkpoint-manifest artifacts\\aeitron\train\checkpoint_manifest.json `
  --schedule config\eval_schedule.json `
  --output-dir artifacts\\aeitron\eval `
  --tokenizer-path artifacts\\aeitron\tokenizer\tokenizer.json `
  --device cpu
```

Data mixing controller:

1. Read clean JSONL rows that already passed license, contamination, quality,
   and dedup gates.
2. Classify each row into `general`, `code`, `cybersecurity`, or `agentic`.
3. Exclude `eval_holdout` and `benchmark_holdout` rows from training output.
4. Estimate token counts with the tokenizer when available.
5. Sample rows according to the configured experiment ratios:
   - `baseline_70_15_15`
   - `domain_heavy_55_15_30`
   - `domain_extreme_40_10_50`
6. Write mixed JSONL and, when a tokenizer is supplied, compatible token
   shards for the pretraining loop.
7. Write `mix_manifest.json`.

Command:

```powershell
python -m src.aeitron.learning.mixer `
  --inputs data\training\clean.jsonl `
  --config config\mix_ratios.json `
  --experiment domain_heavy_55_15_30 `
  --output-dir artifacts\\aeitron\mix-domain-heavy `
  --tokenizer-path artifacts\\aeitron\tokenizer\tokenizer.json
```

Ablation runner:

The ablation runner executes every configured mix experiment against the same
clean corpus and writes `ablation_report.json` plus a Markdown summary. It is
used to compare whether a general-heavy, domain-heavy, or domain-extreme corpus
produces better downstream checkpoint eval results.

Command:

```powershell
python -m src.aeitron.learning.ablation_runner `
  --mix-config config\mix_ratios.json `
  --base-run-dir artifacts\\aeitron\data-pipeline `
  --output-dir artifacts\\aeitron\mix-ablation
```

Scratch-only training rule:

1. Raw crawl rows never train directly.
2. Only promoted rows from the data gate enter tokenizer training, sharding, or
   scratch pretraining.
3. Instruction-like, repair-like, and safety-related examples are treated as
   ordinary pretraining/curriculum text unless a future architecture decision
   explicitly reopens a separate post-training stage.
4. There is no adapter-based, pairwise post-training, or external-checkpoint
   tuning path in Aeitron.
5. Checkpoints are promoted only through validation loss, benchmark gates,
   security gates, and regression comparison.

Scratch pretraining command:

```powershell
python -m src.aeitron.learning.data_pipeline `
  --sources config\data_sources.ultimate.json `
  --dataset-id aeitron-defensive-coding-corpus `
  --work-dir artifacts\\aeitron\data-pipeline `
  --vocab-size 64000 `
  --sequence-length 2048 `
  --shard-token-count 1000000
```

Security boundary:

- Allowed: authorized labs, CTF/eval data, vulnerability metadata, defensive
  analysis, secure patch generation, and reviewed educational material.
- Blocked: autonomous exploit execution, malware collection, live-target attack
  workflows, credential theft instructions, and unsupervised harmful misuse data.

## Production Readiness Hardening

Files:

- `alembic.ini`
- `src/aeitron/db/alembic/env.py`
- `src/aeitron/db/alembic/versions/0001_initial.py`
- `src/aeitron/db/alembic/versions/0002_data_platform.py`
- `src/aeitron/learning/storage.py`
- `src/aeitron/learning/dataset_validation.py`
- `src/aeitron/deployment/k8s_validate.py`
- `src/aeitron/evaluation/benchmark_suites.py`
- `src/aeitron/security/audit.py`
- `deploy/gpu/run_10k_training_validation.py`
- `deploy/prod/prometheus.yml`
- `deploy/prod/grafana-dashboard.json`
- `deploy/prod/otel-collector.yaml`

Purpose:

This layer turns the architecture from local MVP code into a deployable
production candidate. It does not magically prove billion-scale operation on a
single laptop; it provides the gates and commands that must pass on real
infrastructure before production promotion.

Postgres migration strategy:

- The existing SQL migration runner remains available for lightweight Docker
  and CI workflows.
- Alembic is now configured for standard production migration operations.
- The Alembic version scripts reuse the same SQL migration files, keeping one
  source of truth.
- Migrations are forward-only to avoid destructive rollback surprises.

Commands:

```powershell
python -m src.aeitron.db.migration_runner --database-url postgresql://aeitron:pass@localhost:5432/aeitron --dry-run
alembic upgrade head
```

Object storage lifecycle:

- Supports local storage and S3-compatible storage such as MinIO.
- Verifies write, head, download, checksum match, list, and delete.
- Writes `object_store_lifecycle_report.json`.

Commands:

```powershell
python -m src.aeitron.learning.storage `
  --uri local://artifacts/aeitron/object-store `
  --work-dir artifacts/aeitron/object-store-lifecycle

python -m src.aeitron.learning.storage `
  --uri s3://aeitron-datasets/pretraining `
  --endpoint-url http://localhost:9000 `
  --work-dir artifacts/aeitron/s3-lifecycle
```

Kubernetes validation:

- Loads every YAML manifest under `deploy/k8s`.
- Checks workload resources, probes, secret references, privileged containers,
  privilege escalation, PVCs, HPA presence, services, and network policy.
- Optional `--kubectl-dry-run` performs server-side validation against a real
  cluster.
- Placeholder secrets in `secrets.example.yaml` are warnings, not blockers.

Commands:

```powershell
python -m src.aeitron.deployment.k8s_validate --output-dir artifacts/aeitron/k8s-validation
python -m src.aeitron.deployment.k8s_validate --kubectl-dry-run --output-dir artifacts/aeitron/k8s-validation
```

Long-running crawler supervision:

- `src/aeitron/learning/supervisor.py` runs supervised crawl cycles against a
  Postgres frontier.
- It writes heartbeat and status JSON for external monitoring.
- Docker Compose and Kubernetes include crawler worker and supervisor services.
- The data dashboard summarizes crawl, quality, license, contamination,
  source reputation, budget, task extraction, review, upload, and feedback
  status.

Large dataset validation:

- `src/aeitron/learning/dataset_validation.py` streams JSONL files and does not
  load the full corpus into memory.
- It checks record count, duplicate fraction, average text length, category
  coverage, license presence, quality metadata, and holdout/train split.
- Use it before tokenizer training and before promoting a dataset version.

Command:

```powershell
python -m src.aeitron.learning.dataset_validation `
  --inputs artifacts/aeitron/data-pipeline/clean/clean-000000.jsonl `
  --output-dir artifacts/aeitron/dataset-validation `
  --min-records 100000 `
  --max-duplicate-fraction 0.02
```

10k-step GPU validation:

- `deploy/gpu/run_10k_training_validation.py` enforces at least 10,000 training
  steps.
- It runs the scratch pretraining loop and checkpoint eval.
- Use this on Kaggle/Colab T4/A100/L4/P100-compatible PyTorch builds or on a
  real GPU node.
- On Kaggle/Colab, install `requirements-kaggle-smoke.txt` first. It avoids
  `vllm`, `deepspeed`, and torch reinstallations that can break the hosted CUDA
  runtime. Use `requirements-linux-gpu.txt` only on controlled GPU machines or
  containers.
- Long-running GPU/data commands emit structured progress to stdout and to
  `progress.jsonl`. Each event has `stage`, `status`, `ts_unix`, and metrics
  such as fetched docs, accepted rows, train loss, validation loss, trained
  tokens, checkpoint paths, and final report paths.

Command:

```bash
python deploy/gpu/run_10k_training_validation.py \
  --manifest artifacts/aeitron/shards/manifest.json \
  --device cuda \
  --steps 10000 \
  --sequence-length 128 \
  --batch-size 2 \
  --gradient-accumulation-steps 8
```

Live progress example:

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

tail -n 80 artifacts/aeitron/kaggle-real-data-smoke/progress.jsonl
```

Kaggle validation preset:

```bash
%%bash
cd /kaggle/working/aeitron-agentic-ai
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

Kaggle notebooks may buffer `%%bash` output until the process exits. For real
live progress, run the job in the background and tail the progress file in a
second cell.

Cell 1:

```bash
%%bash
cd /kaggle/working/aeitron-agentic-ai
git pull origin master
mkdir -p artifacts/aeitron/real-data-10k-strict-v1
PYTHONUNBUFFERED=1 nohup python -u deploy/gpu/run_real_data_training_pipeline.py \
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
  --progress-every-steps 25 \
  > artifacts/aeitron/real-data-10k-strict-v1/run.log 2>&1 &
echo $! > artifacts/aeitron/real-data-10k-strict-v1/run.pid
cat artifacts/aeitron/real-data-10k-strict-v1/run.pid
```

Cell 2:

```bash
%%bash
cd /kaggle/working/aeitron-agentic-ai
tail -f artifacts/aeitron/real-data-10k-strict-v1/progress.jsonl
```

When the job finishes:

```bash
%%bash
cd /kaggle/working/aeitron-agentic-ai
tail -n 80 artifacts/aeitron/real-data-10k-strict-v1/run.log
cat artifacts/aeitron/real-data-10k-strict-v1/reports/real_data_training_report.json

python deploy/gpu/run_checkpoint_comparison.py \
  --training-report artifacts/aeitron/real-data-10k-strict-v1/reports/real_data_training_report.json \
  --output-dir artifacts/aeitron/real-data-10k-strict-v1/reports/checkpoint_compare \
  --device cuda
```


Inspect the run and get the next recommended action:

```bash
%%bash
cd /kaggle/working/aeitron-agentic-ai
python deploy/gpu/inspect_real_data_run.py \
  --work-dir artifacts/aeitron/real-data-validation-v1
```
Benchmark suite adapters:

- `swe_bench_style`: local SWE-Bench-like JSONL files.
- `human_eval_style`: local HumanEval-like rows.
- `mbpp_style`: local MBPP-like rows.
- `cyberseceval_style`: local CyberSecEval-like rows.
- `custom_security`: Aeitron-owned security benchmark rows.
- Benchmark files are local/explicit. The system does not automatically
  download or mix protected eval data into training.

Command:

```powershell
python -m src.aeitron.evaluation.benchmark_suites `
  --suite swe swe_bench_style data/eval/swe_style.jsonl `
  --suite cyber cyberseceval_style data/eval/cyber.jsonl `
  --output-dir artifacts/aeitron/benchmark-suites
```

Security audit:

- Scans production source and deployment assets.
- Checks hardcoded secret patterns, SSRF sinks, path traversal sinks, risky
  process execution sinks, dependency version bounds, optional Bandit output,
  and Kubernetes manifest status.
- Tests and deliberately vulnerable benchmark fixtures are excluded from the
  default production-source scan to reduce false positives.

Commands:

```powershell
python -m src.aeitron.security.audit --no-bandit --output-dir artifacts/aeitron/security-audit
python -m src.aeitron.security.audit --output-dir artifacts/aeitron/security-audit
```

Monitoring:

- Prometheus scrapes `/metrics`.
- Grafana dashboard is defined in `deploy/prod/grafana-dashboard.json`.
- Optional OpenTelemetry tracing is enabled when
  `AEITRON_OTEL_EXPORTER_OTLP_ENDPOINT` is set.
- Docker Compose includes Prometheus, Grafana, and an OTel collector under the
  `monitoring` profile.

Command:

```powershell
docker compose --env-file deploy/prod/.env.example -f deploy/prod/docker-compose.yml --profile monitoring up
```

## Strict Training Data Quality Gate

The data pipeline now has a promotion gate between deduplication/source
reputation and tokenizer/shard construction. Its purpose is to keep weak
internet text out of scratch pretraining and to preserve high-value rows for
review instead of silently discarding them.

Pipeline order:

1. Crawl approved sources.
2. Apply license filtering.
3. Remove benchmark contamination.
4. Remove exact and near duplicates.
5. Scan contamination patterns.
6. Inspect quality and source quality.
7. Extract security/coding tasks and automated review decisions.
8. Score source reputation and allocate future source budgets.
9. Promote rows through `training_data_gate.py`.
10. Balance sources, train tokenizer, build shards, and train/evaluate.

The gate writes:

- `gated/training-promoted.jsonl`: rows allowed into training.
- `gated/eval-holdout.jsonl`: promoted rows reserved for local validation.
- `gated/human-review-queue.jsonl`: high-value rows that need review.
- `reports/training_data_gate_decisions.jsonl`: one decision per scanned row.
- `reports/training_data_gate_report.json`: aggregate promotion/rejection
  report.

Gate scoring uses:

- Row quality score.
- Source reputation score.
- Patch/debug/security priority labels.
- Boilerplate and repeated-line risk flags.
- Holdout sampling controlled by `--eval-holdout-fraction`.

Default thresholds:

- `--min-training-quality-score 0.62` in the Kaggle real-data entrypoint.
- `--min-training-average-quality-score 0.62` for the actual tokenizer/training corpus.
- `--min-source-reputation-score 0.50` in the Kaggle real-data entrypoint.
- `--eval-holdout-fraction 0.02`

For strict Kaggle validation on public approved sources, start with:

```bash
python deploy/gpu/run_real_data_training_pipeline.py \
  --sources config/data_sources.ultimate.json \
  --work-dir artifacts/aeitron/real-data-10k-strict-v1 \
  --target-records 10000 \
  --min-training-rows 5000 \
  --min-train-tokens 2000000 \
  --max-docs 16000 \
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
  --max-source-fraction 0.25 \
  --progress-every-steps 25
```

For production dataset builds, use stricter defaults and inspect the gate
report before accepting a dataset version. A serious run should have:

- High promoted count.
- Low boilerplate rejection after source allowlist tuning.
- Non-empty human review queue for high-value security rows.
- Eval holdout separated from training.
- Source mix controlled by `config/mix_ratios.json`.

## Model Quality Build Blocks

These modules are the current production path for improving actual model
quality before large GPU training.

### Strong Real-Corpus Tokenizer

Module:

- `src/aeitron/model_ops/tokenizer_pipeline.py`

Purpose:

- Train a code/security optimized BPE tokenizer on promoted real corpus rows.
- Inject deterministic stress samples for indentation, hex dumps, compile
  errors, memory markers, and Aeitron control tokens.
- Build token shards from the same corpus.
- Write `tokenizer_audit_report.json` with special-token coverage, vocab size,
  sample token counts, source row/character counts, and shard manifest.

Command:

```bash
python -m src.aeitron.model_ops.tokenizer_pipeline \
  --real-corpus-audit \
  --input artifacts/aeitron/real-data-5k-quality-gated/gated/training-promoted.jsonl \
  --output-dir artifacts/aeitron/real-tokenizer-v1 \
  --vocab-size 64000 \
  --min-frequency 2 \
  --shard-token-count 1000000 \
  --sequence-length 128 \
  --validation-fraction 0.02
```

### Verified Security Patch Dataset

Module:

- `src/aeitron/learning/verified_patch_dataset.py`

Purpose:

- Read approved local Git repositories.
- Find security-relevant commits.
- Extract parent commit, patch, changed files, before/after snippets, and
  vulnerability categories.
- Verify the patch applies cleanly to the parent commit using `git apply
  --check` in an isolated temporary clone.
- Write scratch-training JSONL records with repository context, patch text,
  provenance, and verification metadata.

Command:

```bash
python -m src.aeitron.learning.verified_patch_dataset \
  --repo /path/to/approved/permissive/repo \
  --license mit \
  --output artifacts/aeitron/verified-patches/security_patches.jsonl \
  --max-commits-per-repo 500
```

Only use repositories with approved licenses. This does not run exploit code or
attack live targets.

### Repository Indexing + Verified Patch Loop

Module:

- `src/aeitron/patches/verified_loop.py`

Purpose:

- Index the repository.
- Build pre-patch context with changed files pinned.
- Preview/apply patch edits.
- Run configured commands, secret scan, and optional Semgrep/CodeQL checks.
- Reindex and build post-patch context.
- Roll back by default unless `--apply-on-accept` is provided.

Edits file format:

```json
{
  "edits": [
    {
      "path": "src/auth.py",
      "new_content": "def login(user, password):\n    return bool(user) and bool(password)\n"
    }
  ]
}
```

Command:

```bash
python -m src.aeitron.patches.verified_loop \
  --repo /path/to/repo \
  --goal "fix authentication validation" \
  --edits-json artifacts/aeitron/patch-edits.json \
  --command "python -m pytest" \
  --output artifacts/aeitron/patch-loop/report.json
```

### Benchmark Pack

Module:

- `src/aeitron/evaluation/benchmark_pack.py`

Purpose:

- Run local HumanEval-like, MBPP-like, SWE-Bench-like, CyberSecEval-like, and
  Aeitron-owned security suites through one strict report.
- Required benchmark files fail if missing in strict mode.
- Optional custom security suite is skipped if omitted.
- Reports are explicit measured/skipped/failed states, never fake passes.

Command:

```bash
python -m src.aeitron.evaluation.benchmark_pack \
  --human-eval data/eval/humaneval.jsonl \
  --mbpp data/eval/mbpp.jsonl \
  --swe-bench data/eval/swe_bench_style.jsonl \
  --cyberseceval data/eval/cyberseceval_style.jsonl \
  --custom-security data/eval/aeitron_security.jsonl \
  --output-dir artifacts/aeitron/benchmark-pack
```

Current benchmark pack validates local suite adapters and static expected-term
contracts. Full model-generation benchmark scoring requires connecting the
trained Aeitron checkpoint generation runner to these suites.

## Transformer Core

Module:

- `src/aeitron/model_ops/torch_decoder.py`

Current implemented transformer capabilities:

- Decoder-only scratch LM.
- RMSNorm.
- SwiGLU MLP.
- RoPE with scaling factor.
- Grouped-query attention.
- PyTorch SDPA attention path. On supported CUDA/PyTorch builds this can use
  FlashAttention or memory-efficient kernels through PyTorch's SDPA dispatcher.
- Eager attention fallback for portability and debugging.
- Optional sliding-window attention mask through `attention_window`.
- KV-cache inference with `past_key_values`.
- Greedy/top-k sampling generation API.
- Gradient checkpointing support.
- Logit soft-cap option.
- Finite-loss and finite-gradient checks in pretraining.
- Export directory with `model.pt`, `config.json`, and serving compatibility
  metadata, including native Aeitron support status, KV-cache contract,
  vLLM/TensorRT conversion blockers, and deterministic generation defaults.
- Shape-valid scratch profiles: `tiny`, `1b`, `7b`, `32b`, `62b`.
- Cluster training plan generator for FSDP, DeepSpeed ZeRO-2/ZeRO-3, and
  Megatron-style launch contracts. This validates the shard manifest, global
  batch math, token throughput target, node/GPU counts, and required environment
  before a large run is attempted.

Important boundary:

- The code supports large-profile construction and cluster-oriented training
  flags.
- Actual 62B training, DeepSpeed/Megatron/FSDP scaling, vLLM/TensorRT serving,
  and 10k+ step multi-GPU validation still require real Linux CUDA cluster
  hardware. Do not mark those as production-proven until cluster release gates
  have run.
- Native PyTorch FSDP runtime is wired into the pretraining loop for `torchrun`
  execution: it initializes distributed state, maps local CUDA ranks, wraps
  decoder blocks, uses mixed precision when requested, and writes rank-safe full
  checkpoints. DeepSpeed and Megatron paths are launch/readiness contracts until
  their dedicated engine adapters pass cluster release gates.

Tiny transformer smoke:

```powershell
python -m unittest tests.test_aeitron_scratch_decoder
```

Pretraining loop with memory-efficient settings:

```bash
python -m src.aeitron.model_ops.pretrain_loop \
  --manifest artifacts/aeitron/real-tokenizer-v1/shards/manifest.json \
  --output-dir artifacts/aeitron/pretrain-eager-gc \
  --device cuda \
  --dtype fp16 \
  --model-profile tiny \
  --attention-impl auto \
  --gradient-checkpointing \
  --steps 1000 \
  --batch-size 2 \
  --sequence-length 128 \
  --gradient-accumulation-steps 8
```

62B config dry contract:

```python
from src.aeitron.model_ops.torch_decoder import model_profile
profile = model_profile("62b")
print(profile.parameter_estimate(), profile.model_dump())
```

Cluster training plan:

```bash
python -m src.aeitron.model_ops.pretrain_loop \
  --cluster-plan-only \
  --distributed-strategy fsdp \
  --manifest artifacts/aeitron/real-tokenizer-v1/shards/manifest.json \
  --output-dir artifacts/aeitron/cluster-runs/Aeitron-62b \
  --cluster-plan-out artifacts/aeitron/cluster-runs/Aeitron-62b/cluster_training_plan.json \
  --model-profile 62b \
  --num-nodes 8 \
  --gpus-per-node 8 \
  --sequence-length 8192 \
  --batch-size 1 \
  --gradient-accumulation-steps 16 \
  --steps 10000 \
  --dtype bf16 \
  --attention-impl auto \
  --gradient-checkpointing
```

What the plan proves:

- the training manifest exists
- the selected scratch model profile is shape-valid
- global batch size and tokens per optimizer step are explicit
- required distributed environment variables are visible before launch
- warnings appear when hardware is too small for the selected profile

What the plan does not prove:

- that the cluster has enough GPU memory
- that NCCL networking is healthy
- that DeepSpeed/Megatron/FSDP actually completed a checkpoint on your cluster
- that exported weights are vLLM/TensorRT native without adapter/conversion work

Those are cluster release-gate tasks, not laptop/Kaggle smoke tasks.

## Production Readiness Contract

Module:

- `src/aeitron/production_readiness.py`
- `src/aeitron/shared/config_contracts.py`
- `config/mix_ratios.json`
- `config/eval_schedule.json`
- `config/active_model_profile.json`
- `config/security_audit_excludes.json`
- `config/verifier_policy.json`

Purpose:

Aeitron must never silently claim production readiness when it is only locally
smoke-tested. The readiness contract classifies each subsystem using explicit
machine-readable states:

- `production_ready`
- `production_ready_requires_external_service`
- `built_not_cluster_proven`
- `blocked_missing_dependency`
- `not_implemented`

Covered subsystems:

- auth and quota
- native Aeitron serving
- Postgres, object storage, Qdrant, and OpenTelemetry config
- Semgrep, CodeQL, Bandit, pip-audit, Docker, and kubectl availability
- CUDA pretraining runtime
- FSDP, DeepSpeed/Megatron, vLLM/TensorRT
- required benchmark files

Development readiness report:

```powershell
python -m src.aeitron.production_readiness --mode dev --output-dir artifacts\\aeitron\production-readiness
```

Production readiness report:

```powershell
python -m src.aeitron.production_readiness --mode production --benchmark-dir data\eval --output-dir artifacts\\aeitron\production-readiness
```

Production mode is expected to fail until real external infrastructure is
configured. That is intentional. A missing Redis, Postgres, S3/MinIO, Qdrant,
Semgrep, CodeQL, Docker, kubectl, CUDA runtime, or benchmark suite must be a
visible blocker, not a hidden warning.

Live production proof:

`production_readiness` tells you whether configuration claims are valid.
`production_proof` actually touches the running dependencies and writes measured
evidence. It verifies:

- Postgres migration dry-run or live migration apply
- Redis regenerative quota execution through the Lua-backed quota store
- local or S3/MinIO object-store write/read/delete lifecycle
- Qdrant HTTP health
- native Aeitron serving health and model listing
- optional OpenAI-compatible chat load test
- governed benchmark pack presence and minimum production coverage
- security audit report

Validation mode:

```powershell
python -m src.aeitron.deployment.production_proof `
  --object-store-uri local://artifacts/aeitron/proof-object-store `
  --output-dir artifacts\\aeitron\production-proof-validation
```

Validation mode is for local/Kaggle proof of code paths. Missing external
services are marked `skipped`, not `passed`.

Strict production mode:

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
  --benchmark-dir data\eval `
  --run-security-audit `
  --strict-security-tools `
  --output-dir artifacts\\aeitron\production-proof
```

Strict mode fails if required dependencies are missing or unhealthy. This is the
command to use after starting the production Docker Compose stack or a
Kubernetes deployment.

Configuration contract layer:

- All production-critical JSON configs are validated through strict Pydantic
  contracts before runtime use.
- `mix_ratios.json` enforces exact ratio sums, protected holdout policies,
  source budget metadata, scratch instruction mix ratios, and minimum bucket
  requirements.
- `eval_schedule.json` includes benchmark-level minimum scores, protected
  holdout flags, repetition-collapse thresholds, promotion policy, safety
  targets, and regression thresholds.
- `active_model_profile.json` must declare scratch-only status, dev-only
  status, checkpoint/tokenizer paths when applicable, and explicit production
  blockers for local test-double profiles.
- `security_audit_excludes.json` requires reason, owner, risk category, and
  explicit approved executable-sink classes before an excluded file may contain
  executable sink strings.
- `verifier_policy.json` defines default and production profiles, allowed
  command roots, timeouts, scanner selections, fail-closed behavior, and
  production-readiness flags.
- Invalid configs fail before execution: bad ratio sums, duplicate benchmark
  names, unprotected required benchmark paths, non-scratch model profiles,
  unsafe verifier shell shapes, and ungoverned audit excludes are rejected.

## Production Training Guardrails

The scratch pretraining loop now records production traceability metadata in
each checkpoint:

- optimizer state
- scheduler state
- model config
- training args
- tokenizer path and SHA-256 hash
- shard manifest path and SHA-256 hash
- git commit
- environment report
- distributed world size

Production mode:

```bash
python -m src.aeitron.model_ops.pretrain_loop \
  --production \
  --manifest artifacts/aeitron/shards/manifest.json \
  --output-dir artifacts/aeitron/production-pretrain \
  --device cuda \
  --model-profile 7b \
  --dtype bf16 \
  --validate-every 100 \
  --checkpoint-every 500 \
  --steps 10000
```

Production mode rejects:

- `model-profile=tiny` unless `--dev-smoke` is explicitly set
- missing shard manifest
- missing tokenizer asset
- validation schedules that never run
- disabled checkpointing
- non-finite or catastrophic loss
- incompatible checkpoint resume shape

The data pipeline also has production validation. When `--production` is used
through `deploy/gpu/run_real_data_training_pipeline.py`, it requires Postgres
frontier storage, non-local object storage, license filtering, benchmark
contamination filtering, near-dedup, source balancing, training-data gate, and
in-run validation.

## Native Scratch Serving

Module:

- `src/aeitron/model_ops/native_serving.py`

Purpose:

Serve a Aeitron-owned scratch checkpoint directly before vLLM/TensorRT
conversion exists. This is not a mock backend. It loads the checkpoint manifest,
`model.pt`, tokenizer, validates tokenizer/model compatibility, and exposes an
OpenAI-compatible chat endpoint.

Command:

```bash
python -m src.aeitron.model_ops.native_serving \
  --checkpoint-manifest artifacts/aeitron/train/checkpoint_manifest.json \
  --tokenizer-path artifacts/aeitron/tokenizer/tokenizer.json \
  --model-name aeitron-scratch \
  --device cuda \
  --host 0.0.0.0 \
  --port 8001
```

Endpoints:

- `GET /health/live`
- `GET /health/ready`
- `GET /v1/models`
- `POST /v1/chat/completions`

The endpoint supports normal JSON responses and SSE streaming. Auth, quota, and
observability middleware are installed by default. Use `--no-auth` and
`--no-quota` only for local validation.

## DeepSpeed Runtime Status

DeepSpeed ZeRO-2/ZeRO-3 is now wired as a real runtime path in the scratch
pretraining loop. When `--distributed-strategy deepspeed_zero2` or
`deepspeed_zero3` is selected, the loop:

- imports DeepSpeed and fails immediately if it is not installed
- initializes distributed state through DeepSpeed
- loads and patches the ZeRO JSON config with real batch sizes
- uses DeepSpeed engine `backward()` and `step()`
- writes DeepSpeed engine checkpoint folders alongside the native checkpoint

Example:

```bash
deepspeed --num_nodes 1 --num_gpus 8 -m src.aeitron.model_ops.pretrain_loop \
  --distributed-strategy deepspeed_zero3 \
  --deepspeed-config deploy/gpu/deepspeed_zero3.json \
  --manifest artifacts/aeitron/shards/manifest.json \
  --output-dir artifacts/aeitron/ds-zero3-run \
  --device cuda \
  --model-profile 7b \
  --dtype bf16 \
  --sequence-length 2048 \
  --batch-size 1 \
  --gradient-accumulation-steps 16 \
  --steps 10000 \
  --production
```

This path is built, but still not cluster-proven until an actual multi-GPU
DeepSpeed run saves, reloads, and evaluates a checkpoint. Megatron-LM remains an
external-checkout requirement until a real Megatron adapter is implemented and
cluster-tested.

## vLLM / TensorRT-LLM / Megatron Adapters

Module:

- `src/aeitron/model_ops/production_adapters.py`

Purpose:

Bridge native Aeitron scratch checkpoints into production runtime ecosystems
without pretending that external GPU runtimes have been validated locally.

HF/vLLM export:

```bash
python -m src.aeitron.model_ops.production_adapters export-hf \
  --checkpoint-manifest artifacts/aeitron/train/checkpoint_manifest.json \
  --tokenizer-path artifacts/aeitron/tokenizer/tokenizer.json \
  --output-dir artifacts/aeitron/exports/hf-llama \
  --torch-dtype bfloat16
```

This writes:

- `config.json`
- `model.safetensors`
- `tokenizer.json`
- `tokenizer_config.json`
- `special_tokens_map.json`
- `aeitron_conversion_manifest.json`

Validate vLLM prerequisites:

```bash
python -m src.aeitron.model_ops.production_adapters validate-vllm \
  --hf-model-dir artifacts/aeitron/exports/hf-llama
```

TensorRT-LLM build plan:

```bash
python -m src.aeitron.model_ops.production_adapters plan-tensorrt \
  --hf-model-dir artifacts/aeitron/exports/hf-llama \
  --output-dir artifacts/aeitron/exports/tensorrt \
  --dtype bfloat16
```

Megatron launch plan:

```bash
python -m src.aeitron.model_ops.production_adapters plan-megatron \
  --manifest artifacts/aeitron/shards/manifest.json \
  --tokenizer-path artifacts/aeitron/tokenizer/tokenizer.json \
  --output-dir artifacts/aeitron/megatron \
  --model-profile 7b \
  --tensor-parallel 2 \
  --pipeline-parallel 2 \
  --data-parallel 4 \
  --sequence-length 2048 \
  --micro-batch-size 1 \
  --global-batch-size 16 \
  --train-iters 10000 \
  --megatron-root /opt/Megatron-LM
```

Promotion rule:

- HF export existing is not enough.
- vLLM must load and decode the exported package.
- TensorRT-LLM must build an engine and pass decode parity.
- Megatron must run on a real cluster, save a checkpoint, reload, and evaluate.

## Production Benchmark Pack

The benchmark pack now has production minimum-count checks. A tiny local JSONL
file can still validate adapter shape in dev mode, but cannot pass production
coverage.

Public coding benchmarks can be materialized locally:

```bash
python -m src.aeitron.evaluation.benchmark_pack \
  --materialize-public \
  --target-dir data/eval
```

This fetches OpenAI HumanEval and Google Research MBPP from their public
repositories and writes:

- `data/eval/humaneval.jsonl`
- `data/eval/mbpp.jsonl`
- `data/eval/benchmark_materialization_report.json`

SWE-Bench and CyberSecEval remain governed local-file inputs. They are not
silently downloaded into training or evaluation because the real runners,
licenses, and holdout rules must be handled explicitly.

```bash
python -m src.aeitron.evaluation.benchmark_pack \
  --production \
  --human-eval data/eval/humaneval.jsonl \
  --mbpp data/eval/mbpp.jsonl \
  --swe-bench data/eval/swe_bench_style.jsonl \
  --cyberseceval data/eval/cyberseceval_style.jsonl \
  --custom-security data/eval/aeitron_security.jsonl \
  --output-dir artifacts/aeitron/benchmark-pack
```

Default production minimums:

- HumanEval: 164 tasks
- MBPP: 374 tasks
- SWE-style suite: at least 1 local governed task file
- CyberSecEval-style suite: at least 1 local governed task file

## Scratch Learning Validation Layer

Purpose:

- detect whether a scratch checkpoint actually learns instead of merely running
- catch tokenizer collapse such as dot/newline/space dominance
- prove the optimizer/model/data path can overfit a controlled high-signal corpus
- replace the 5-prompt smoke comparison with a 50-200 prompt coding/security suite
- provide a non-tiny T4 validation profile before expensive 7B+ cluster runs

Main module:

- `src/aeitron/model_ops/learning_validation.py`

What it builds:

- `InstructionRecord` schema with `prompt`, `context`, `answer`, `code_patch`, `tests`, and `verification`
- deterministic controlled instruction corpus for defensive security, agentic coding, debugging, patch generation, and repository reasoning
- tokenizer dominance report with total tokens, top tokens, dot fraction, quote fraction, whitespace fraction, newline fraction, unknown-token rate, single-character token rate, special-token checks, sample efficiency, and code/security pattern coverage
- tokenizer audit Markdown report for quick human inspection
- overfit sanity report with first/final/best loss and required relative loss drop
- expanded checkpoint comparison suite compatible with `run_checkpoint_comparison.py`
- T4 validation command using the real scratch profile `t4_validation`

Run controlled validation:

```bash
python -m src.aeitron.model_ops.learning_validation \
  --output-dir artifacts/aeitron/learning-validation-v1 \
  --instruction-count 200 \
  --overfit-steps 300 \
  --device cuda \
  --dtype fp16
```

Fast local check:

```bash
python -m src.aeitron.model_ops.learning_validation \
  --output-dir artifacts/aeitron/learning-validation-smoke \
  --instruction-count 50 \
  --skip-overfit \
  --device cpu
```

Interpretation:

- if tokenizer audit fails, fix tokenizer/data before training longer
- if overfit sanity fails, do not spend serious GPU time yet
- if overfit passes but real-data eval still emits repetitive punctuation, improve instruction-style data mix and generation settings
- if expanded eval remains near zero, the model is still too small or undertrained for reasoning quality
- if checkpoint comparison returns `failed_generation_collapse`, the model is repeating patterns above the allowed threshold and must not be promoted

T4 validation profile:

- model profile: `t4_validation`
- hidden size: 512
- layers: 8
- sequence length target: 256 for Kaggle run, max model context 2048
- gradient checkpointing: enabled
- intended run: 1k-10k steps on Kaggle/Colab T4/L4/A100 after overfit sanity passes

Expanded comparison command:

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

The deterministic comparison path now supports:

- `repetition_penalty`
- `no_repeat_ngram_size`
- stop tokens
- generation collapse detection
- fail-fast comparison status when repetitive output exceeds the threshold

The real-data pipeline writes tokenizer audit artifacts at:

- `reports/tokenizer_audit_report.json`
- `reports/tokenizer_audit_report.md`

## Curriculum-First Scratch Training

Purpose:

- reduce hallucination and generation collapse by training one capability band
  at a time
- start with stable code/security language before mixing agentic workflows
- keep offensive-security material out of the defensive phase

Supported curriculum modes:

- `fundamentals_only`
- `defensive_security_only`
- `debug_patch_only`
- `agentic_coding_only`
- `balanced`

Implementation:

- `src/aeitron/learning/mixer.py`
- `src/aeitron/model_ops/learning_validation.py`
- `src/aeitron/model_ops/checkpoint_compare.py`

Defensive-only data rules:

- keep only defensive security/coding instruction rows
- reject offensive misuse rows containing patterns such as reverse shells,
  shellcode, credential dumping, exfiltration, C2 callbacks, exploit payload
  instructions, or EDR/AV bypass wording
- convert accepted rows into prompt/context/answer/patch/tests/verification
  training text

Defensive hallucination checks:

- if evidence is missing, output must say it cannot confirm or needs more context
- generated CVE IDs are forbidden unless the prompt already includes that CVE
- claims such as "tests passed" are forbidden unless verification evidence is
  present in the prompt
- exploit steps are forbidden in defensive eval

Generate defensive-only validation assets:

```bash
python -m src.aeitron.model_ops.learning_validation \
  --output-dir artifacts/aeitron/defensive-learning-validation-v1 \
  --instruction-count 100 \
  --curriculum-mode defensive_security_only \
  --overfit-steps 300 \
  --device cuda \
  --dtype fp16
```

This writes:

- `instruction_corpus.jsonl`
- `expanded_eval_suite.jsonl` with 100 defensive prompts
- `tokenizer_dominance_report.json`
- `tokenizer_dominance_report.md`
- `learning_validation_report.json`
- staged commands for small overfit, 1k Kaggle validation, and 10k Kaggle
  validation

Kaggle defensive 1k validation:

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

Kaggle defensive 10k validation:

```bash
PYTHONUNBUFFERED=1 python -u deploy/gpu/run_real_data_training_pipeline.py \
  --sources config/data_sources.ultimate.json \
  --work-dir artifacts/aeitron/defensive-validation-10k-v1 \
  --kaggle-validation \
  --curriculum-mode defensive_security_only \
  --model-profile t4_validation \
  --checkpoint-compare-prompt-suite artifacts/aeitron/defensive-learning-validation-v1/expanded_eval_suite.jsonl \
  --checkpoint-compare-repetition-penalty 1.18 \
  --checkpoint-compare-no-repeat-ngram-size 4 \
  --checkpoint-compare-max-repetition-ratio 0.72 \
  --steps 10000 \
  --sequence-length 256 \
  --batch-size 1 \
  --gradient-accumulation-steps 8 \
  --validation-interval 250 \
  --validation-batches 8 \
  --early-stopping-patience 12 \
  --gradient-checkpointing \
  --progress-to-stdout
```

Promotion rule:

- small overfit sanity must pass before spending long GPU time
- 1k defensive validation must pass before 10k
- 10k checkpoint must not regress, collapse, invent CVEs, claim unverified test
  success, or produce exploit steps

## Strict Scanner Bootstrap

Security audit reports now include a scanner install plan. For local Windows
setup:

```powershell
python -m pip install --upgrade bandit semgrep pip-audit
winget install --id GitHub.CodeQL
codeql database create artifacts/aeitron/codeql-db --language=python --source-root=.
python -m src.aeitron.security.audit --strict-external-tools --output-dir artifacts\\aeitron\security-audit
```

Strict mode fails when required scanner tools are missing or scanner findings
fail policy.

## Security Audit Production Behavior

Module:

- `src/aeitron/security/audit.py`

Dev behavior:

- missing Bandit/Semgrep/CodeQL/pip-audit is reported as `skipped`
- skipped optional tools do not fail local dev release gates

Production behavior:

```powershell
python -m src.aeitron.security.audit --strict-external-tools --output-dir artifacts\\aeitron\security-audit
```

In strict mode, missing required scanner CLIs become release blockers. Critical
findings, dependency warnings, failed scanner output, and failed Kubernetes
validation block release.

## Verification Commands

Use these after major changes:

```powershell
python -m compileall -q src\aeitron tests deploy\gpu
python -m unittest tests.test_aeitron_data_engine tests.test_aeitron_production_hardening tests.test_aeitron_training_control tests.test_aeitron_enterprise_readiness
python -m src.aeitron.deployment.k8s_validate --output-dir artifacts\\aeitron\k8s-validation
python -m src.aeitron.learning.storage --uri local://artifacts/aeitron/object-store --work-dir artifacts\\aeitron\object-store-lifecycle
python -m src.aeitron.security.audit --no-bandit --output-dir artifacts\\aeitron\security-audit
python -m src.aeitron.evaluation.release_gate
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\run_aeitron_consolidated_smoke.ps1
```

## Training Workspace And Scale Control Plane

### Purpose

The Training Workspace is the operational control plane between a researcher
and scratch pretraining compute. It solves three separate problems without
mixing their responsibilities:

1. The client submits a validated training profile and follows progress.
2. The control plane owns identity, durable state, scheduling, audit, and
   artifact promotion.
3. Trusted workers own data processing, model computation, checkpoint writing,
   and measured evaluation.

Kaggle and Colab are validation clients. They are not represented as 7B-60B
production training infrastructure. Large synchronous pretraining belongs on a
trusted Kubernetes or Slurm cluster with pinned containers and private service
connectivity.

No external model weights or post-training adaptation path is introduced. The
workspace accepts Aeitron scratch pretraining, evaluation, and checkpoint
lifecycle jobs only.

### Canonical Components

- `src/aeitron/training_workspace.py`: schemas, state machine, Postgres and
  in-memory stores, Redis event bus, scheduler adapters, controller, event
  archiver, checkpoint/evaluation promotion, and readiness.
- `src/aeitron/training_client.py`: async SDK, short-lived token refresh, SSE
  replay, and CLI.
- `src/aeitron/shared/progress.py`: non-blocking worker event batching,
  heartbeat, coalescing, local WAL, retry, and WAL replay.
- `config/training_profiles.json`: append-only versioned profile contracts.
- `deploy/gpu/run_workspace_validation.py`: direct-kernel notebook validation
  launcher.
- `apps/training-workspace`: React and TypeScript workspace UI.
- `src/aeitron/db/migrations/0004_training_workspace.sql`: durable control
  plane schema.
- `deploy/k8s/training-workspace.yaml`: controller identity, RBAC, network
  policy, and deployment.
- `deploy/k8s/training-workspace-ui.yaml`: hardened workspace UI deployment.
- `deploy/k8s/training-monitoring.yaml`: DCGM GPU telemetry.

### Request Flow

```text
SDK / CLI / Web UI
    -> bootstrap exchange
    -> 15-minute scoped access token + <=12-hour refresh session
    -> immutable profile resolution
    -> idempotent job creation
    -> Postgres state transition
    -> controller validation
    -> scheduler submission
    -> trusted worker
    -> Redis live events + local WAL fallback
    -> S3/MinIO durable artifacts
    -> checkpoint reload proof
    -> evaluation gate
    -> audited checkpoint promotion
```

The notebook receives only `AEITRON_WORKSPACE_URL` and
`AEITRON_BOOTSTRAP_TOKEN`. It never receives Postgres, Redis, Qdrant, or
object-store administration credentials. Cluster workers receive a job-bound
token through a mounted Secret or a mode-0600 Slurm token file. The controller
rotates worker credentials before expiry.

### Immutable Profiles

The registry currently includes:

- `defensive-1k`: direct notebook validation.
- `defensive-10k`: serious single-GPU Kubernetes validation.
- `fundamentals-validation`: coding-fundamentals notebook validation.
- `aeitron-1b-single-node`: single-node cluster proof.
- `aeitron-7b-fsdp`: multi-node native FSDP.
- `aeitron-32b-zero3`: multi-node DeepSpeed ZeRO-3.
- `aeitron-60b-hybrid`: Slurm and hybrid/Megatron target.

A profile version is immutable. Postgres uses `(profile_id, version)` as the
identity and stores its SHA-256. Synchronization fails if content changes
without a version bump. A job stores the exact profile hash, spec hash, Git
commit, image digest, tokenizer hash, and dataset manifest hash.

Allowed overrides have explicit integer ranges. A client cannot submit an
executable, shell fragment, environment secret, mount, image, or scheduler
argument. `build_training_command()` creates argv from validated schema fields
only.

Production mode takes the Git commit and image digest from trusted server
configuration:

```text
AEITRON_TRAINING_GIT_COMMIT
AEITRON_TRAINING_IMAGE_DIGEST
```

Zero/placeholder values block submission. Images are scheduled by digest, not
mutable tag.

### Job State Machine

```text
validating -> queued -> provisioning -> running
running -> checkpointing -> running
running/checkpointing -> evaluating -> running/succeeded

terminal: succeeded | failed | blocked | cancelled
resumable: infrastructure failed or cancelled -> queued
not resumable: blocked
```

`blocked` is used for data, tokenizer, non-finite-loss, quality, compatibility,
or security failures. Automatic resume is deliberately unavailable for these
failures. `failed` is reserved for infrastructure/runtime failure. Resume
requires a promoted checksum-verified checkpoint.

Every transition uses optimistic version checks. Concurrent writers cannot
silently overwrite state. Attempt state, start time, and finish time follow the
job state. Every create, scheduler submit/failure, transition, cancel, resume,
checkpoint, evaluation, and promotion event is written to the audit table.

### Postgres Schema

- `training_profiles`: immutable versioned profile JSON and hash.
- `training_jobs`: canonical spec, optimistic version, state, active attempt,
  scheduler binding, failure classification, and event cursors.
- `training_attempts`: scheduler attempt and resume checkpoint.
- `training_event_ingress`: `(attempt, rank, source_sequence)` deduplication.
- `training_artifacts`: verified object URI, size, SHA-256, kind, and promotion
  status.
- `checkpoint_versions`: step, immutable hashes, topology, metrics, reload
  proof, and promotion state.
- `evaluation_runs`: checkpoint decision and verified report.
- `service_accounts`: scoped identity and bootstrap token hash.
- `service_account_sessions`: expiring/revocable refresh sessions.
- `training_audit_events`: actor, action, outcome, request, and redacted
  metadata.

Migrations run through the existing migration runner or Alembic chain. The
database is the source of truth. Redis is never used as durable job state.

### Live Event Protocol

Workers emit versioned events with job, attempt, source sequence, rank,
world-size, node, stage, status, step, loss, validation loss, tokens/second,
and GPU memory.

Delivery behavior:

- up to 25 events or one second per batch;
- five-second heartbeat even when the trainer emits nothing;
- bounded in-memory queue;
- metric updates coalesce to their latest value under pressure;
- error, checkpoint, and evaluation events are persisted to WAL rather than
  dropped;
- failed HTTP delivery writes fsync-backed JSONL WAL;
- exponential retry uses jitter;
- restart-safe source sequences and transactional server deduplication;
- WAL replay is safe after an ambiguous network response;
- rank zero emits global metrics; non-zero ranks retain heartbeat, checkpoint,
  and fatal diagnostics.

The SSE endpoint accepts `Last-Event-ID`. SDK and web clients reconnect from
the last acknowledged sequence. The API sends `X-Accel-Buffering: no`; the UI
Nginx proxy disables response buffering. Redis holds the hot stream while the
archiver writes ordered gzip JSONL chunks to object storage.

### Artifact And Checkpoint Lifecycle

Workers request short-lived S3/MinIO presigned PUT URLs. The request binds the
object path, content type, size declaration, and SHA-256 metadata. The notebook
never sees permanent object-store credentials.

Promotion sequence:

```text
write shards and temporary checkpoint prefix
  -> upload manifest through a presigned request
  -> server HEAD size/SHA verification
  -> checkpoint commit
  -> dataset/tokenizer hash comparison with job spec
  -> reload smoke proof
  -> verified evaluation artifact commit
  -> pass/release decision
  -> admin promotion
  -> atomic jobs/{job}/checkpoints/latest.json update
```

Validation-only notebook checkpoints cannot become release checkpoints. A
checkpoint cannot promote without both `reload_verified=true` and a committed
passing evaluation. Promotion is an audited `training:admin` operation.

### Scheduler Safety

`NotebookValidationAdapter` supports one node and no distributed strategy. It
is explicitly validation-only.

`KubernetesSchedulerAdapter` creates a structured `batch/v1 Job`. Before
submission it checks the Kubernetes client, node readiness, GPU count, GPU
memory label, and RDMA label. Nodes must be labeled:

```bash
kubectl label node GPU_NODE aeitron.ai/gpu-memory-gib=80
kubectl label node GPU_NODE aeitron.ai/rdma=true
```

`KubernetesPyTorchAdapter` additionally requires the Kubeflow Training Operator
and its `PyTorchJob` CRD. Multi-node profiles fail before provisioning when the
operator or sufficient eligible nodes are absent.

Worker pods use:

- pinned image digest;
- non-root UID/GID 10001;
- runtime-default seccomp;
- no privilege escalation;
- all Linux capabilities dropped;
- read-only root filesystem;
- bounded memory-backed `/tmp`;
- node-local `/workspace` cache;
- profile-owned Secret references only;
- job-scoped token mounted read-only;
- explicit CPU, memory, and GPU requests/limits.

`SlurmSchedulerAdapter` uses argv-only `sbatch`, `sacct`, `sinfo`, and
`scancel`. Generated scripts use `set -euo pipefail`; no `eval` exists. The
preflight requires enough nodes with the requested GPU GRES and features
`gpu-memory-<N>g,rdma`. Slurm production workers require an HTTPS workspace
URL.

### Identity And Authorization

Roles are represented by scopes:

- `training:jobs:create`
- `training:jobs:read`
- `training:jobs:cancel`
- `training:events:write`
- `training:artifacts:write`
- `training:artifacts:read`
- `training:admin`

Generic API scope does not grant training scope. Non-admin users see only jobs
they own. Worker tokens are bound to a single `job_id` claim. Event payloads
are limited to 64 KiB, batches to 100, and known secret keys/values are
redacted before Redis, audit, or object storage.

The initial environment bootstrap credential is materialized as a reserved
hashed service account before a refresh session is issued. Rotation updates
the hash and revokes its existing refresh sessions. Explicit logout/revocation
is supported. Production ingress must use HTTPS.

### APIs

```text
POST /v1/training/token/exchange
POST /v1/training/token/refresh
POST /v1/training/token/revoke
GET  /v1/training/profiles
POST /v1/training/jobs
GET  /v1/training/jobs
GET  /v1/training/jobs/{job_id}
POST /v1/training/jobs/{job_id}/claim
POST /v1/training/jobs/{job_id}/cancel
POST /v1/training/jobs/{job_id}/resume
GET  /v1/training/jobs/{job_id}/events
POST /v1/training/jobs/{job_id}/events:batch
GET  /v1/training/jobs/{job_id}/artifacts
POST /v1/training/jobs/{job_id}/artifacts/presign
POST /v1/training/jobs/{job_id}/artifacts/register
GET/POST /v1/training/jobs/{job_id}/checkpoints
GET/POST /v1/training/jobs/{job_id}/evaluations
POST /v1/training/jobs/{job_id}/checkpoints/{checkpoint_id}/promote
GET  /v1/training/jobs/{job_id}/audit
```

### Notebook Operation

Place only these values in Kaggle/Colab Secrets:

```text
AEITRON_WORKSPACE_URL=https://workspace.example.com
AEITRON_BOOTSTRAP_TOKEN=<bootstrap-value>
```

Clone the repository once, then use direct-kernel execution:

```python
%run -i deploy/gpu/run_workspace_validation.py --profile defensive-1k
```

`%run -i` avoids a nested buffered shell process. The same kernel displays
each progress line immediately. If workspace secrets are absent, the launcher
runs standalone validation and labels that condition in output.

SDK:

```python
from aeitron_client import Workspace

workspace = Workspace.from_environment()
run = await workspace.train(profile="defensive-1k", follow=True)
```

CLI:

```bash
python -m pip install -e . --no-deps
aeitron train --profile defensive-1k --follow
aeitron jobs list
aeitron jobs inspect JOB_ID
aeitron jobs cancel JOB_ID
aeitron jobs resume JOB_ID
```

When the Python Scripts directory is not on `PATH`, use
`python -m src.aeitron.training_client` with the same arguments.

### Deployment

Local integration proof:

```bash
cp deploy/prod/.env.example deploy/prod/.env
docker compose --env-file deploy/prod/.env -f deploy/prod/docker-compose.yml --profile training up --build
```

The API is on port 8090 and UI on port 8088. Example/default secrets are
invalid for production and must be replaced.

Kubernetes:

```bash
kubectl apply -f deploy/k8s/postgres-redis.yaml
kubectl apply -f deploy/k8s/minio.yaml
kubectl apply -f deploy/k8s/api.yaml
kubectl apply -f deploy/k8s/training-workspace.yaml
kubectl apply -f deploy/k8s/training-workspace-ui.yaml
kubectl apply -f deploy/k8s/training-monitoring.yaml
```

Secret examples are templates, not deployable credentials. Use External
Secrets, Vault, cloud workload identity, or another approved secret manager in
the real cluster.

### Observability

Control-plane metrics include HTTP rate/latency, job submissions/transitions,
event ingestion and deduplication, training/validation loss, token throughput,
checkpoint commits/promotions, and evaluation decisions. Histogram storage is
constant-memory; the process does not retain every observation.

DCGM Exporter exposes GPU utilization, memory, and temperature. The Kubernetes
Service carries Prometheus scrape annotations. The Grafana production
dashboard includes jobs, loss, throughput, and GPU utilization.

Alerts still require the production Prometheus/Alertmanager policy to be
installed and tested. Missing heartbeat, non-finite loss, OOM, NCCL timeout,
data starvation, checkpoint failure, dependency outage, and evaluation
regression are the required alert classes.

### Honest Readiness

Locally tested code is not cluster proof. Status rules:

- notebook direct-kernel path: `validation_ready`;
- configured Postgres/Redis/S3 workspace: `production_ready_requires_external_service`;
- Kubernetes/PyTorchJob/Slurm profiles before live proof: `built_not_cluster_proven`;
- missing dependency or placeholder image/commit: `blocked_missing_dependency`.

The following cannot be honestly completed on this Windows workstation:

- 24-hour single-GPU soak;
- 10k-step T4/L4/A100 proof;
- two-GPU FSDP save/reload;
- multi-node node-loss recovery;
- one-million-event Redis interruption test;
- Postgres failover;
- MinIO checkpoint interruption;
- 7B, 32B, or 60B scratch pretraining.

The code paths and fail-fast gates exist, but these statuses remain
`built_not_cluster_proven` until the corresponding infrastructure test report
is produced.

### Production Training Policy Contract

`config/training_profiles.json` schema version 2 is the canonical, append-only
training policy registry. JSON `defaults` reduce duplication, but
`TrainingProfileRegistry.from_file()` deep-resolves every profile before
validation and hashing. The immutable hash therefore covers the complete
resolved policy rather than a reference to mutable defaults.

Every profile now binds all of the following:

- AdamW learning rate, beta values, epsilon, weight decay, and gradient clip;
- constant, linear, or cosine schedule with exactly one warmup mode;
- global batch sequences and non-padding target/maximum token budgets;
- checkpoint interval, retained latest/best counts, optimizer/scheduler/RNG
  state, checksum, and reload requirements;
- evaluation interval, suite, strictness, early stopping, and regression limit;
- hot/archive/delete retention and promoted-checkpoint preservation;
- promotion evidence, minimum step, and minimum evaluation count;
- retryable failure classes, exponential backoff, preemption handling, and
  maximum attempts;
- wall time, heartbeat timeout, and graceful termination;
- deterministic dataloader seed, bounded prefetch, workers, checksum policy,
  pinning, and node-local cache budget;
- OCI image repository plus required Python, PyTorch, and CUDA versions;
- namespace/partition, queue, account, storage class, priority, gang
  scheduling, and topology key;
- maximum GPU-hours, projected USD cost, storage, egress, and owner concurrency;
- promoted dataset and tokenizer URI/hash requirements.

Resolution recalculates global batch and target tokens whenever an authorized
step/node override changes topology. Production submissions reject unpromoted
datasets, disallowed URI schemes, mutable image identities, excessive
concurrency, GPU-hour requests above profile quota, and projected GPU cost
above profile quota. Projected cost is the maximum wall time multiplied by the
requested GPU count and the profile's audited per-GPU-hour estimate. These values are passed
to the real pretraining process. The runtime uses configured AdamW and
warmup/decay behavior, reports current learning rate, validates expected
runtime versions, prefetches shard batches, and fails if consumed tokens exceed
the immutable budget.

`steps` is deliberately defined as dataloader micro-batch steps in this
runtime. `global_batch_sequences = micro_batch * gradient_accumulation *
world_size`; the immutable token target is calculated from consumed
micro-batches, sequence length, and world size. Optimizer-update metrics remain
separate so changing gradient accumulation cannot silently change token
accounting.

### Gated Qualification Staircase

`config/training_qualification_campaigns.json` defines compact ranges rather
than dozens of copied profiles. `defensive-staircase-v1` expands to 37
milestones:

1. 1k, 2k, ..., 20k;
2. 30k, 40k, ..., 100k;
3. 200k, 300k, ..., 1M.

The first ten milestones use notebook validation. Later milestones require the
trusted Kubernetes scheduler. A create request must bind both campaign and
milestone and set the exact milestone step override. For every later
milestone, the service queries durable prior jobs and requires a succeeded
predecessor, reload-verified checkpoint, passing evaluation, <=3% validation
regression, and identical dataset/tokenizer hashes. User-supplied metadata
cannot bypass this gate.

Example:

```powershell
aeitron train --profile defensive-staircase-notebook --steps 1000 `
  --campaign defensive-staircase-v1 --milestone steps-0001000 `
  --dataset-manifest-uri file:///data/manifest.json `
  --dataset-manifest-sha256 <sha256> `
  --tokenizer-uri file:///data/tokenizer.json --tokenizer-sha256 <sha256>
```

This validates one model/config progressively. It does not mean that 1M steps
automatically qualifies a 1B or 60B model. Parameter-scale changes begin a new
checkpoint lineage because their tensors are incompatible.

### Measured Infrastructure Proofs

`src/aeitron/training_proofs.py` writes a machine-readable report where every
proof is `passed`, `failed`, or `blocked`. `deploy/proof/docker-compose.yml`
provides isolated pinned Postgres, Redis, and MinIO dependencies.

The local proof performs real migrations, stores a job and attempt in
Postgres, checks event sequence deduplication through Redis Streams, executes
MinIO upload/head/download/list/delete with SHA-256, kills a real Docker worker
and verifies node-loss retry/backoff, dumps/restores Postgres, restarts
Postgres/Redis/MinIO, and verifies persistence after restart.

```powershell
docker compose -p aeitron-proof -f deploy\proof\docker-compose.yml up -d
python -m src.aeitron.training_proofs `
  --output-dir artifacts\aeitron\production-proofs\local-docker
```

Full ordered-event and soak commands:

```powershell
python -m src.aeitron.training_proofs --event-count 1000000 `
  --skip-disaster-recovery --output-dir artifacts\aeitron\production-proofs\million-events
python -m src.aeitron.training_proofs --soak-seconds 86400 `
  --output-dir artifacts\aeitron\production-proofs\24-hour-soak
```

The local full stress proof executed on 2026-07-16 accepted exactly 1,000,000
ordered events, verified both final and tail sequence as 1,000,000, and measured
1,285.64 events/second with no failed proof. This proves the tested local
Postgres/Redis path; it is not a multi-region or cluster throughput claim. A
30-second soak harness smoke also completed 15 dependency health cycles. The
required 24-hour soak remains unproven until the exact 86,400-second command
finishes without interruption.

Kubernetes/PyTorchJob, Slurm, FSDP, ZeRO-3, Megatron, and 60B resume checks
probe their real dependencies. A missing cluster or GPU allocation is an
explicit blocker. Running a local mock does not promote those proof states.

### Workspace Verification

```powershell
python -m compileall -q src\aeitron tests deploy\gpu
python -m unittest tests.test_aeitron_training_workspace
python -m src.aeitron.training_workspace profiles
python -m src.aeitron.training_workspace readiness
python -m src.aeitron.evaluation.release_gate
python -m src.aeitron.security.audit --output-dir artifacts\aeitron\security-audit
python -m src.aeitron.deployment.k8s_validate --output-dir artifacts\aeitron\k8s-validation
```

Frontend:

```powershell
cd apps\training-workspace
npm ci
npm run build
```

## Final Rule

Do not reintroduce numbered legacy folders. If a feature is needed, add it to
the correct final module under `src/aeitron` and update this manual with enough
detail that the system can be understood without reading all source code.



