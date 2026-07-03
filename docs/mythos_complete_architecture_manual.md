# Mythos Final Architecture Manual

This is the single source of truth for Mythos after the architecture cleanup.
The old numbered architecture has been removed. New work must live under
`src/mythos` and use the final module names below.

## Final Module Set

1. Gateway Layer: FastAPI app, auth, project/session/run APIs.
2. Identity & Access Layer: JWT auth middleware and user boundary.
3. Model Foundation & Serving Layer: scratch architecture contracts plus Mythos-owned checkpoint serving adapters.
4. Repository Intelligence Layer: repository indexing, chunking, symbol extraction.
5. Context Builder: ranked context packs from indexed code.
6. TaskGraph Runtime: durable task graph and run records.
7. Agent Runtime: single-agent MVP loop over planner, context, tools, patches, verifier.
8. Tool Runtime: bounded local command/test execution.
9. Patch Manager: preview, apply, rollback, and persisted patch records.
10. Verification Runtime: tests, basic secret checks, accept/reject verdicts.
11. Unified Memory Layer: verified fix retrieval and promotion API.
12. Guardrails Layer: critic/security review contracts.
13. Evaluation Service: native release/smoke evaluation.
14. Learning Pipeline: verified candidate validation/export hooks.
15. Observability Layer: health, structured responses, and future metrics hooks.
16. Production Operations Layer: Postgres migrations, CI, deployment manifests, quota, and GPU validation commands.

No legacy numbered modules are part of the final architecture.

## Current MVP Capabilities

- Create projects from a local repository path.
- Create sessions.
- Index repository files.
- Extract Python symbols and general code chunks.
- Extract Python AST signatures, imports, calls, dependencies, decorators, docstrings, and state mutations.
- Extract common import/dependency hints for JavaScript, TypeScript, Go, Rust, Java, C/C++, and Bash.
- Build ranked context packs.
- Run local vector search across indexed code chunks.
- Create durable agent runs.
- Persist six-node TaskGraphs:
  `understand -> retrieve_context -> edit -> test -> verify -> summarize`.
- Advance TaskGraphs node-by-node with dependency-aware queued/running/completed/failed states.
- Execute bounded local test/tool commands.
- Preview patches.
- Apply patches.
- Roll back patches.
- Run preview/apply/verify/rollback patch verification loops.
- Verify patches with command checks and basic secret scanning.
- Run optional defensive Semgrep and CodeQL verifier hooks when their CLIs are installed.
- Enforce auth/quota middleware on protected API routes when enabled.
- Gate token issuance in production with `MYTHOS_ALLOW_TOKEN_ISSUE` and `MYTHOS_TOKEN_ISSUE_KEY`.
- Use Redis-backed atomic quota when `MYTHOS_REDIS_URL` is configured, with local fallback only for development.
- Expose Prometheus-style `/metrics` and structured JSON request logs.
- Run hardened Docker sandbox executions when Docker is available.
- Run built-in security benchmark harness.
- Plan scratch pretraining specs for Mythos 7B, 32B, 70B, and 100B-class decoder models.
- Validate tokenizer/data/checkpoint readiness before any scratch training run.
- Run a real PyTorch scratch-decoder GPU smoke test with synthetic tokens.
- Write smoke checkpoints and checkpoint manifests for GPU validation.
- Run native MVP smoke tests.

## Source Layout

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
```

## Main Commands

Run the native MVP checks:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\run_mythos_mvp_foundation.ps1
```

Run the CLI:

```powershell
python -m src.mythos.cli --prompt "fix auth bug" --workspace . --agent-backend-mode mock --no-verifier --no-security
```

Start the API:

```powershell
python -m uvicorn src.mythos.gateway.api:app --host 127.0.0.1 --port 8090
```

## API Surface

- `GET /health/ready`
- `GET /v1/auth/status`
- `POST /v1/auth/token`
- `GET /v1/model/profiles`
- `POST /v1/evaluation/security-static`
- `GET /v1/model/foundation/status`
- `POST /v1/model/foundation/pretraining/readiness`
- `POST /v1/projects`
- `GET /v1/projects/{project_id}`
- `POST /v1/sessions`
- `POST /v1/projects/{project_id}/index`
- `GET /v1/projects/{project_id}/index/status`
- `GET /v1/projects/{project_id}/symbols`
- `POST /v1/context/build`
- `POST /v1/context/vector-search`
- `POST /v1/agent/runs`
- `GET /v1/agent/runs/{run_id}`
- `GET /v1/taskgraphs/{task_graph_id}`
- `POST /v1/taskgraphs/{task_graph_id}/advance`
- `POST /v1/tasks/{task_id}/complete`
- `POST /v1/tasks/{task_id}/fail`
- `POST /v1/tools/execute`
- `POST /v1/sandbox/run`
- `POST /v1/patches/preview`
- `POST /v1/patches/verify`
- `POST /v1/patches/{patch_id}/apply`
- `POST /v1/patches/{patch_id}/rollback`
- `POST /v1/verifier/run`

## Model Foundation & Serving

Mythos is scratch-only. Borrowed-model training and borrowed-model quality
baselines are not part of the architecture. The `mock` backend exists only as a
test double for plumbing checks.

Scratch foundation contracts:

- decoder-only architecture presets: `mythos-7b`, `mythos-32b`, `mythos-70b`, `mythos-100b`
- executable PyTorch decoder family for scratch training and GPU smoke tests
- tokenizer contract with code/reasoning special tokens
- data manifest readiness contract
- contamination/license/PII policy gates
- checkpoint manifest with file hashes

GPU smoke command:

```bash
pip install -r requirements-linux-gpu.txt
python deploy/gpu/run_scratch_gpu_smoke.py --device cuda --steps 2 --sequence-length 64
python -m src.mythos.model_ops.pretrain_loop --device cuda --steps 10 --sequence-length 64
```

## Production Operations

- Postgres migration runner: `python -m src.mythos.db.migration_runner`
- CI: `.github/workflows/ci.yml`
- Docker image: `Dockerfile`
- Prod compose: `deploy/prod/docker-compose.yml`
- Kubernetes manifests: `deploy/k8s/`
- Metrics endpoint: `GET /metrics`

Production auth/quota environment:

```bash
MYTHOS_AUTH_ENABLED=1
MYTHOS_JWT_SECRET=<long-random-secret>
MYTHOS_ALLOW_TOKEN_ISSUE=0
MYTHOS_TOKEN_ISSUE_KEY=<only-if-token-issue-enabled>
MYTHOS_QUOTA_ENABLED=1
MYTHOS_REDIS_URL=redis://redis:6379/0
```

Serving adapters:

Supported adapters:

- `mock`: local plumbing test double only.
- `mythos_serving`: private endpoint serving a Mythos-owned scratch checkpoint.

Default real target:

- `mythos-scratch`
- Endpoint env: `MYTHOS_MODEL_ENDPOINT`
- Model env: `MYTHOS_MODEL_NAME`

## Database

Local development:

- SQLite file: `artifacts/mythos/mythos.sqlite3`

Production schema:

- `src/mythos/db/schema.sql`

Main tables:

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

## Patch Acceptance Standard

A patch is acceptable only when:

- It is previewed before apply.
- It stays inside the project root.
- It does not write inside `.git`.
- It applies cleanly.
- Configured commands pass.
- Secret scan has no findings.

## Final Rule

Do not reintroduce numbered legacy folders. If a feature is needed, add it to
the correct final module under `src/mythos`.
