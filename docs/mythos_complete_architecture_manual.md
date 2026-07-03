# Mythos Final Architecture Manual

This is the single source of truth for Mythos after the architecture cleanup.
The old numbered architecture has been removed. New work must live under
`src/mythos` and use the final module names below.

## Final Module Set

1. Gateway Layer: FastAPI app, auth, project/session/run APIs.
2. Identity & Access Layer: JWT auth middleware and user boundary.
3. Model Serving Layer: model-agnostic adapters for mock and OpenAI/vLLM-compatible models.
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

No legacy numbered modules are part of the final architecture.

## Current MVP Capabilities

- Create projects from a local repository path.
- Create sessions.
- Index repository files.
- Extract Python symbols and general code chunks.
- Extract Python AST signatures, imports, calls, dependencies, decorators, docstrings, and state mutations.
- Extract common import/dependency hints for JavaScript, TypeScript, Go, Rust, Java, C/C++, and Bash.
- Build ranked context packs.
- Create durable agent runs.
- Persist six-node TaskGraphs:
  `understand -> retrieve_context -> edit -> test -> verify -> summarize`.
- Execute bounded local test/tool commands.
- Preview patches.
- Apply patches.
- Roll back patches.
- Verify patches with command checks and basic secret scanning.
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
- `POST /v1/projects`
- `GET /v1/projects/{project_id}`
- `POST /v1/sessions`
- `POST /v1/projects/{project_id}/index`
- `GET /v1/projects/{project_id}/index/status`
- `GET /v1/projects/{project_id}/symbols`
- `POST /v1/context/build`
- `POST /v1/agent/runs`
- `GET /v1/agent/runs/{run_id}`
- `GET /v1/taskgraphs/{task_graph_id}`
- `POST /v1/tools/execute`
- `POST /v1/patches/preview`
- `POST /v1/patches/{patch_id}/apply`
- `POST /v1/patches/{patch_id}/rollback`
- `POST /v1/verifier/run`

## Model Serving

Supported adapters:

- `mock`: local plumbing backend.
- `openai_compatible`: vLLM/OpenAI-compatible endpoint.

Default real target:

- `Qwen/Qwen2.5-Coder-7B-Instruct`
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
