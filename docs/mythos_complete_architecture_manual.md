# Mythos Complete Architecture Manual

This is the single source-of-truth manual for the Mythos/Titan AI architecture repository. It consolidates the target architecture, Mythos V1 productization notes, Phase 1-51 implementation manual, runtime guides, model/backend notes, training/evaluation plans, and production hardening blueprints into one file.

Generated locally from the previous `docs/*.md` set on 2026-07-02. The older split docs were consolidated into this one manual and removed to keep the repository clean. Source file names below are historical labels so you can still see where each section came from.

## Current Ground Truth

- Repository: `AI_Architecture_Build`
- Remote: `https://github.com/mah5472651/mythos-agentic-ai.git`
- Production-facing package: `src/mythos`
- Legacy phase packages: `src/phase1` through `src/phase51` remain as implementation sources and adapters until each capability is fully migrated.
- Latest stable local backend profile: `tiny-llama-cpu-smoke`
- Stable local model: `hf-internal-testing/tiny-random-LlamaForCausalLM`
- Stable local model revision: `9fb191250dd56d0ba7ec9785a025ed29c03d5998`
- Purpose of tiny backend: real OpenAI-compatible endpoint plumbing, scorecard plumbing, API routing, and release-gate checks.
- Quality boundary: tiny backend is not a reasoning-quality model; Phase 18 correctly marks it `needs_improvement`.
- Qwen CPU note: `qwen-cpu-smoke` remains a target local profile, but this Windows CPU Torch stack can crash natively while loading the 0.5B Qwen checkpoint.
- Future quality target: Qwen/DeepSeek/Llama coder-class 7B, 14B, 32B, then 50B-100B+ on Linux CUDA/vLLM infrastructure.

## Consolidated Runtime Status

The current executable product surface is the consolidated `src/mythos` package. It exposes the 12-module architecture as stable imports while delegating mature functionality to the older phase implementations. New code should depend on `src/mythos/*`, not directly on `src/phase*/*`, unless it is actively migrating a legacy capability.

Implemented consolidated modules:

- `src/mythos/gateway`: FastAPI-facing gateway shell for runtime, health, model profiles, evaluation, and learning hooks.
- `src/mythos/planning`: Intent expansion and planner facade over the meta-planner and intent-expansion phases.
- `src/mythos/runtime`: Single run entrypoint that coordinates planning, model backend selection, and the integrated agent runtime.
- `src/mythos/agents`: Worker-pool/router facade over the parallel-agent phase.
- `src/mythos/tools`: Tool execution facade for sandbox, shell-safe execution, Semgrep, CodeQL, and Git/browser style tool wrappers.
- `src/mythos/context`: Workspace index and context-builder facade over long-context packing and call-graph tooling.
- `src/mythos/memory`: Unified memory facade over vector, hierarchical, experience, and strict memory layers.
- `src/mythos/guardrails`: Critic, verifier, policy, security, and strict-stability facade.
- `src/mythos/patches`: Patch preview/apply/rollback facade.
- `src/mythos/evaluation`: Scorecard, release gate, quality loop, and regression gate facade.
- `src/mythos/learning`: Dataset gate, data flywheel, and checkpoint rollback facade.
- `src/mythos/model_ops`: Backend profile, active backend, and runtime health facade.

Consolidated smoke test:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\run_mythos_consolidated_smoke.ps1
```

Equivalent direct command:

```powershell
python -m src.mythos.cli --prompt "build secure login api" --workspace . --policy-mode development --agent-backend-mode mock --max-agent-nodes 2 --no-verifier --no-security
```

Expected current behavior:

- Runs end-to-end through the consolidated runtime facade.
- Uses mock agent reasoning by default so the architecture can be tested without GPU hardware.
- Produces `artifacts/mythos/consolidated-smoke.json`.
- Confirms control-plane plumbing, not final model quality.
- Gateway auth/quota is wired through Phase 34 middleware. Local default is disabled; production should set `PHASE34_AUTH_ENABLED=1`, `PHASE34_JWT_SECRET`, and optionally `PHASE34_QUOTA_ENABLED=1`.

Important boundary:

- This is Phase A architecture consolidation, not a full deletion migration. Old phase modules are intentionally still present as legacy adapters. Physical deletion should happen only after the equivalent `src/mythos` module has tests, docs, and release-gate coverage.

## MVP Implementation Plan: 8-Week Coding Agent

This section is the concrete build plan for the MVP only. The target is a working AI coding agent capable of repository understanding, code editing, testing, and verification in 8 weeks.

MVP scope:

- FastAPI Gateway
- Qwen2.5-Coder 7B via vLLM
- Workspace Index & Context Builder
- TaskGraph Runtime
- Single Agent Runtime
- Tool Layer
- Patch Manager
- Verifier
- Evaluation Service
- Learning Candidate Queue

Out of scope for this MVP:

- New architecture phases
- 70B+ planning
- GRPO
- Multimodal
- Multiple model families
- Research-only systems
- Autonomous exploit execution

### Implementation Order By Module

| Order | Module | Why Needed | Dependencies | Estimated LOC | Estimated Time | Risk |
|---:|---|---|---|---:|---:|---|
| 1 | FastAPI Gateway | One stable external API for chat, agent runs, projects, tasks, patches, evaluation, health, auth, and quota. | Phase 34 auth/quota, Pydantic schemas, Postgres connection. | 700-1,000 | 4 days | Medium |
| 2 | Qwen2.5-Coder 7B vLLM Serving | Real coding model backend for useful reasoning and generation. | Linux CUDA box, vLLM, OpenAI-compatible client, model profile config. | 350-550 | 3 days after GPU is available | High |
| 3 | Database Foundation | Persistent projects, sessions, runs, task graphs, patches, evals, and learning candidates. | Postgres, Alembic or SQL init scripts. | 450-650 | 3 days | Medium |
| 4 | Workspace Index & Context Builder | Cursor-class repo understanding requires file map, symbols, chunks, imports, call graph, and retrieval packs. | Git workspace, Tree-sitter or fallback AST, Qdrant, Postgres. | 1,400-2,000 | 7 days | High |
| 5 | TaskGraph Runtime | Converts user intent into executable coding steps with status, retries, dependencies, and resumability. | Database, planner schema, single agent runtime. | 900-1,300 | 5 days | High |
| 6 | Single Agent Runtime | Minimal reliable agent loop: plan, gather context, edit, test, verify, answer. | Model backend, TaskGraph runtime, Context Builder, Tools, Patch Manager. | 1,200-1,800 | 7 days | High |
| 7 | Tool Layer | Safe command execution, tests, git diff, file read/write, search, sandbox route, Semgrep route. | Sandbox, filesystem policies, Docker optional. | 900-1,300 | 5 days | High |
| 8 | Patch Manager | Turns model edits into previewable, reversible diffs with backups and rollback. | Workspace, git diff, database patches table. | 600-900 | 4 days | Medium |
| 9 | Verifier | Compile/test/static/security checks decide whether an answer is acceptable. | Tool Layer, Patch Manager, test commands, Semgrep optional. | 700-1,100 | 5 days | High |
| 10 | Evaluation Service | SWE-Bench Lite and internal regression checks measure if the coding agent is improving. | Agent API, Tool Layer, database, reports. | 1,000-1,500 | 6 days | High |
| 11 | Learning Candidate Queue | Saves only verified failures/fixes/traces for later SFT data. | Evaluation Service, Verifier, Postgres, JSONL export. | 400-700 | 3 days | Medium |
| 12 | Release/Smoke Automation | Keeps every MVP build testable with one command. | All modules above. | 250-450 | 2 days | Medium |

### Exact 8-Week Build Order

Week 1:

- Harden `src/mythos/gateway`.
- Add Postgres schema and migration/init script.
- Add project/session/run/task CRUD endpoints.
- Keep auth/quota middleware wired through Phase 34.
- Deliverable: local API can create a project, start a run, store run state, and return health.

Week 2:

- Add Qwen2.5-Coder 7B vLLM server config and OpenAI-compatible backend profile.
- Add gateway model health checks.
- Add fallback mock profile for local CPU development.
- Deliverable: same gateway request works with mock locally and Qwen via vLLM on GPU.

Week 3:

- Build Workspace Index.
- Store file inventory, code chunks, symbols, imports, dependencies, and hashes.
- Add Qdrant embeddings for chunks.
- Deliverable: indexing a repository creates searchable context records and a workspace summary.

Week 4:

- Build Context Builder and TaskGraph Runtime.
- Generate task graph from prompt plus indexed repo context.
- Persist every node and transition.
- Deliverable: a prompt creates a durable task graph and a ranked context pack.

Week 5:

- Build Single Agent Runtime.
- Agent executes one task graph through context retrieval, model call, patch proposal, and tool calls.
- Deliverable: agent can inspect a repo and produce a patch preview.

Week 6:

- Finish Tool Layer, Patch Manager, and Verifier.
- Add command execution, test command routing, diff preview, apply, rollback, and verification reports.
- Deliverable: agent can edit code, run tests, rollback failed patches, and return verified status.

Week 7:

- Build Evaluation Service with SWE-Bench Lite workflow.
- Add internal smoke tasks for repo understanding, debugging, patching, and security detection.
- Deliverable: one command runs benchmark tasks and stores scores.

Week 8:

- Build Learning Candidate Queue and release automation.
- Only verifier-passed fixes and useful failures become learning candidates.
- Add end-to-end release gate for gateway, indexing, agent run, patch verify, and evaluation smoke.
- Deliverable: working MVP coding agent with repeatable green smoke checks.

### Final MVP Repository Structure

```text
src/mythos/
  gateway/
    api.py
    auth.py
    dependencies.py
    errors.py
    schemas.py
  serving/
    vllm_server.py
    profiles.py
    openai_client.py
    health.py
  db/
    connection.py
    migrations/
    models.py
    repositories.py
  indexing/
    file_inventory.py
    chunker.py
    symbols.py
    embeddings.py
    qdrant_store.py
    context_builder.py
  runtime/
    taskgraph.py
    state_machine.py
    single_agent.py
    prompts.py
  tools/
    command_runner.py
    sandbox.py
    filesystem.py
    git_tools.py
    search.py
    semgrep.py
  patch_manager/
    diff.py
    apply.py
    rollback.py
    records.py
  verifier/
    test_runner.py
    static_checks.py
    security_checks.py
    verdict.py
  evaluation/
    swebench_lite.py
    internal_tasks.py
    reports.py
    regression.py
  learning/
    candidate_queue.py
    exporters.py
  shared/
    schemas.py
    config.py
    logging.py
    ids.py
```

Current bridge rule:

- Existing `src/mythos/*` facade remains the product import path.
- Old `src/phase*` modules may be called only from adapters while their code is migrated into the MVP folders above.
- New MVP code should not import directly from `src/phase*` unless the file name includes `adapter`.

### API Contracts

All responses use this error shape:

```json
{
  "error": "string",
  "detail": "string",
  "request_id": "string"
}
```

#### Gateway / Health

Endpoint: `GET /health/live`

Response:

```json
{
  "status": "live"
}
```

Endpoint: `GET /health/ready`

Response:

```json
{
  "status": "ready",
  "database": {"ok": true},
  "model": {"ok": true, "profile": "qwen2.5-coder-7b-vllm"},
  "qdrant": {"ok": true},
  "auth": {"enabled": true, "quota_enabled": true}
}
```

#### Projects

Endpoint: `POST /v1/projects`

Request:

```json
{
  "name": "string",
  "repo_path": "string",
  "default_branch": "string"
}
```

Response:

```json
{
  "project_id": "uuid",
  "name": "string",
  "repo_path": "string",
  "created_at": "timestamp"
}
```

Endpoint: `GET /v1/projects/{project_id}`

Response:

```json
{
  "project_id": "uuid",
  "name": "string",
  "repo_path": "string",
  "index_status": "not_indexed|indexing|ready|failed",
  "last_indexed_at": "timestamp|null"
}
```

#### Sessions

Endpoint: `POST /v1/sessions`

Request:

```json
{
  "project_id": "uuid",
  "title": "string"
}
```

Response:

```json
{
  "session_id": "uuid",
  "project_id": "uuid",
  "title": "string",
  "created_at": "timestamp"
}
```

#### Indexing

Endpoint: `POST /v1/projects/{project_id}/index`

Request:

```json
{
  "force": false,
  "include_globs": ["**/*.py", "**/*.ts", "**/*.js", "**/*.go", "**/*.rs"],
  "exclude_globs": [".git/**", "node_modules/**", ".venv/**", "dist/**"]
}
```

Response:

```json
{
  "index_job_id": "uuid",
  "project_id": "uuid",
  "status": "queued"
}
```

Endpoint: `GET /v1/projects/{project_id}/index/status`

Response:

```json
{
  "project_id": "uuid",
  "status": "ready",
  "file_count": 120,
  "chunk_count": 940,
  "symbol_count": 1800,
  "last_error": null
}
```

Endpoint: `POST /v1/context/build`

Request:

```json
{
  "project_id": "uuid",
  "query": "fix failing auth test",
  "token_budget": 24000,
  "pinned_files": ["src/auth.py"],
  "task_id": "uuid|null"
}
```

Response:

```json
{
  "context_id": "uuid",
  "project_id": "uuid",
  "query": "string",
  "token_budget": 24000,
  "files": [{"path": "string", "reason": "string", "score": 0.91}],
  "chunks": [{"chunk_id": "uuid", "path": "string", "start_line": 1, "end_line": 80, "score": 0.88}],
  "prompt_context": "string"
}
```

#### Agent Runs

Endpoint: `POST /v1/agent/runs`

Request:

```json
{
  "project_id": "uuid",
  "session_id": "uuid",
  "prompt": "fix the login bug",
  "mode": "code_edit|debug|explain|security_review",
  "max_steps": 12,
  "apply_patch": false
}
```

Response:

```json
{
  "run_id": "uuid",
  "project_id": "uuid",
  "session_id": "uuid",
  "status": "queued",
  "task_graph_id": "uuid"
}
```

Endpoint: `GET /v1/agent/runs/{run_id}`

Response:

```json
{
  "run_id": "uuid",
  "status": "queued|running|needs_review|verified|failed|cancelled",
  "summary": "string",
  "current_node_id": "uuid|null",
  "confidence": 0.82,
  "patch_id": "uuid|null",
  "verification_id": "uuid|null"
}
```

Endpoint: `GET /v1/agent/runs/{run_id}/events`

Response: Server-Sent Events, each event:

```json
{
  "event_id": "uuid",
  "run_id": "uuid",
  "type": "task_started|tool_call|patch_created|verification_finished|final",
  "payload": {},
  "created_at": "timestamp"
}
```

#### TaskGraph

Endpoint: `GET /v1/taskgraphs/{task_graph_id}`

Response:

```json
{
  "task_graph_id": "uuid",
  "project_id": "uuid",
  "goal": "string",
  "nodes": [],
  "edges": [],
  "status": "running"
}
```

#### Tools

Endpoint: `POST /v1/tools/execute`

Request:

```json
{
  "project_id": "uuid",
  "run_id": "uuid",
  "tool": "shell|test|git_diff|semgrep|read_file|write_file",
  "args": {},
  "timeout_ms": 30000
}
```

Response:

```json
{
  "tool_call_id": "uuid",
  "status": "ok|failed|timeout|blocked",
  "stdout": "string",
  "stderr": "string",
  "exit_code": 0,
  "duration_ms": 1234
}
```

#### Patches

Endpoint: `POST /v1/patches/preview`

Request:

```json
{
  "project_id": "uuid",
  "run_id": "uuid",
  "edits": [
    {"path": "src/auth.py", "new_content": "string"}
  ]
}
```

Response:

```json
{
  "patch_id": "uuid",
  "status": "preview",
  "diff": "string",
  "files_changed": ["src/auth.py"]
}
```

Endpoint: `POST /v1/patches/{patch_id}/apply`

Response:

```json
{
  "patch_id": "uuid",
  "status": "applied",
  "backup_id": "uuid"
}
```

Endpoint: `POST /v1/patches/{patch_id}/rollback`

Response:

```json
{
  "patch_id": "uuid",
  "status": "rolled_back"
}
```

#### Verifier

Endpoint: `POST /v1/verifier/run`

Request:

```json
{
  "project_id": "uuid",
  "run_id": "uuid",
  "patch_id": "uuid",
  "commands": ["pytest -q"],
  "security_checks": ["semgrep"],
  "timeout_ms": 120000
}
```

Response:

```json
{
  "verification_id": "uuid",
  "status": "passed|failed|timeout|blocked",
  "test_results": [{"command": "pytest -q", "exit_code": 0, "duration_ms": 3000}],
  "security_results": [{"tool": "semgrep", "findings": 0}],
  "verdict": "accept|reject",
  "reason": "string"
}
```

#### Evaluation

Endpoint: `POST /v1/evaluations/swebench-lite`

Request:

```json
{
  "model_profile": "qwen2.5-coder-7b-vllm",
  "limit": 20,
  "timeout_minutes": 30
}
```

Response:

```json
{
  "evaluation_run_id": "uuid",
  "status": "queued",
  "benchmark": "swebench_lite"
}
```

Endpoint: `GET /v1/evaluations/{evaluation_run_id}`

Response:

```json
{
  "evaluation_run_id": "uuid",
  "benchmark": "swebench_lite",
  "status": "running|complete|failed",
  "resolved": 7,
  "total": 20,
  "score": 0.35,
  "report_path": "artifacts/evaluation/run.md"
}
```

#### Learning Queue

Endpoint: `POST /v1/learning/candidates/export`

Request:

```json
{
  "status": "verified",
  "format": "jsonl",
  "limit": 1000
}
```

Response:

```json
{
  "export_id": "uuid",
  "path": "artifacts/learning/candidates.jsonl",
  "rows": 240
}
```

### Database Schema

Use Postgres. All IDs are UUID. All timestamps are `timestamptz`.

```sql
CREATE TABLE projects (
  id uuid PRIMARY KEY,
  name text NOT NULL,
  repo_path text NOT NULL,
  default_branch text NOT NULL DEFAULT 'main',
  index_status text NOT NULL DEFAULT 'not_indexed',
  last_indexed_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE sessions (
  id uuid PRIMARY KEY,
  project_id uuid NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  title text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE runs (
  id uuid PRIMARY KEY,
  project_id uuid NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  session_id uuid REFERENCES sessions(id) ON DELETE SET NULL,
  prompt text NOT NULL,
  mode text NOT NULL,
  status text NOT NULL,
  model_profile text NOT NULL,
  confidence numeric(5,4),
  summary text,
  started_at timestamptz,
  finished_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE task_graphs (
  id uuid PRIMARY KEY,
  project_id uuid NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  run_id uuid REFERENCES runs(id) ON DELETE CASCADE,
  goal text NOT NULL,
  status text NOT NULL,
  graph_json jsonb NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE tasks (
  id uuid PRIMARY KEY,
  task_graph_id uuid NOT NULL REFERENCES task_graphs(id) ON DELETE CASCADE,
  run_id uuid NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
  kind text NOT NULL,
  title text NOT NULL,
  status text NOT NULL,
  depends_on uuid[] NOT NULL DEFAULT '{}',
  input_json jsonb NOT NULL DEFAULT '{}',
  output_json jsonb NOT NULL DEFAULT '{}',
  error text,
  started_at timestamptz,
  finished_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE workspace_files (
  id uuid PRIMARY KEY,
  project_id uuid NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  path text NOT NULL,
  language text,
  content_hash text NOT NULL,
  size_bytes integer NOT NULL,
  indexed_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(project_id, path)
);

CREATE TABLE code_chunks (
  id uuid PRIMARY KEY,
  project_id uuid NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  file_id uuid NOT NULL REFERENCES workspace_files(id) ON DELETE CASCADE,
  path text NOT NULL,
  language text,
  start_line integer NOT NULL,
  end_line integer NOT NULL,
  symbol_name text,
  chunk_hash text NOT NULL,
  token_count integer NOT NULL,
  content text NOT NULL,
  metadata jsonb NOT NULL DEFAULT '{}',
  indexed_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE patches (
  id uuid PRIMARY KEY,
  project_id uuid NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  run_id uuid REFERENCES runs(id) ON DELETE SET NULL,
  status text NOT NULL,
  diff text NOT NULL,
  files_changed text[] NOT NULL DEFAULT '{}',
  backup_json jsonb NOT NULL DEFAULT '{}',
  created_at timestamptz NOT NULL DEFAULT now(),
  applied_at timestamptz,
  rolled_back_at timestamptz
);

CREATE TABLE evaluations (
  id uuid PRIMARY KEY,
  benchmark text NOT NULL,
  model_profile text NOT NULL,
  status text NOT NULL,
  total integer NOT NULL DEFAULT 0,
  resolved integer NOT NULL DEFAULT 0,
  score numeric(8,5),
  report_path text,
  result_json jsonb NOT NULL DEFAULT '{}',
  started_at timestamptz,
  finished_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE memory_entries (
  id uuid PRIMARY KEY,
  project_id uuid REFERENCES projects(id) ON DELETE CASCADE,
  kind text NOT NULL,
  content text NOT NULL,
  source_run_id uuid REFERENCES runs(id) ON DELETE SET NULL,
  relevance numeric(5,4) NOT NULL DEFAULT 0.5,
  success_rate numeric(5,4) NOT NULL DEFAULT 0.5,
  usage_count integer NOT NULL DEFAULT 0,
  last_used_at timestamptz,
  metadata jsonb NOT NULL DEFAULT '{}',
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE learning_candidates (
  id uuid PRIMARY KEY,
  project_id uuid REFERENCES projects(id) ON DELETE CASCADE,
  run_id uuid REFERENCES runs(id) ON DELETE SET NULL,
  patch_id uuid REFERENCES patches(id) ON DELETE SET NULL,
  kind text NOT NULL,
  status text NOT NULL,
  prompt text NOT NULL,
  chosen text NOT NULL,
  verification_json jsonb NOT NULL DEFAULT '{}',
  score numeric(8,5),
  exported_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now()
);
```

Indexes:

```sql
CREATE INDEX idx_runs_project_status ON runs(project_id, status);
CREATE INDEX idx_tasks_graph_status ON tasks(task_graph_id, status);
CREATE INDEX idx_workspace_files_project_path ON workspace_files(project_id, path);
CREATE INDEX idx_code_chunks_project_path ON code_chunks(project_id, path);
CREATE INDEX idx_memory_project_kind ON memory_entries(project_id, kind);
CREATE INDEX idx_learning_status ON learning_candidates(status);
```

### Qdrant Collection Schema

Collection: `mythos_code_chunks`

Vector:

```json
{
  "name": "content",
  "size": 768,
  "distance": "Cosine"
}
```

Payload:

```json
{
  "chunk_id": "uuid",
  "project_id": "uuid",
  "file_id": "uuid",
  "path": "src/auth.py",
  "language": "python",
  "symbol_name": "login_user",
  "start_line": 10,
  "end_line": 80,
  "content_hash": "sha256",
  "chunk_hash": "sha256",
  "token_count": 420,
  "kind": "function|class|module|test|config",
  "imports": ["jwt", "bcrypt"],
  "updated_at": "timestamp"
}
```

Payload indexes:

- `project_id` keyword
- `path` keyword
- `language` keyword
- `symbol_name` keyword
- `kind` keyword

Collection: `mythos_memory`

Vector:

```json
{
  "name": "content",
  "size": 768,
  "distance": "Cosine"
}
```

Payload:

```json
{
  "memory_id": "uuid",
  "project_id": "uuid|null",
  "kind": "verified_fix|passed_benchmark|security_finding|successful_plan",
  "success_rate": 0.93,
  "usage_count": 8,
  "last_used_at": "timestamp",
  "source_run_id": "uuid|null"
}
```

### TaskGraph Schema

```json
{
  "task_graph_id": "uuid",
  "project_id": "uuid",
  "run_id": "uuid",
  "goal": "string",
  "status": "queued|running|blocked|complete|failed",
  "nodes": [
    {
      "node_id": "uuid",
      "kind": "understand|retrieve_context|edit|test|verify|summarize",
      "title": "string",
      "instructions": "string",
      "status": "queued|running|complete|failed|skipped",
      "depends_on": ["uuid"],
      "inputs": {},
      "outputs": {},
      "attempt": 1,
      "max_attempts": 2,
      "started_at": "timestamp|null",
      "finished_at": "timestamp|null"
    }
  ],
  "edges": [
    {"from": "uuid", "to": "uuid", "condition": "success"}
  ],
  "success_criteria": [
    "patch applies cleanly",
    "tests pass",
    "no new security findings"
  ],
  "created_at": "timestamp",
  "updated_at": "timestamp"
}
```

Required MVP node order:

1. `understand`: classify intent and extract target files.
2. `retrieve_context`: build context pack.
3. `edit`: ask model for patch.
4. `test`: run project test command.
5. `verify`: accept/reject patch.
6. `summarize`: return final answer.

### Context Builder Algorithm

Input:

- `project_id`
- user query
- token budget
- optional pinned files
- optional run/task metadata

Algorithm:

1. Normalize query:
   - Lowercase for retrieval terms.
   - Extract file names, symbol names, error messages, stack traces, test names, and package names.
2. Load project metadata:
   - Repo path.
   - File inventory.
   - Last index timestamp.
   - Language distribution.
3. Add pinned context:
   - Always include user-pinned files.
   - Always include files from stack traces.
   - Always include failing test files if detected.
4. Hybrid retrieve chunks:
   - Vector search Qdrant by query embedding.
   - Keyword search Postgres by path, symbol, import, and error tokens.
   - Git recency boost for recently changed files.
5. Expand neighbors:
   - Include direct imports.
   - Include tests for selected source files.
   - Include source files for selected tests.
   - Include callers/callees when symbol graph exists.
6. Score each candidate:
   - `final = 0.45 * semantic + 0.25 * keyword + 0.15 * graph + 0.10 * recency + 0.05 * pinned`
7. Deduplicate:
   - Remove duplicate chunks by `chunk_hash`.
   - Prefer smaller symbol chunks over full-file chunks unless the file is small.
8. Pack by priority:
   - Repository summary.
   - Relevant error/test output.
   - Pinned files.
   - Top source chunks.
   - Top test chunks.
   - Dependency snippets.
9. Enforce budget:
   - Reserve 20 percent for model response.
   - Reserve 10 percent for task instructions.
   - Use remaining 70 percent for code context.
10. Return:
   - `context_id`
   - ranked file list
   - ranked chunk list
   - final prompt context
   - omitted high-score chunks for debugging

Context pack format:

```text
<repo_summary>
...
</repo_summary>
<user_request>
...
</user_request>
<error_context>
...
</error_context>
<files>
<file path="src/auth.py" lines="1-120">
...
</file>
</files>
```

### Patch Verification Workflow

1. Agent proposes edits as structured file operations.
2. Patch Manager validates paths:
   - Path must remain inside project root.
   - No `.git` writes.
   - No secrets file writes unless explicitly allowed.
3. Patch Manager creates preview diff.
4. Verifier checks patch applies cleanly on current workspace hash.
5. Patch Manager writes backup snapshot for changed files.
6. Patch Manager applies patch.
7. Verifier runs formatting/lint command if configured.
8. Verifier runs targeted tests:
   - Tests mentioned in prompt.
   - Tests near changed files.
   - Failing tests from context.
9. Verifier runs full project smoke command if configured.
10. Verifier runs security checks:
    - Semgrep ruleset if installed.
    - Dependency audit when project type supports it.
11. If all checks pass:
    - Patch status becomes `verified`.
    - Run status becomes `verified`.
    - Candidate is eligible for learning queue.
12. If any check fails:
    - Patch status becomes `failed`.
    - Verifier stores logs.
    - Patch Manager rolls back unless user requested manual review.
    - Failure becomes a learning candidate only as `failed_trace`, not SFT `chosen`.

Acceptance rule:

- MVP only returns `verified` when tests pass and no configured security check reports new high-confidence findings.

### SWE-Bench Lite Evaluation Workflow

1. Download or mount SWE-Bench Lite task set.
2. For each task:
   - Create isolated workspace.
   - Checkout specified repository commit.
   - Create Mythos project row.
   - Index workspace.
   - Start agent run with issue prompt.
3. Agent builds context and proposes patch.
4. Patch Manager previews and applies patch.
5. Verifier runs SWE-Bench test command.
6. Record result:
   - `resolved = true` only when official tests pass.
   - Store patch diff, logs, duration, model profile, and failure reason.
7. After all tasks:
   - Compute resolved/total.
   - Generate Markdown report.
   - Store evaluation row.
   - Add verified successes and useful failed traces to Learning Candidate Queue.

Minimum MVP metrics:

- `resolved`
- `total`
- `resolve_rate`
- `mean_duration_seconds`
- `patch_apply_rate`
- `test_pass_rate`
- `top_failure_reasons`

### First Three Milestones

Milestone 1: API and Persistence Skeleton

- Deadline: end of Week 1.
- Must include:
  - Gateway health.
  - Auth/quota middleware wired.
  - Postgres schema.
  - Project/session/run/task CRUD.
  - Basic CLI smoke command.
- Done when:
  - A project can be created.
  - A session can be created.
  - A run can be created and stored.
  - Release smoke passes locally.

Milestone 2: Repository Understanding

- Deadline: end of Week 3.
- Must include:
  - Workspace file inventory.
  - Code chunking.
  - Symbol extraction.
  - Qdrant code chunk storage.
  - Context Builder endpoint.
- Done when:
  - A real repository can be indexed.
  - Querying "fix auth bug" returns relevant auth files/tests.
  - Context pack stays inside token budget.

Milestone 3: Verified Code Editing Loop

- Deadline: end of Week 6.
- Must include:
  - TaskGraph execution.
  - Single Agent Runtime.
  - Patch preview/apply/rollback.
  - Test execution.
  - Verifier verdict.
- Done when:
  - Agent can modify a small repo.
  - Tests run automatically.
  - Passing patch is marked `verified`.
  - Failing patch rolls back and stores logs.

### MVP Completion Definition

The 8-week MVP is complete only when:

- Gateway can run with auth enabled.
- Qwen2.5-Coder 7B vLLM profile is available on GPU infrastructure.
- Repository indexing works on a real multi-file repo.
- Context Builder returns ranked relevant code context.
- TaskGraph persists and resumes node status.
- Single Agent Runtime creates a patch from a short coding/debugging prompt.
- Patch Manager can preview, apply, and rollback.
- Verifier runs tests and rejects failed patches.
- Evaluation Service can run at least a small SWE-Bench Lite subset.
- Learning Candidate Queue stores only verifier-backed records.

## Most Important Seven Pillars

These are the parts to keep improving again and again:

1. Planner
2. Multi-Agent System
3. Memory
4. Critic
5. Verification
6. Security Experts
7. High-quality coding/reasoning data

Parameter count helps, but these seven systems decide whether the AI is actually useful for agentic coding, debugging, security review, patching, and long-context repo work.

## Fast Operational Commands

Start stable local real backend:

```powershell
.\scripts\run_phase42_profile_switcher.ps1 -Profile tiny-llama-cpu-smoke
.\scripts\start_phase16_real_backend.ps1
```

Start chat API:

```powershell
.\scripts\start_phase11_chat_background.ps1
```

Check health:

```powershell
Invoke-RestMethod http://127.0.0.1:8016/health/ready
Invoke-RestMethod http://127.0.0.1:8090/health/ready
Invoke-RestMethod http://127.0.0.1:8090/v1/quality/latest
```

Run V1 release gate:

```powershell
python src\mythos_v1\release_gate.py --run-id mythos-v1-local --mode quick --include-real-backend --strict
```

Run Phase 18 model-quality loop:

```powershell
$env:PHASE18_MODEL_ENDPOINT='http://127.0.0.1:8016/v1'
$env:PHASE18_MODEL_NAME='hf-internal-testing/tiny-random-LlamaForCausalLM'
.\scripts\run_phase18_model_quality.ps1
```

## Chief Architect Migration Plan: 51 Phases To 12 Core Modules

This is the executable consolidation plan. The goal is to stop treating phases as product modules. Phases become historical implementation sources. The production system should be operated through 12 core modules.

### Target Core Modules

1. Gateway Layer
2. Intent & Planning Engine
3. TaskGraph Runtime
4. Agent Router & Worker Pool
5. Tool Execution Layer
6. Workspace Index & Context Builder
7. Unified Memory System
8. Critic / Verifier / Guardrails
9. Patch Manager
10. Evaluation Service
11. Learning Pipeline
12. Model Ops & Serving

### Phase Migration Table

| Current Phase | Action | New Module | Reason |
|---|---|---|---|
| Phase 1: Data Ingestion And Code Tokenizer | Merge | Workspace Index & Context Builder; Learning Pipeline | Code parsing/call graphs belong to repo indexing. Tokenizer training belongs to training assets, not runtime control. |
| Phase 2: Docker Sandbox Engine | Keep | Tool Execution Layer | This is a core capability for safe execution, tests, and verification. |
| Phase 3: Rejection Sampling Pipeline | Merge | Learning Pipeline | Dataset generation should be one path fed by eval failures and verifier-approved traces. |
| Phase 4: Dynamic Swarm Orchestrator | Replace | Agent Router & Worker Pool | Superseded by consolidated runtime. Keep ideas, remove separate orchestrator control plane. |
| Phase 5: Self-Healing Runtime | Merge | Learning Pipeline; TaskGraph Runtime | Runtime repair should be an execution policy, while successful traces feed learning. |
| Phase 6: Redis Regenerative Quota Engine | Keep | Gateway Layer | Quota/rate limiting belongs at API edge. |
| Phase 7: GRPO Training Loop | Keep | Learning Pipeline | Critical later, but inactive until eval rewards are reliable. |
| Phase 8: Serving Stack | Merge | Model Ops & Serving; Gateway Layer | vLLM belongs to Model Ops; FastAPI gateway belongs to Gateway Layer. |
| Phase 9: Evaluation Harness | Merge | Evaluation Service | Keep benchmark logic but unify with all other scorecards. |
| Phase 10: Deployment Smoke And Readiness Audit | Keep | Model Ops & Serving; Evaluation Service | Keep as release/readiness gate, but no separate product phase. |
| Phase 11: PyTorch AI Core And Chat Interface | Merge | Gateway Layer; Model Ops & Serving | Backend abstraction remains. Chat API moves to gateway. PyTorch skeleton is archived until real training. |
| Phase 12: Capability Gauntlet | Merge | Evaluation Service | Architecture plumbing tests are one evaluation suite. |
| Phase 13: Backend Quality Harness | Merge | Evaluation Service; Model Ops & Serving | Model/backend quality comparison becomes an eval suite. |
| Phase 14: Exact Scorecard Harness | Keep | Evaluation Service | This is the main architecture scorecard seed. |
| Phase 15: Target Architecture Blueprint | Archive | Documentation / Architecture Records | Useful history, not runtime code. |
| Phase 16: Core Architecture Upgrades | Merge | TaskGraph Runtime; Agent Router; Tool Execution; Learning Pipeline | Contains durable pieces that must be absorbed into fewer modules. |
| Phase 17: GPU 7B-32B Readiness | Merge | Model Ops & Serving | GPU profiles and launch configs belong to model ops. |
| Phase 18: Real Model Quality Loop | Merge | Evaluation Service; Learning Pipeline | Real model eval and failure export should be one evaluation-to-learning path. |
| Phase 19: Unified Verifier Registry | Merge | Critic / Verifier / Guardrails | Verification registry is part of one guardrail service. |
| Phase 20: TaskGraph Runtime | Keep | TaskGraph Runtime | This should become the single execution state machine. |
| Phase 21: Experience Promotion | Merge | Unified Memory System; Learning Pipeline | Experience records are memory entries and training candidates. |
| Phase 22: Critic Service | Merge | Critic / Verifier / Guardrails | Merge with Phase 47 and Phase 51 strict review. |
| Phase 23: Model Quality Profiles | Merge | Model Ops & Serving; Evaluation Service | Profiles live in model ops; quality runs live in evaluation. |
| Phase 24: Main Agent V2 | Replace | TaskGraph Runtime; Agent Router & Worker Pool | Superseded by the consolidated Phase 40 path. |
| Phase 25: Experience Retrieval | Merge | Unified Memory System | Retrieval belongs inside memory, not a separate phase. |
| Phase 26: Patch Manager | Keep | Patch Manager | Critical isolated function: preview, diff, backup, rollback. |
| Phase 27: Verifier Policy Engine | Merge | Critic / Verifier / Guardrails | Policy profiles belong to the verifier/guardrail engine. |
| Phase 28: Security Expert Workflow | Merge | Critic / Verifier / Guardrails; Agent Router | Security agent is a worker role; security checks are guardrails. |
| Phase 29: Dataset Review Gate | Keep | Learning Pipeline | Critical quality gate to prevent bad synthetic data entering training. |
| Phase 30: Expanded Golden Benchmark | Merge | Evaluation Service | Benchmark generation and storage belong to eval service. |
| Phase 31: Long Context Packer | Keep | Workspace Index & Context Builder | Context packing is central to coding performance. |
| Phase 32: Critic Endpoint Contract | Merge | Critic / Verifier / Guardrails; Model Ops | Endpoint contract is config/health for critic model. |
| Phase 33: GPU Backend Contract | Merge | Model Ops & Serving | Model deployment contract belongs to model ops. |
| Phase 34: Auth And Quota Control | Keep | Gateway Layer | Critical for production exposure. |
| Phase 35: Observability | Keep | Gateway Layer; all services | Metrics/logging/tracing are cross-cutting, configured at gateway and service layer. |
| Phase 36: Data Flywheel | Merge | Learning Pipeline | Failure-to-data automation belongs to learning pipeline. |
| Phase 37: Production Vector Memory | Merge | Unified Memory System | Vector memory is one storage backend of unified memory. |
| Phase 38: Multi-Language Security Engine | Merge | Critic / Verifier / Guardrails | Static multi-language checks belong to guardrails. |
| Phase 39: Training Checkpoint Rollback Gate | Keep | Learning Pipeline; Model Ops & Serving | Critical for model promotion safety. |
| Phase 40: Integrated Default Agent Runtime | Keep | TaskGraph Runtime | Becomes the main runtime shell after removing duplicate paths. |
| Phase 41: Real Task Regression Pack | Merge | Evaluation Service | Regression pack becomes an eval suite. |
| Phase 42: Production Profile Switcher | Keep | Model Ops & Serving | One profile switcher is useful. Keep and simplify. |
| Phase 43: Meta Planner | Merge | Intent & Planning Engine | Merge with Phase 44. |
| Phase 44: Intent Expansion Engine | Merge | Intent & Planning Engine | Merge with Phase 43. |
| Phase 45: Parallel Agent Runtime | Merge | Agent Router & Worker Pool | Merge with Phase 50 routing. |
| Phase 46: Hierarchical Memory | Merge | Unified Memory System | Merge with vector memory, experience memory, and knowledge graph. |
| Phase 47: Reasoning Engine | Merge | Critic / Verifier / Guardrails | Merge with critic and strict reasoning contracts. |
| Phase 48: Knowledge Graph | Merge | Unified Memory System | Knowledge graph is memory metadata, not a standalone control plane. |
| Phase 49: Multimodal Expert | Archive | Agent Router & Worker Pool | Keep contract for future. Do not keep in core until coding agent is strong. |
| Phase 50: MoE Router Layer | Replace | Agent Router & Worker Pool | Rename software MoE to router. Real MoE later belongs to model architecture, not orchestration. |
| Phase 51: High-Stability Reasoning And Unified Memory | Merge | Unified Memory System; Critic / Verifier / Guardrails | Keep strict contracts, but split memory and review responsibilities into their target modules. |

### Final Module Contracts

#### Gateway Layer

Responsibilities:

- HTTP/API entrypoint for chat, agent runs, eval runs, memory lookup, and admin status.
- Authentication, API keys/JWT, Redis quota, request policy, request tracing, and SSE streaming.
- Public contract stability so internal modules can change without breaking clients.

Internal components:

- FastAPI app.
- Auth middleware.
- Quota middleware.
- Request router.
- SSE event streamer.
- Structured logging and Prometheus metrics.

APIs:

- `POST /v1/chat`
- `POST /v1/agent/run`
- `POST /v1/agent/run/stream`
- `GET /v1/quality/latest`
- `GET /health/live`
- `GET /health/ready`
- `GET /metrics`

Inputs:

- User prompt, workspace ID, files/project metadata, selected model profile, policy flags.

Outputs:

- Final answer, streamed events, tool traces, quality reports, quota headers.

Dependencies:

- TaskGraph Runtime, Model Ops, Unified Memory, Evaluation Service, Redis, observability.

#### Intent & Planning Engine

Responsibilities:

- Convert vague user intent into an executable task graph.
- Expand short prompts.
- Identify risks, success criteria, required tools, relevant files, and verification plan.
- Never execute tools directly.

Internal components:

- Intent classifier.
- Prompt expander.
- Requirement extractor.
- TaskGraph planner.
- Risk and security planner.
- Plan schema validator.

APIs:

- `plan(request) -> TaskGraph`
- `expand_intent(prompt, workspace_context) -> ExpandedIntent`
- `estimate_complexity(prompt, repo_context) -> ComplexityScore`

Inputs:

- User prompt, workspace context, retrieved memories, repo index summaries.

Outputs:

- Validated TaskGraph, success criteria, risk list, tool plan, context requirements.

Dependencies:

- Workspace Index & Context Builder, Unified Memory, Model Ops.

#### TaskGraph Runtime

Responsibilities:

- Single source of truth for execution state.
- Execute TaskGraph nodes in order or parallel where safe.
- Persist run state, retries, errors, artifacts, and final report.
- Enforce max iterations and stop conditions.

Internal components:

- DAG executor.
- Run state store.
- Event bus.
- Retry/correction policy.
- Artifact registry.
- Final aggregation.

APIs:

- `run(task_graph) -> AgentRunReport`
- `resume(run_id) -> AgentRunReport`
- `cancel(run_id) -> RunStatus`
- `events(run_id) -> AsyncIterator[RunEvent]`

Inputs:

- TaskGraph, policy profile, model profile, workspace ID.

Outputs:

- Run report, artifacts, events, failure traces, patch candidates.

Dependencies:

- Agent Router, Tool Execution, Patch Manager, Critic/Verifier, Unified Memory.

#### Agent Router & Worker Pool

Responsibilities:

- Route task nodes to specialist workers.
- Maintain a small, explicit worker set.
- Avoid spawning agents unless the task benefits from role separation.

Internal components:

- Router policy.
- Worker registry.
- Coder worker.
- Debugger worker.
- Security auditor worker.
- Tester worker.
- Research worker.
- Reviewer worker.

APIs:

- `route(task_node, context) -> WorkerAssignment`
- `execute(worker_assignment) -> WorkerArtifact`
- `review_peer(artifact) -> ReviewResult`

Inputs:

- Task node, context pack, tool permissions, model profile.

Outputs:

- Code artifacts, analysis artifacts, test plans, security findings, peer reviews.

Dependencies:

- Model Ops, Tool Execution, Critic/Verifier, Workspace Index.

#### Tool Execution Layer

Responsibilities:

- Safely run shell commands, tests, Docker sandbox jobs, Git operations, browser tasks, Semgrep, CodeQL, database probes, and future MCP/code-mode tools.
- Keep tool outputs compact and structured.
- Enforce permissions and resource limits.

Internal components:

- Docker sandbox.
- Shell runner.
- Git adapter.
- Static analysis adapters.
- Browser/visual QA adapter.
- Database adapter.
- Tool output trimmer.
- Permission gate.

APIs:

- `execute_tool(tool_call) -> ToolResult`
- `run_sandbox(files, command, policy) -> SandboxResult`
- `run_static_scan(workspace, policy) -> SecurityResult`

Inputs:

- Tool call schema, workspace, command, files, security policy.

Outputs:

- Structured stdout/stderr, exit code, metrics, summarized findings, artifacts.

Dependencies:

- Docker, Git, Semgrep, CodeQL, browser automation, databases.

#### Workspace Index & Context Builder

Responsibilities:

- Maintain repo index and code understanding.
- Build context packs under token budget.
- Combine AST/call graph, semantic search, file summaries, symbols, diffs, and test history.

Internal components:

- Incremental file index.
- AST/call graph extractor.
- Symbol graph.
- Embedding index.
- Merkle/hash change detector.
- Context packer.
- Token budget allocator.

APIs:

- `index_workspace(workspace_id) -> IndexReport`
- `retrieve(query, filters, budget) -> ContextPack`
- `pack(task_graph, memory_hits, budget) -> ModelContext`

Inputs:

- Workspace path, changed files, query, task graph, memory hits.

Outputs:

- Ranked code chunks, symbol graph, call graph slices, context pack.

Dependencies:

- Unified Memory, embedding model, tokenizer, filesystem.

#### Unified Memory System

Responsibilities:

- Store and retrieve session, project, experience, and knowledge graph memory.
- Prevent context pollution.
- Promote only verified fixes, passed benchmarks, security findings, and successful plans.

Internal components:

- Session memory.
- Project memory.
- Experience memory.
- Knowledge graph.
- Vector store.
- Memory ingestion gate.
- Retrieval ranker.
- Cold archive.

APIs:

- `remember(entry, policy) -> MemoryWriteResult`
- `retrieve(query, project_id, limit) -> MemoryHits`
- `promote_failure_fix(trace) -> ExperienceRecord`
- `archive_low_quality() -> ArchiveReport`

Inputs:

- Verified run traces, project metadata, bug/fix/outcome records, queries.

Outputs:

- Ranked memory hits, promotion reports, graph relationships.

Dependencies:

- Qdrant/pgvector, Postgres, Redis cache, Workspace Index.

#### Critic / Verifier / Guardrails

Responsibilities:

- Validate plans, outputs, patches, security posture, format, and release readiness.
- Separate critique from execution.
- Apply deterministic checks before model judgment.

Internal components:

- Schema validator.
- Policy engine.
- Static security verifier.
- Test verifier.
- Critic model adapter.
- Strict role-contract checker.
- Safety and permission guardrails.

APIs:

- `verify_artifact(artifact, criteria) -> VerificationResult`
- `critic_review(artifact, context) -> CriticResult`
- `security_review(workspace, patch) -> SecurityResult`
- `release_gate(run_id) -> ReleaseDecision`

Inputs:

- Plans, patches, tool results, test results, security scan results.

Outputs:

- Pass/fail decision, confidence, flaws, required fixes, release decision.

Dependencies:

- Tool Execution, Model Ops, Evaluation Service.

#### Patch Manager

Responsibilities:

- Apply, preview, revert, and review file changes.
- Protect user changes and support rollback.

Internal components:

- Diff generator.
- Patch applier.
- Backup store.
- Conflict detector.
- Rollback manager.

APIs:

- `preview_patch(patch) -> Diff`
- `apply_patch(patch, policy) -> PatchResult`
- `rollback(patch_id) -> RollbackResult`

Inputs:

- Patch artifact, workspace, policy.

Outputs:

- Diff, applied files, backup IDs, rollback status.

Dependencies:

- Git, filesystem, Critic/Verifier.

#### Evaluation Service

Responsibilities:

- Run all architecture, coding, security, regression, and model-quality evaluations.
- Store comparable results.
- Drive failure analysis and learning candidates.

Internal components:

- Architecture smoke suite.
- Golden scorecard.
- SWE-bench/SWE-bench Lite/SWE-bench Verified adapter.
- HumanEval/MBPP adapter.
- CyberSecEval/custom security suite.
- Regression tracker.
- Report generator.

APIs:

- `run_eval(suite, model_profile) -> EvalReport`
- `compare_runs(current, baseline) -> RegressionReport`
- `export_failures(run_id) -> LearningCandidates`

Inputs:

- Model profile, workspace, benchmark suite, policy.

Outputs:

- Scores, pass rates, category deltas, failure clusters, candidate data.

Dependencies:

- Tool Execution, Model Ops, Learning Pipeline, Postgres.

#### Learning Pipeline

Responsibilities:

- Convert verified traces and eval failures into reviewed SFT/GRPO data.
- Run SFT/QLoRA and GRPO.
- Gate model promotion with rollback.

Internal components:

- Rejection sampler.
- Dataset review gate.
- SFT/QLoRA trainer.
- GRPO trainer.
- Reward model/rule rewards.
- Checkpoint comparator.
- Promotion and rollback gate.

APIs:

- `queue_candidate(trace) -> CandidateId`
- `review_candidate(candidate_id) -> ReviewDecision`
- `train_sft(config) -> TrainingRun`
- `train_grpo(config) -> TrainingRun`
- `promote_checkpoint(checkpoint_id) -> PromotionDecision`

Inputs:

- Verified traces, failure clusters, approved examples, model configs.

Outputs:

- Datasets, checkpoints, eval reports, promotion decisions.

Dependencies:

- Evaluation Service, Model Ops, Postgres, object storage, GPU cluster.

#### Model Ops & Serving

Responsibilities:

- Manage model profiles, serving, inference, tokenizer assets, quantization, health, and scaling.
- Provide one model API to all modules.

Internal components:

- OpenAI-compatible client.
- vLLM server config.
- Profile switcher.
- Quantization pipeline.
- Checkpoint registry.
- Health probes.
- Load test runner.

APIs:

- `generate(request, profile) -> ModelResponse`
- `list_profiles() -> ModelProfiles`
- `activate_profile(profile) -> ActivationReport`
- `health(profile) -> HealthReport`

Inputs:

- Prompt/context, generation config, model profile.

Outputs:

- Model response, token usage, latency, health status.

Dependencies:

- vLLM, CUDA hosts, checkpoint storage, tokenizer storage.

### Final Folder Structure

```text
src/mythos/
  gateway/
    api.py
    auth.py
    quota.py
    streaming.py
    observability.py
  planning/
    intent.py
    planner.py
    schemas.py
  runtime/
    taskgraph.py
    executor.py
    events.py
    state_store.py
  agents/
    router.py
    workers.py
    prompts.py
  tools/
    sandbox.py
    shell.py
    git.py
    security.py
    browser.py
    databases.py
  context/
    indexer.py
    callgraph.py
    embeddings.py
    packer.py
  memory/
    store.py
    ranker.py
    graph.py
    promotion.py
  guardrails/
    critic.py
    verifier.py
    security.py
    policies.py
  patches/
    manager.py
    diff.py
    rollback.py
  evaluation/
    suites.py
    swebench.py
    cybersec.py
    regression.py
    reports.py
  learning/
    datasets.py
    rejection_sampling.py
    sft.py
    grpo.py
    checkpoint_gate.py
  model_ops/
    backends.py
    profiles.py
    serving.py
    quantization.py
    health.py
  shared/
    config.py
    schemas.py
    errors.py
    telemetry.py
legacy/
  phase1/
  ...
  phase51/
docs/
  mythos_complete_architecture_manual.md
```

Rule: new code goes into `src/mythos/*`. Existing `src/phase*` becomes compatibility/legacy until migrated. Do not add Phase 52.

### Final Service Architecture

```text
Client / IDE / CLI
  -> Gateway API
      -> TaskGraph Runtime
          -> Intent & Planning Engine
          -> Workspace Index & Context Builder
          -> Unified Memory System
          -> Agent Router & Worker Pool
              -> Model Ops & Serving
              -> Tool Execution Layer
              -> Patch Manager
          -> Critic / Verifier / Guardrails
          -> Evaluation Service
          -> Learning Pipeline
```

Deployment units:

- `mythos-gateway`: FastAPI API, auth, quota, streaming, metrics.
- `mythos-runtime`: TaskGraph execution and event state.
- `mythos-worker`: agent workers and tool calls.
- `mythos-indexer`: repo indexing and embeddings.
- `mythos-evaluator`: scorecards and benchmarks.
- `mythos-learning`: dataset review, SFT/GRPO jobs, checkpoint gates.
- `mythos-model`: vLLM/OpenAI-compatible serving profile.

### Final Database Architecture

Postgres:

- `runs`: agent runs and status.
- `task_nodes`: TaskGraph nodes and dependencies.
- `artifacts`: code/test/security artifacts.
- `eval_runs`: benchmark/eval results.
- `learning_candidates`: SFT/GRPO candidate rows.
- `checkpoints`: checkpoint metadata and promotion state.
- `memory_entries`: durable memory metadata.
- `audit_events`: user/tool/security audit logs.

Redis:

- Quota buckets.
- Short-lived run locks.
- Event stream buffers.
- Hot memory cache.
- Rate limits and idempotency keys.

Qdrant or pgvector:

- Code chunk embeddings.
- Experience memory embeddings.
- Project knowledge embeddings.

Object storage or filesystem artifact store:

- Run logs.
- Sandbox workspaces.
- Diffs and patches.
- Dataset shards.
- Checkpoints.
- Evaluation reports.

### Final Memory Architecture

```text
Working Memory
  -> current run only; cleared after run
Session Memory
  -> conversation/session scoped
Project Memory
  -> repo facts, architecture decisions, known conventions
Experience Memory
  -> verified failure/fix/outcome records
Knowledge Graph
  -> relationships among files, symbols, dependencies, bugs, fixes
Vector Index
  -> semantic retrieval over code and experience
Cold Archive
  -> low-use or low-score records
```

Retrieval policy:

1. Determine task intent and workspace.
2. Retrieve project facts and relevant code chunks.
3. Retrieve experience records only if verified.
4. Retrieve knowledge graph neighbors for symbols/files.
5. Rank by vector similarity, success rate, recency, and usage.
6. Pack context under token budget.
7. Never inject raw thoughts or failed guesses.

### Final Evaluation Architecture

Evaluation Service owns every score.

Suites:

- `architecture_smoke`: compile, imports, API, sandbox, memory.
- `golden_scorecard`: short prompt, debugging, security, patching, long-context tasks.
- `regression_pack`: fixed project-specific regression tasks.
- `humaneval_mbpp`: coding sanity.
- `swebench_lite`: first serious repo-level coding gate.
- `swebench_verified`: production-grade coding benchmark.
- `cybersec_eval`: insecure code, vulnerability detection, patch correctness.
- `head_to_head`: old model vs new model.

Metrics:

- pass@1, pass@5, pass@10.
- first-pass compile/test success.
- patch acceptance rate.
- insecure code rate.
- regression count.
- average tool calls.
- token cost per solved task.
- wall-clock time per solved task.
- human-review acceptance rate.

### Final Training Pipeline Architecture

```text
Evaluation failures + successful verified runs
  -> Learning candidate queue
  -> Dataset review gate
  -> SFT data
  -> QLoRA SFT
  -> Evaluation Service
  -> Checkpoint gate
  -> GRPO candidate pool
  -> GRPO training
  -> Evaluation Service
  -> Promotion or rollback
```

Rules:

- No training on unreviewed synthetic data.
- No GRPO before reliable verifier rewards.
- Every checkpoint must beat baseline on coding, security, and regression.
- If security score drops, automatic rollback.
- If SWE-bench score improves but insecure-code rate worsens, do not promote.

### Final Deployment Architecture

Local development:

- Gateway + runtime + tiny model profile.
- Docker sandbox.
- Redis/Postgres/Qdrant dev compose.
- Mock/low-quality model allowed only for plumbing.

GPU staging:

- vLLM 7B/14B profile.
- Real scorecards.
- SWE-bench Lite subset.
- Security scans.
- Dataset candidate export.

Production:

- Gateway behind Nginx/ingress.
- Redis quota and event streams.
- Postgres primary.
- Qdrant/pgvector.
- Runtime worker pool.
- Tool sandbox pool.
- vLLM model servers.
- Prometheus/Grafana/log pipeline.
- Human approval for risky tools.

### File Disposition

Delete immediately:

- Split docs already consolidated and removed from `docs/`.
- Any future standalone phase docs. Add content to this manual instead.

Merge into new modules:

- `src/phase43` + `src/phase44` -> `src/mythos/planning`.
- `src/phase45` + `src/phase50` -> `src/mythos/agents`.
- `src/phase46` + `src/phase48` + `src/phase37` + memory pieces of `src/phase51` -> `src/mythos/memory`.
- `src/phase22` + `src/phase47` + review pieces of `src/phase51` + `src/phase19` + `src/phase27` + `src/phase38` -> `src/mythos/guardrails`.
- `src/phase12` + `src/phase13` + `src/phase14` + `src/phase18` + `src/phase30` + `src/phase41` -> `src/mythos/evaluation`.
- `src/phase3` + `src/phase5` + `src/phase7` + `src/phase21` + `src/phase29` + `src/phase36` + `src/phase39` -> `src/mythos/learning`.
- `src/phase8` + `src/phase17` + `src/phase23` + `src/phase33` + `src/phase42` + model backend pieces of `src/phase11` -> `src/mythos/model_ops`.

Archive after migration:

- `src/phase4`
- `src/phase15`
- `src/phase24`
- `src/phase49`
- Old phase runner scripts after replacement scripts exist.

Critical and must remain until replacements pass:

- `src/phase2/docker_sandbox_engine.py`
- `src/phase10/architecture_readiness_audit.py`
- `src/phase11/chat_api.py`
- `src/phase11/model_backends.py`
- `src/phase14/scorecard_harness.py`
- `src/phase20/taskgraph_runtime.py`
- `src/phase26/patch_manager.py`
- `src/phase34/auth_quota.py`
- `src/phase35/observability.py`
- `src/phase40/integrated_agent.py`
- `src/phase42/profile_switcher.py`
- `src/mythos_v1/release_gate.py`

### Phased Execution Roadmap

#### Phase A: Architecture Consolidation

Goal: replace 51-phase mental model with 12 modules.

Deliverables:

- Create `src/mythos/*` folders.
- Add facade imports from old phases.
- Move no behavior at first; wrap old implementations.
- Replace public docs with this migration plan.
- One command: `python -m src.mythos.evaluation.release_gate`.

Exit criteria:

- Existing release gate still passes.
- `/v1/agent/run` still works.
- No new phase directories.

#### Phase B: Real Model Integration: Qwen 7B / 14B

Goal: stop measuring quality with tiny random model.

Deliverables:

- Linux CUDA host.
- vLLM serving profile for Qwen2.5-Coder 7B first, 14B second.
- Real model scorecard run.
- Baseline latency/cost profile.

Exit criteria:

- 7B endpoint stable.
- Phase 18/Evaluation Service reports real scores.
- Tiny model used only for local plumbing.

#### Phase C: Cursor-Class Repository Indexing

Goal: make repo context retrieval a competitive advantage.

Deliverables:

- Incremental file hashing.
- AST/symbol chunking.
- Embedding index.
- Context packer with diff/test/symbol awareness.
- Re-index only changed files.

Exit criteria:

- Large repo can be indexed incrementally.
- Context packs cite files/symbols.
- Retrieval improves golden scorecard.

#### Phase D: SWE-Bench & CyberSecEval

Goal: evaluate like a real coding/security system.

Deliverables:

- SWE-bench Lite adapter.
- SWE-bench Verified staging adapter.
- CyberSecEval/custom security suite.
- Regression storage in Postgres.

Exit criteria:

- Every model/profile has comparable eval report.
- Failures export to learning queue.

#### Phase E: SFT + QLoRA

Goal: improve model on verified coding/security traces.

Deliverables:

- Reviewed dataset.
- QLoRA SFT config.
- Checkpoint gate.
- Baseline comparison.

Exit criteria:

- SFT checkpoint beats base on target evals.
- No security regression.

#### Phase F: GRPO

Goal: optimize outcome-based coding/security performance.

Prerequisite:

- Reliable sandbox/test/security rewards.

Deliverables:

- GRPO rollout generator.
- Reward component calibration.
- KL/reference model tracking.
- Rollback gate.

Exit criteria:

- GRPO improves pass rates without reward hacking.
- Security and format scores do not degrade.

#### Phase G: 32B Scaling

Goal: production-quality reasoning and repo-scale coding.

Deliverables:

- 32B vLLM deployment.
- Tensor parallel config.
- Longer context.
- Larger eval suite.

Exit criteria:

- 32B beats 14B enough to justify cost.
- Latency acceptable for agent runs.

#### Phase H: 70B Scaling

Goal: high-end competitive coding/security model.

Deliverables:

- Multi-GPU serving.
- Larger context and better retrieval.
- Distillation path into smaller models.
- Production workload tests.

Exit criteria:

- 70B improves hard repo/security tasks.
- Cost per solved task is economically viable.
- 100B+ only considered after this is proven.

## Consolidated Table Of Contents

1. [Mythos Target Architecture](#source-1-mythos-target-architecture) - `mythos_target_architecture.md`
2. [Mythos Architecture V1 Productization](#source-2-mythos-v1-productization) - `mythos_v1_productization.md`
3. [Phase 1-51 Architecture Manual](#source-3-phase1-to-51-architecture-manual) - `phase1_to_51_architecture_manual.md`
4. [Phase 40-42 Integrated Runtime](#source-4-phase40-to-42-integrated-runtime) - `phase40_to_42_integrated_runtime.md`
5. [Phase 43-50 Cognitive Architecture](#source-5-phase43-to-50-cognitive-architecture) - `phase43_to_50_cognitive_architecture.md`
6. [Phase 51: High-Stability Reasoning And Unified Memory](#source-6-phase51-high-stability-reasoning-memory) - `phase51_high_stability_reasoning_memory.md`
7. [mvp_start_guide.md](#source-7-mvp-start-guide) - `mvp_start_guide.md`
8. [Current Live Infrastructure Status](#source-8-live-infra-blockers) - `live_infra_blockers.md`
9. [7B-32B GPU Readiness](#source-9-gpu-7b32b-readiness) - `gpu_7b32b_readiness.md`
10. [Phase 10: Deployment Doctor and E2E Smoke Runner](#source-10-phase10-deployment-smoke) - `phase10_deployment_smoke.md`
11. [Phase 11 Architecture Plan](#source-11-phase11-20-step-architecture-plan) - `phase11_20_step_architecture_plan.md`
12. [Phase 11 PyTorch AI Core](#source-12-phase11-pytorch-ai-core) - `phase11_pytorch_ai_core.md`
13. [Phase 12 Capability Gauntlet](#source-13-phase12-capability-gauntlet) - `phase12_capability_gauntlet.md`
14. [Phase 13 Backend Quality Harness](#source-14-phase13-backend-quality) - `phase13_backend_quality.md`
15. [Phase 16 Core Architecture Upgrades](#source-15-phase16-core-upgrades) - `phase16_core_upgrades.md`
16. [Phase 18 Real Model Quality Loop](#source-16-phase18-model-quality-loop) - `phase18_model_quality_loop.md`
17. [Phases 19-23 Quality Hardening](#source-17-phase19-to-23-quality-hardening) - `phase19_to_23_quality_hardening.md`
18. [Phases 24-33 Power Architecture Layer](#source-18-phase24-to-33-power-architecture) - `phase24_to_33_power_architecture.md`
19. [Phases 34-36 Production Control Layer](#source-19-phase34-to-36-production-control) - `phase34_to_36_production_control.md`
20. [Phase 37-39 Production Hardening](#source-20-phase37-to-39-production-hardening) - `phase37_to_39_production_hardening.md`
21. [Phase 5 Self-Healing Runtime and QLoRA Staging Blueprint](#source-21-phase5-self-healing-blueprint) - `phase5_self_healing_blueprint.md`
22. [Phase 6 Redis Continuous Regenerative Quota Blueprint](#source-22-phase6-regenerative-quota-blueprint) - `phase6_regenerative_quota_blueprint.md`
23. [Phase 7 GRPO Training Blueprint](#source-23-phase7-grpo-blueprint) - `phase7_grpo_blueprint.md`
24. [Phase 8 vLLM Serving Blueprint](#source-24-phase8-serving-blueprint) - `phase8_serving_blueprint.md`
25. [Phase 9: Automated Evaluation Harness](#source-25-phase9-evaluation-harness) - `phase9_evaluation_harness.md`
26. [AI Architecture Scorecard](#source-26-scorecard-harness) - `scorecard_harness.md`

---

<a id="source-1-mythos-target-architecture"></a>

## Source 1: Mythos Target Architecture

Source file: `docs/mythos_target_architecture.md`

### Mythos Target Architecture

Target: A coding and cybersecurity AI system in the spirit of Cursor + Claude Code + DeepSeek R1, with a practical base-model start and a future 50B-100B+
scale path.

#### Core Pipeline

```text
User
  -> Intent Engine
  -> Planner
  -> Task Graph
  -> Agent Orchestrator
       -> Coding Expert
       -> Security Expert
       -> Research Expert
       -> Debug Expert
       -> Testing Expert
  -> Tool Layer
  -> Memory Layer
  -> Critic
  -> Verifier
  -> Answer
```

#### Practical Model Strategy

We should not begin by training a foundation model from scratch.

Practical starting point:

- Qwen/DeepSeek/Llama-family coding/reasoning model
- Serve through vLLM/OpenAI-compatible API
- Measure with the scorecard
- Fine-tune using failures

Why:

- Foundation pretraining needs huge GPU count, months, and massive data.
- The architecture, data loop, critic, verifier, and tool use matter first.
- A strong base model plus excellent agent architecture can move much faster.

#### Future Scale

Near term:

- 7B-32B practical model
- QLoRA/SFT/GRPO
- vLLM inference

Next:

- 50B-100B stronger model
- Multi-GPU Linux CUDA
- Better coding/security datasets

Research path:

- MoE design
- 512 experts as long-term target
- 500B total / 64B active / top-8 routing as research-scale goal



40% Data Quality
25% Architecture
20% Training Pipeline
10% Evaluation
5% Infrastructure




Data Quality        35%
Reasoning Training  20%
Agent System        15%
Evaluation          10%
Memory              10%
Tools               5%
Architecture        5%



#### What Actually Creates A Top AI

Parameter count is not enough. A 500B raw model alone will not automatically become world-class. A 70B model with excellent planning, memory, tools, critic, and verifier can often be more useful than a much larger model without those systems.

The seven highest-leverage parts we should keep improving together:

1. Planner
2. Multi-Agent System
3. Memory
4. Critic
5. Verification
6. Security Experts
7. High-quality coding/reasoning data

Yes: this is the right direction. From now on, every architecture upgrade should be judged by whether it improves one or more of these seven pillars.


`## Current Status

Already built locally:

- ModelBackend abstraction
- Chat/agent API
- Agentic coding runtime
- Docker sandbox
- Memory engine with Qdrant/Postgres/Redis hooks
- Security reasoning engine
- Scorecard and quality harness
- vLLM/OpenAI-compatible gateway path
- Durable TaskGraph planner
- Role-specific async micro-agents
- CriticBackend and VerifierBackend interfaces
- ExperienceMemory failure/fix/outcome promotion
- Defensive Git/Semgrep/CodeQL/browser tool adapters
- Scorecard failure exporter for SFT/GRPO data candidates
- Local real OpenAI-compatible backend path is connected and stable through a pinned tiny HF model for Windows CPU plumbing checks
- Main chat API can be pointed at `tiny-llama-cpu-smoke`, `qwen-cpu-smoke`, or future 7B-32B GPU/vLLM profiles through Phase 42
- Qwen and tiny HF model revisions are pinned for safer reproducible local loading
- Semgrep is available through the official Docker image
- CodeQL CLI is installed locally under tools/codeql
- CodeQL Python, JavaScript, and C/C++ query packs are installed
- Phase 16 real smoke passes with TaskGraph, agents, critic/verifier, tools, memory, exporter, and base-model connector all green
- Phase 17 GPU readiness pack exists for 7B-32B model serving/training
- Pinned 7B/14B/32B Qwen Coder profiles and DeepSeek coder profiles are generated
- vLLM, QLoRA SFT, GRPO, DeepSpeed, and Accelerate launch configs are generated for future Linux CUDA hardware
- Phase 18 real-model quality loop is implemented for local Qwen or future vLLM endpoints
- Phase 18 can run quick balanced or full exact scorecard suites, cluster failures, and export review-required SFT/GRPO candidates
- Chat API exposes `/v1/model-quality/latest` and aggregate `/v1/quality/latest` includes Phase 18
- Phase 19 unified verifier registry normalizes rule security, secret scan, Semgrep, CodeQL, and sandbox/test gates
- Phase 20 TaskGraph-first runtime executes role agents with critic and verifier hooks
- Main Phase 11 agent now includes a durable TaskGraph payload during planning
- Phase 21 promotes failures/outcomes into experience memory JSONL and optional Postgres/Qdrant/Redis sinks
- Phase 22 critic service supports heuristic mode today and model-backed critic mode through OpenAI-compatible endpoints
- Phase 23 model quality profiles prepare 7B/14B/32B scorecard runs against future vLLM endpoints
- Phase 24 Main Agent V2 bridges normal agent workflows into TaskGraph-first execution with experience memory
- Phase 25 retrieves failure/fix/outcome memory for planner prompts
- Phase 26 Patch Manager provides safe patch preview, backup, diff, and rollback
- Phase 27 Verifier Policy Engine adds fast/security/release/CodeQL policy profiles
- Phase 28 Security Expert Workflow runs defensive vulnerability triage, patch direction, and verifier checks
- Phase 29 Dataset Review Gate prevents unapproved synthetic rows from entering training
- Phase 30 Expanded Golden Benchmark generates 450 harder coding/security tasks
- Phase 31 Long Context Packer combines workspace retrieval and experience memory under token budget
- Phase 32 Critic Endpoint Contract validates real critic model endpoints
- Phase 33 GPU Backend Contract validates future 7B/14B/32B vLLM quality-run readiness
- Phase 34 adds JWT auth middleware and connects authenticated users to Phase 6 Redis quota
- Phase 35 adds Prometheus-compatible metrics and structured JSONL API logging
- Phase 36 adds the failure data flywheel from Phase 18 to Phase 3 queue and Phase 7 trigger manifest
- Phase 37 adds vector experience memory with local index plus optional Qdrant/Postgres sinks
- Phase 38 adds Rust, Go, JavaScript/TypeScript, and Solidity defensive security scanning
- Phase 39 adds training checkpoint promotion and rollback gates to prevent SFT/GRPO degradation
- Phase 40 makes the integrated vector-memory -> agent -> critic -> verifier -> security path the default `/v1/agent/run`
- Phase 41 generates a 400-task regression pack for short prompts, debugging, security, multi-file repos, and patch verification
- Phase 42 activates local CPU/mock and future 7B/14B/32B GPU profiles through one profile switcher
- Phase 43 adds a durable Meta Planner that turns broad prompts into requirements, architecture components, execution lanes, risks, and TaskGraph briefs
- Phase 44 adds short-prompt intent expansion for domains like login, streaming, API, commerce, and general systems
- Phase 45 adds a parallel specialist agent runtime with architect/coder/tester/security/research/reviewer lanes
- Phase 46 adds hierarchical memory layers: working, session, project, experience, and knowledge
- Phase 47 adds a thinker/critic/verifier reasoning engine for acceptance checks before final answers
- Phase 48 adds an explicit knowledge graph for phase dependencies, architecture relationships, and future bug/fix links
- Phase 49 adds a safe multimodal expert contract for screenshots, PDFs, diagrams, images, and repository folders
- Phase 50 adds a software MoE router for security, planning, coding, reasoning, memory, multimodal, and research experts
- Phase 40 now injects MoE route, meta-plan, hierarchical memory, vector memory, critic, reasoning review, verifier, and multi-language security into the default integrated agent path
- Chat API now exposes Phase 43-50 reports in `/v1/quality/latest` and provides `/v1/agent/run/stream` for SSE agent-run updates
- Phase 51 adds strict no-role-mixing reasoning contracts, schema-validated Planner/Executor/Critic/Verifier outputs, forced reflection below critic confidence `0.6`, and a four-tier unified memory manager with anti-pollution gates
- Phase 51 is now wired into Phase 40 as an active strict-stability guardrail, not only a standalone module
- Chat API now exposes Phase 51 in `/v1/quality/latest`
- Mythos V1 productization now adds a capability registry, release gate, real-backend comparison runner, GPU/training preflight, schema-validated SFT/GRPO record contracts, and chat activity streaming.
- The V1 release gate passes locally with the pinned tiny HF endpoint reachable; Phase 18 correctly reports that this tiny random model is not a deployable reasoning backend.

Main gaps:

- Current stable local backend is a pinned tiny HF model for immediate UI/API behavior checks; the 7B-32B GPU backend/training path is prepared but cannot be executed on this Windows CPU machine.
- The exact scorecard loop is implemented for OpenAI-compatible Qwen/DeepSeek/Llama endpoints; it should be run regularly in quick mode locally and full mode on a stronger GPU/vLLM backend.
- Critic service can call a real model, but no dedicated trained critic checkpoint exists yet.
- Verifier layer now exists, but needs broader benchmark coverage: SWE-Bench-style coding tasks, CyberSecEval-style security tasks, and repo-scale regression suites.
- Semgrep/CodeQL wrappers and verifier gates exist; Phase 19/27 now can call Phase 38 multi-language security directly, but deeper SARIF severity calibration and language benchmark tuning should continue.
- Experience memory promotion and retrieval exist; Phase 37 adds hash-vector retrieval plus optional `sentence-transformers` semantic embeddings, and production usage should enable Qdrant/pgvector-backed retrieval in every planning run.
- Training checkpoint rollback gates now exist through Phase 39, but they must be connected to real Phase 7/17 training jobs once GPU training begins.
- Phase 40 is now default for `/v1/agent/run` and includes Phase 43/46/47/50 context; it needs repeated real-prompt tuning so routing thresholds, reasoning thresholds, and verifier profiles become sharper.
- Phase 41 creates a stronger local regression pack; next step is running it against real 7B+ backends and storing per-run deltas.
- Phase 42 writes profile contracts; GPU profiles still require Linux CUDA hardware before they can serve/train.
- Git wrapper is ready and the local workspace has been initialized as a Git repository; next step is remote sync and disciplined commits/checkpoints.
- Auth is implemented but intentionally disabled by default for local development; set `PHASE34_AUTH_ENABLED=1` and `PHASE34_JWT_SECRET` before exposing the API.
- Observability is local-file/in-process today; production should scrape `/metrics` and ship JSONL logs to a log pipeline.
- Multimodal support is currently metadata/schema level only; OCR, vision encoders, and document extraction adapters are future work.
- The MoE router is currently a software router, not a trained neural router; later 50B-100B/MoE serving can attach to the same routing contract.
- Phase 51 memory retrieval now uses deterministic hash embeddings locally and can use semantic embeddings/Qdrant/pgvector in production while preserving the exact ranking formula.
- 50B-100B training/serving still needs Linux CUDA hardware, not this Windows CPU setup.

#### Immediate Build Order

1. Use Phase 40 `/v1/agent/run` as the default agent path for all serious architecture tests.
2. Enable Phase 34 auth/quota before exposing the API outside local machine.
3. Scrape Phase 35 `/metrics` and inspect structured logs during agent runs.
4. Run Phase 36 data flywheel after Phase 18 scorecard runs, then review candidates through Phase 29.
5. Rebuild Phase 37 vector memory after every Phase 21/36 promotion cycle and inject retrieved memories into planner prompts.
6. Run Phase 38 multi-language security scan after meaningful code changes, especially Rust/Go/JS/Solidity repos.
7. Generate Phase 41 regression pack after architecture changes and use it as a local regression gate.
8. Activate profiles through Phase 42 instead of manually changing scattered env vars.
9. Put all future Phase 7/17 trained checkpoints through Phase 39 before promotion.
10. Run Phase 27 security/release verifier profiles after meaningful code changes.
11. Run Phase 43-50 E2E after any cognitive/control-plane change.
12. Run Phase 51 smoke after any reasoning or memory contract change.
13. Use Phase 31 packed context plus Phase 46/51 memory in planner/model prompts for large repositories.
14. Feed Phase 48 knowledge graph and Phase 51 knowledge graph from Phase 1 call graphs and Phase 21 experience records.
15. Configure Phase 32 model critic against a stronger endpoint when available; later train a dedicated critic.
16. Expand verifier coverage with SWE-Bench/CyberSecEval-style tasks and repo-scale regression tasks.
17. Sync this Git repository to a remote and use commits/checkpoints before risky architecture changes.
18. On Linux CUDA hardware, run the prepared 7B profile first, then 14B/32B; reserve 50B-100B for later multi-GPU infrastructure.
19. Run `scripts/run_mythos_v1_release.ps1 -Mode full -IncludeRealBackend` before calling an architecture checkpoint production-ready.
20. Run `scripts/run_mythos_v1_training_preflight.ps1` after every dataset or training-script change so GPU arrival is not the first time we find broken contracts.

#### What We Need Next To Make It Stronger

- A stronger real model than the tiny CPU smoke backend: practical next target is Qwen/DeepSeek/Llama coder class 7B-14B, then 32B.
- A real critic backend trained or prompted specifically to find coding, security, planning, and verification mistakes.
- More golden tasks with hard short prompts, multi-file repos, failing tests, and security patch cases.
- A production memory promotion loop: failure -> fix -> outcome -> embedding -> retrieval during planning. Phase 37 now provides the retrieval layer; next step is making it default in every planner run.
- A verifier registry that combines sandbox tests, Semgrep, CodeQL, security rules, benchmark tests, and peer review.
- A dataset review gate so bad synthetic outputs do not enter SFT/GRPO training.
- A Linux CUDA environment for serious model quality work.
- Real checkpoint promotion policy: Phase 39 now protects checkpoints; next step is connecting it directly after every training/evaluation job.

#### Safety Position

Security tooling stays defensive:

- Static analysis
- Vulnerability detection
- Patch generation
- Verification

No autonomous exploit execution.

#### Generated Blueprint

Machine-readable and markdown blueprint:

```powershell
.\scripts\write_target_architecture.ps1
```

Outputs:

```text
artifacts/phase15/mythos-target-architecture.json
artifacts/phase15/mythos-target-architecture.md
```

---

<a id="source-2-mythos-v1-productization"></a>

## Source 2: Mythos Architecture V1 Productization

Source file: `docs/mythos_v1_productization.md`

### Mythos Architecture V1 Productization

Mythos V1 consolidates the architecture around one serious runtime:

```text
Phase 40 Integrated Agent
  -> intent and expert routing
  -> durable task graph
  -> project-isolated ranked memory
  -> specialist execution
  -> critic and strict reflection
  -> verifier and defensive security
  -> immutable reports and failure promotion
```

The older phase modules remain implementation components. They are not separate
products or separate brains.

#### Eight V1 Decisions

1. Git baseline and release tags protect known-good architecture states.
2. The capability registry assigns one owner and quality signal to each core capability.
3. The release gate executes exact golden and regression suites before release.
4. Backend comparison separates architecture-control quality from real-model quality.
5. Unified memory uses project isolation, vector ranking, deduplication, and cold archival.
6. Serious API requests enforce strict stability, verifier, and security policy.
7. The chat UI streams real runtime stages and exposes plan, memory, tools, and verification.
8. Training preflight validates reviewed SFT/GRPO contracts without pretending a GPU is present.

#### Release Gate

Quick local gate:

```powershell
.\scripts\run_mythos_v1_release.ps1
```

Full gate with live Docker, databases, gateway, and sandbox:

```powershell
$env:MYTHOS_RELEASE_MODE = "full"
.\scripts\run_mythos_v1_release.ps1
```

Include the active real model comparison:

```powershell
$env:MYTHOS_COMPARE_REAL = "1"
.\scripts\run_mythos_v1_release.ps1
```

Current local Windows CPU backend:

- Default stable profile: `tiny-llama-cpu-smoke`
- Model: `hf-internal-testing/tiny-random-LlamaForCausalLM`
- Revision: `9fb191250dd56d0ba7ec9785a025ed29c03d5998`
- Purpose: prove OpenAI-compatible serving, routing, release gate, scorecard, and failure promotion work end to end.
- Limitation: this is a random tiny model, so Phase 18 correctly reports low model quality. Do not use its score as Titan/Mythos reasoning quality.

The `qwen-cpu-smoke` profile remains documented, but the local Windows CPU stack currently exits with a native Torch access violation while loading that checkpoint. Use Linux CUDA/vLLM or a compatible Torch/Python stack for 7B-32B quality runs.

The release gate blocks on:

- missing capability modules,
- Python compilation failures,
- Bandit findings,
- role-contract or memory-lifecycle failures,
- integrated agent failures,
- regression failures,
- golden scorecard failures,
- missing training architecture assets.

GPU absence and missing reviewed training rows are reported separately. They do
not make the local architecture dishonest or unusable.

#### Memory Lifecycle

Phase 51 memory now provides:

- per-project directories,
- deterministic entry IDs and atomic upserts,
- hash embeddings locally and optional sentence-transformer embeddings,
- exact weighted retrieval scoring,
- retrieval history,
- low-quality cold archival,
- optional Qdrant/PostgreSQL synchronization,
- session working-memory deletion.

#### Training Contracts

Tracked schemas:

- `data/training/sft_record.schema.json`
- `data/training/grpo_record.schema.json`

Manifest:

- `config/mythos_v1_training_manifest.json`

Preflight:

```powershell
.\scripts\run_mythos_v1_training_preflight.ps1
```

Training remains blocked until reviewed data and Linux CUDA hardware exist.
After training, Phase 39 checkpoint comparison is mandatory before promotion.

---

<a id="source-3-phase1-to-51-architecture-manual"></a>

## Source 3: Phase 1-51 Architecture Manual

Source file: `docs/phase1_to_51_architecture_manual.md`

### Phase 1-51 Architecture Manual

This document is the A to Z map of the current AI architecture build. It
explains what each phase contains, why it exists, how it works, when to use it,
and where its code/artifacts live.

Current local target:

- Build a powerful coding and defensive cybersecurity AI architecture before
  serious GPU training.
- Keep the architecture model-independent: mock, local PyTorch, local Qwen,
  vLLM, and future 7B-100B models should fit the same contracts.
- Make every important action measurable through verifiers, scorecards,
  memory, and review gates.

Current machine constraints:

- Windows CPU local development is enough for architecture, sandbox, API,
  verifier, memory, and small Qwen smoke checks.
- Real 7B-32B serving/training and 50B-100B research need Linux CUDA hardware.
- DeepSpeed and AWQ are prepared in configs, but are GPU/Linux-path tools.

#### Phase 1: Data Ingestion And Code Tokenizer

Location:

- `src/phase1/callgraph_extractor.py`
- `src/phase1/train_code_bpe_tokenizer.py`

Why it exists:

- A coding model needs structured code understanding, not only raw text.
- The tokenizer must be efficient for source code, logs, compiler errors,
  indentation, hex values, and low-level memory patterns.

Main features:

- Walks repositories and extracts AST/callgraph style metadata.
- Builds JSONL structural records for later memory/training pipelines.
- Trains a code-optimized BPE tokenizer with special control tokens.
- Preserves code syntax, indentation, and common source-code byte patterns.

How it works:

- The callgraph extractor reads source files and emits dense structural
  metadata.
- The tokenizer trainer uses Hugging Face `tokenizers` and code/log focused
  pre-tokenization.

When to use:

- Before pretraining/SFT (Supervised Fine Tuning) data preparation.
- When indexing new repositories for code understanding.

Outputs:

- AST/callgraph JSONL artifacts.
- Tokenizer artifacts under `artifacts/`.

#### Phase 2: Docker Sandbox Engine

Location:

- `src/phase2/docker_sandbox_engine.py`

Why it exists:

- AI-generated code must run in an isolated environment.
- Compilation/runtime telemetry is required for self-healing and verification.

Main features:

- Async Docker orchestration.
- Network isolation.
- CPU/memory limits.
- Read-only filesystem and tmpfs writable scratch area.
- Non-root UID/GID.
- Timeout and cleanup handling.

How it works:

- Accepts source files, compile command, run command, image, and environment.
- Creates hardened containers, executes the request, captures stdout/stderr,
  exit code, timeout flag, and metrics.

When to use:

- Verifying generated code.
- Running unit tests safely.
- Feeding runtime errors back into self-healing.

Outputs:

- Structured sandbox execution result.

#### Phase 3: Rejection Sampling Pipeline

Location:

- `src/phase3/rejection_sampling_pipeline.py`

Why it exists:

- SFT data should include only verified high-quality model generations.
- Bad synthetic examples can damage the model.

Main features:

- Prompts a base model with code and structural context.
- Parses model reasoning and patch output.
- Runs the patch through the sandbox/test gate.
- Writes accepted rows only when first-pass verification succeeds.

How it works:

- The model proposes reasoning and a patch.
- The sandbox verifies compilation/tests.
- Accepted rows are saved as JSONL SFT examples.

When to use:

- Later, when generating training data from model attempts.

Outputs:

- Verified JSONL SFT candidate dataset.

#### Phase 4: Dynamic Swarm Orchestrator

Location:

- `src/phase4/swarm_orchestrator.py`

Why it exists:

- Complex coding tasks need multiple roles, not one flat assistant response.

Main features:

- Async master orchestrator.
- Pydantic schemas for agent messages.
- Role-specific micro-agents.
- Peer-review and correction loop.

How it works:

- The master splits a task into subtasks.
- Micro-agents produce artifacts.
- Reviewer agents score and request correction if confidence is low.

When to use:

- Multi-step coding, debugging, research, and security workflows.

Outputs:

- Validated agent artifacts and orchestration report.

#### Phase 5: Self-Healing Runtime

Location:

- `src/phase5/self_healing_runtime.py`

Why it exists:

- Agentic code often fails on first execution.
- The system needs a controlled loop for error capture, repair, and training
  trace generation.

Main features:

- Crash/error telemetry capture.
- Recursive repair loop with max depth.
- Lifecycle trace capture.
- Async staging buffer for future fine-tuning data.

How it works:

- Sandbox failure logs are injected back into the reasoning context.
- The model attempts a corrected patch.
- Successful repair traces are staged for later training review.

When to use:

- Runtime failures, compilation errors, or failing tests.

Outputs:

- Self-healing trace and optional database/vector staging records.

#### Phase 6: Redis Regenerative Quota Engine

Location:

- `src/phase6/redis_quota_engine.py`
- `src/phase6/redis_regenerative_bucket.lua`

Why it exists:

- The UI/API needs scalable usage limits without fixed reset windows.

Main features:

- Redis-backed continuous token bucket.
- Atomic Lua update.
- Floating point refill rate.
- FastAPI-style middleware contract.

How it works:

- On each request, Redis calculates elapsed time, refills tokens up to capacity,
  checks cost, decrements if allowed, and returns remaining balance.

When to use:

- API quota, message limits, premium usage plans.

Outputs:

- Allowed/denied decision and remaining token balance.

#### Phase 7: GRPO Training Loop

Location:

- `src/phase7/grpo_training_loop.py`

Why it exists:

- Coding/security model optimization needs outcome-based policy training.

Main features:

- GRPO-style grouped response scoring.
- Reward components for execution, security, format, and efficiency.
- KL penalty against reference model.
- DeepSpeed/TRL-oriented training structure.

How it works:

- Generate multiple candidates per prompt.
- Score each candidate.
- Normalize advantages within the group.
- Apply clipped policy loss and KL regularization.

When to use:

- Later on Linux CUDA hardware after SFT data and verifier rewards are ready.

Outputs:

- Training checkpoints and metrics.

#### Phase 8: Serving Stack

Location:

- `src/phase8/vllm_server.py`
- `src/phase8/gateway.py`
- `src/phase8/quantize_awq.py`
- `deploy/phase8/docker-compose.yml`
- `deploy/phase8/nginx.conf`

Why it exists:

- Production inference needs batching, routing, streaming, and gateway safety.

Main features:

- vLLM launch wrapper.
- FastAPI gateway with routing.
- SSE streaming support.
- AWQ quantization pipeline scaffold.
- Nginx/Docker Compose deployment assets.

How it works:

- vLLM serves the model.
- Gateway chooses generation settings by request type.
- Nginx fronts the service in production deployment.

When to use:

- Model serving and UI/API integration.

Outputs:

- OpenAI-compatible inference endpoint and gateway responses.

#### Phase 9: Evaluation Harness

Location:

- `src/phase9/evaluate.py`
- `src/phase9/benchmarks.py`
- `src/phase9/security_suite.py`
- `src/phase9/regression_tracker.py`
- `src/phase9/head_to_head.py`

Why it exists:

- Model progress must be measured after every training/architecture change.

Main features:

- HumanEval/MBPP/CyberSecEval-style runner structure.
- Custom security suite.
- Regression tracking.
- Head-to-head model comparison.

How it works:

- Runs prompts through model clients.
- Executes code safely through sandbox adapter.
- Stores benchmark scores and regression deltas.

When to use:

- After SFT/GRPO runs, model swaps, or major agent changes.

Outputs:

- Evaluation reports and regression records.

#### Phase 10: Deployment Smoke And Readiness Audit

Location:

- `src/phase10/e2e_smoke_runner.py`
- `src/phase10/architecture_readiness_audit.py`
- `src/phase10/mock_vllm_server.py`

Why it exists:

- The whole system needs a single readiness answer.

Main features:

- End-to-end smoke test.
- Deep architecture audit.
- Mock vLLM server for local development.
- Readiness scoring and markdown/json reports.

How it works:

- Checks phase files, packages, GPU status, smoke tests, sandbox, gateway,
  quota, database schemas, static security, docs, and phase readiness.

When to use:

- Daily local validation.
- Before saying the architecture is healthy.

Outputs:

- `artifacts/phase10/*readiness*.json`
- `artifacts/phase10/*readiness*.md`

#### Phase 11: PyTorch AI Core And Chat Interface

Location:

- `src/phase11/chat_api.py`
- `src/phase11/agentic_runtime.py`
- `src/phase11/model_backends.py`
- `src/phase11/memory_engine.py`
- `src/phase11/security_engine.py`
- `src/phase11/pytorch_model.py`
- `src/phase11/static/*`

Why it exists:

- This is the usable local AI interface and unified backend abstraction.

Main features:

- FastAPI chat/agent API.
- Static chat UI.
- ModelBackend abstraction for mock, PyTorch, and OpenAI-compatible backends.
- Workspace memory retrieval.
- Tool registry.
- Security analyzer.
- Agentic coding runtime.

How it works:

- User sends chat or agent request.
- Backend generates a response.
- Agent runtime can retrieve context, plan, run tools/sandbox, review, and
  produce final answers.

When to use:

- Daily chat/API testing.
- Agent workflow development.

Outputs:

- API responses.
- Runtime reports.
- UI at `http://127.0.0.1:8090`.

#### Phase 12: Capability Gauntlet

Location:

- `src/phase12/capability_gauntlet.py`

Why it exists:

- Before training, we need architecture-level tasks that test the system's
  ability to expand short prompts, use memory, reason about security, and route
  tools.

Main features:

- Golden tasks across short prompt, agent workflow, security, patch review,
  long context, tool safety, and self-healing.
- Quick and full suite modes.

How it works:

- Generates synthetic but structured tasks.
- Runs local architecture components and scores categories.

When to use:

- Local architecture regression testing.

Outputs:

- `artifacts/phase12/*.json`
- `artifacts/phase12/*.md`

#### Phase 13: Backend Quality Harness

Location:

- `src/phase13/backend_quality_harness.py`

Why it exists:

- Architecture plumbing and real model quality are different.
- This phase compares backend behavior against quality expectations.

Main features:

- Candidate/baseline comparison.
- Category quality scoring.
- Recommendations for weak response areas.

How it works:

- Sends standard prompts to a backend.
- Scores content, structure, safety, and completeness.

When to use:

- When connecting a new model endpoint.

Outputs:

- `artifacts/phase13/*.json`
- `artifacts/phase13/*.md`

#### Phase 14: Exact Scorecard Harness

Location:

- `src/phase14/scorecard_harness.py`

Why it exists:

- The user requested exact scorecard metrics for architecture and model quality.

Main features:

- 20 short prompt coding tasks.
- 20 debugging tasks.
- 20 security finding tasks.
- 20 patch generation tasks.
- 10 long-context repo tasks.
- Metrics for architecture reliability, agent workflow, security, short prompt,
  sandbox pass rate, and regressions.

How it works:

- Runs mock and/or real backend mode.
- Scores each task and writes failure reports.

When to use:

- Baseline model comparison and architecture scorecard runs.

Outputs:

- `artifacts/scorecard/scorecard-local.json`

#### Phase 15: Target Architecture Blueprint

Location:

- `src/phase15/target_architecture.py`
- `docs/mythos_target_architecture.md`

Why it exists:

- The long-term vision needs a living blueprint.

Main features:

- Cursor + Claude Code + DeepSeek R1 style target architecture.
- Seven priority pillars.
- Current status, gaps, immediate build order.

How it works:

- Stores and emits machine-readable architecture state.

When to use:

- Strategy and roadmap updates.

Outputs:

- `artifacts/phase15/mythos-target-architecture.json`
- `artifacts/phase15/mythos-target-architecture.md`

#### Phase 16: Core Architecture Upgrades

Location:

- `src/phase16/task_graph.py`
- `src/phase16/role_agents.py`
- `src/phase16/critic_verifier.py`
- `src/phase16/experience_memory.py`
- `src/phase16/tool_adapters.py`
- `src/phase16/sft_exporter.py`
- `src/phase16/base_model_connector.py`

Why it exists:

- Adds durable planning, role agents, critic/verifier, experience memory, and
  defensive tools.

Main features:

- TaskGraph DAG planner.
- Role-specific agents.
- Heuristic/model critic.
- Composite verifier.
- Git/Semgrep/CodeQL/browser adapters.
- Scorecard-to-training export.

How it works:

- Plans as a graph, executes role agents, verifies outputs, and promotes
  failures to memory/training candidates.

When to use:

- Core agent architecture smoke and real-backend checks.

Outputs:

- `artifacts/phase16/phase16-smoke*.json`
- `artifacts/phase16/experience_memory.jsonl`

#### Phase 17: GPU 7B-32B Readiness

Location:

- `src/phase17/gpu_readiness.py`
- `src/phase17/qlora_sft_training.py`
- `deploy/gpu/*`

Why it exists:

- We should not wait for GPU hardware to prepare the training/serving contract.

Main features:

- Qwen/DeepSeek model profiles.
- vLLM launch scripts.
- QLoRA SFT script.
- GRPO launch script.
- DeepSpeed/Accelerate configs.

How it works:

- Validates that GPU-target files are present and CUDA status is known.

When to use:

- Before moving to Linux CUDA machine.

Outputs:

- `artifacts/phase17/gpu-readiness.json`

#### Phase 18: Real Model Quality Loop

Location:

- `src/phase18/model_quality_loop.py`

Why it exists:

- Real model behavior must be scored and converted into reviewed improvement
  candidates.

Main features:

- Runs scorecard against local Qwen or future vLLM endpoint.
- Clusters failures by category, phase, and issue type.
- Exports SFT/GRPO review-required candidates.

How it works:

- Uses Phase 14 exact scorecard logic.
- Failing/warning generations become candidate rows, not train-ready rows.

When to use:

- After changing model endpoint or architecture.

Outputs:

- `artifacts/phase18/model-quality-latest.json`

#### Phase 19: Unified Verifier Registry

Location:

- `src/phase19/verifier_registry.py`

Why it exists:

- Code acceptance needs one consistent verifier surface.

Main features:

- Rule-based security.
- Secret scan.
- Optional Semgrep.
- Optional CodeQL.
- Optional sandbox/test command.
- Fixture-aware exclusions.

How it works:

- Runs configured checks and normalizes findings into one report.

When to use:

- After generated patches or before accepting architecture code changes.

Outputs:

- `artifacts/phase19/verifier-latest.json`

#### Phase 20: TaskGraph Runtime

Location:

- `src/phase20/taskgraph_runtime.py`

Why it exists:

- TaskGraph planning must be executable, not only a schema.

Main features:

- Durable TaskGraph execution.
- RoleAgent orchestration.
- Critic review.
- Optional verifier execution.

How it works:

- Builds a graph, runs role agents layer by layer, critic-reviews artifacts,
  and optionally verifies the workspace.

When to use:

- Complex prompts that need planner/agents/reviewer flow.

Outputs:

- `artifacts/phase20/taskgraph-runtime-latest.json`

#### Phase 21: Experience Promotion

Location:

- `src/phase21/experience_promotion.py`

Why it exists:

- Failures should become searchable experience memory.

Main features:

- Promotes Phase 18/19/20 reports.
- Writes JSONL memory.
- Optional Redis/Postgres/Qdrant sinks.

How it works:

- Converts failures, verifier findings, and runtime outcomes into
  failure/fix/outcome records.

When to use:

- After scorecard/verifier/agent runs.

Outputs:

- `artifacts/phase21/experience_memory.jsonl`
- `artifacts/phase21/experience-promotion-latest.json`

#### Phase 22: Critic Service

Location:

- `src/phase22/critic_service.py`

Why it exists:

- The critic should be swappable: heuristic today, real model later.

Main features:

- Heuristic critic mode.
- OpenAI-compatible model critic mode.
- Artifact quality scoring.

How it works:

- Reviews artifact for depth, verification, security, and risky primitives.

When to use:

- Before accepting generated plans/patches.

Outputs:

- `artifacts/phase22/critic-latest.json`

#### Phase 23: Model Quality Profiles

Location:

- `src/phase23/model_quality_profiles.py`

Why it exists:

- Future 7B/14B/32B scorecard runs should be profile-driven.

Main features:

- Loads GPU model profiles.
- Builds Phase 18 quality-run commands.
- Supports dry-run and execute modes.

How it works:

- Selects a profile and emits the exact quality command for that model.

When to use:

- On future vLLM/GPU endpoints.

Outputs:

- `artifacts/phase23/quality-profile-latest.json`

#### Phase 24: Main Agent V2

Location:

- `src/phase24/main_agent_v2.py`

Why it exists:

- Normal agent workflows should use TaskGraph, experience memory, verifier,
  and critic together.

Main features:

- TaskGraph-first main agent bridge.
- Injects Phase 25 experience memory.
- Runs Phase 20 runtime.
- Supports verifier, Semgrep, sandbox, and model critic flags.

How it works:

- Retrieves related experience.
- Enriches the prompt.
- Executes TaskGraph runtime.
- Writes a consolidated report.

When to use:

- The preferred future path for `/v1/agent/run` style workflows.

Outputs:

- `artifacts/phase24/main-agent-v2-latest.json`

#### Phase 25: Experience Retrieval

Location:

- `src/phase25/experience_retrieval.py`

Why it exists:

- The system should avoid repeating past mistakes.

Main features:

- Searches Phase 21 and Phase 16 experience JSONL stores.
- Renders compact planner context.

How it works:

- Token-matches query terms against failure/fix/outcome/tag text.

When to use:

- Before planning a new task.

Outputs:

- `artifacts/phase25/experience-retrieval-latest.json`

#### Phase 26: Patch Manager

Location:

- `src/phase26/patch_manager.py`

Why it exists:

- Generated patches need preview, backup, and rollback.

Main features:

- Safe path validation.
- Unified diff preview.
- Apply mode.
- Backup manifest.
- Rollback mode.

How it works:

- Reads proposed patch JSON.
- Resolves paths inside workspace.
- Generates diff.
- Applies only if explicitly requested.

When to use:

- Before writing model-generated code changes.

Outputs:

- `artifacts/phase26/patch-manager-latest.json`

#### Phase 27: Verifier Policy Engine

Location:

- `src/phase27/verifier_policy_engine.py`
- `config/verifier_policy.json`

Why it exists:

- Different workflows need different verifier strictness.

Main features:

- `fast` profile.
- `security` profile.
- `release` profile.
- `codeql-python` profile.

How it works:

- Loads policy profile and runs Phase 19 with those settings.

When to use:

- Fast local checks, deeper security checks, and release gates.

Outputs:

- `artifacts/phase27/verifier-latest.json`

#### Phase 28: Security Expert Workflow

Location:

- `src/phase28/security_expert_workflow.py`

Why it exists:

- Cybersecurity behavior should be a dedicated defensive workflow.

Main features:

- Verifier-backed vulnerability triage.
- Safe patch guidance.
- Defensive-only safety position.

How it works:

- Runs the verifier registry and converts findings into patch guidance.

When to use:

- Security review and vulnerability fixing tasks.

Outputs:

- `artifacts/phase28/security-workflow-latest.json`

#### Phase 29: Dataset Review Gate

Location:

- `src/phase29/dataset_review_gate.py`

Why it exists:

- Training data must be reviewed before it affects the model.

Main features:

- Reads SFT/GRPO candidates.
- Classifies rows as rejected, needs human review, or train-ready.
- Writes only approved rows to train-ready JSONL.

How it works:

- Uses metadata review status, row length, and optional verifier approval.

When to use:

- Before any SFT/GRPO training export.

Outputs:

- `artifacts/phase29/dataset-review-latest.json`
- `artifacts/phase29/*train-ready.jsonl`

#### Phase 30: Expanded Golden Benchmark

Location:

- `src/phase30/expanded_benchmark_suite.py`

Why it exists:

- The original scorecard is useful, but stronger architecture needs more tasks.

Main features:

- Generates 450 tasks:
  - 100 short prompt coding.
  - 100 debugging.
  - 100 security finding.
  - 100 patch generation.
  - 50 long-context repo tasks.

How it works:

- Creates structured JSONL benchmark cases with expected paths/CWEs/patches.

When to use:

- Building a larger regression suite for future model quality.

Outputs:

- `artifacts/phase30/expanded-benchmark-latest.json`
- `artifacts/phase30/*.jsonl`

#### Phase 31: Long Context Packer

Location:

- `src/phase31/long_context_packer.py`

Why it exists:

- Large repository tasks need packed context from code and experience memory.

Main features:

- Workspace memory retrieval.
- Experience memory insertion.
- Token-budgeted context block.

How it works:

- Retrieves relevant files and experience records, renders sections, and trims
  to budget.

When to use:

- Before asking a model to solve large repo tasks.

Outputs:

- `artifacts/phase31/long-context-latest.json`

#### Phase 32: Critic Endpoint Contract

Location:

- `src/phase32/critic_endpoint_contract.py`

Why it exists:

- A future dedicated critic model must satisfy a simple contract before use.

Main features:

- Probes OpenAI-compatible endpoint.
- Runs model-backed critic review.
- Captures success/error report.

How it works:

- Builds a model backend and asks it to review a safe coding artifact.

When to use:

- Before switching Phase 22 or Phase 24 to model critic mode.

Outputs:

- `artifacts/phase32/critic-contract-latest.json`

#### Phase 33: GPU Backend Contract

Location:

- `src/phase33/gpu_backend_contract.py`

Why it exists:

- Future GPU/vLLM runs should be prepared and validated before hardware arrives.

Main features:

- Loads model profiles.
- Validates required 7B/14B/32B profile presence.
- Builds a Phase 23 quality plan.

How it works:

- Uses `deploy/gpu/model_profiles.json` and the Phase 23 launcher contract.

When to use:

- Before running real 7B/14B/32B quality tests on vLLM.

Outputs:

- `artifacts/phase33/gpu-backend-contract-latest.json`

#### Phase 34: Auth And Quota Control

Location:

- `src/phase34/auth_quota.py`
- integrated into `src/phase11/chat_api.py`

Why it exists:

- Without auth, anyone who can reach the API can use the AI system.
- Auth must connect to quota so usage is tracked per authenticated user.

Main features:

- HS256 JWT generation and verification using stdlib HMAC/SHA256.
- Middleware protecting `/v1/*` when `PHASE34_AUTH_ENABLED=1`.
- Health, static UI, logo, `/metrics`, and token helper are exempt.
- Optional Phase 6 Redis quota consumption per authenticated user.

How it works:

- Request arrives with `Authorization: Bearer <jwt>`.
- Middleware verifies issuer, audience, expiry, not-before, signature, and
  subject.
- If quota is enabled, request cost is estimated and Phase 6 Redis token bucket
  is consumed.
- Quota headers are added to responses.

When to use:

- Before exposing the API beyond local-only development.

Outputs:

- Auth response headers.
- Quota response headers.

#### Phase 35: Observability

Location:

- `src/phase35/observability.py`
- integrated into `src/phase11/chat_api.py`

Why it exists:

- A powerful agent system needs live visibility into which phases are being
  exercised, latency, status codes, and errors.

Main features:

- Prometheus-compatible `/metrics`.
- Structured JSONL request logs.
- Phase labels based on route prefixes.

How it works:

- Middleware records every request status/duration.
- Metrics are stored in an in-process registry.
- Logs are appended to `artifacts/phase35/api-events.jsonl`.

When to use:

- During local debugging and production monitoring.

Outputs:

- `GET /metrics`
- `artifacts/phase35/api-events.jsonl`

#### Phase 36: Data Flywheel

Location:

- `src/phase36/data_flywheel.py`

Why it exists:

- Model failures should not just sit in reports. They should move into a
  reviewed improvement loop.

Main features:

- Reads Phase 18 real-model failure/candidate reports.
- Writes a Phase 3 rejection-sampling queue.
- Runs Phase 29 dataset review gate.
- Prepares a Phase 7 GRPO trigger manifest.

How it works:

- Phase 18 SFT candidates are transformed into Phase 3 queue rows.
- Phase 29 marks rows as train-ready only when approved.
- If train-ready rows exist, Phase 36 prepares the Phase 7 command.
- It does not blindly execute training on unreviewed data.

When to use:

- After Phase 18 real scorecard runs.
- Before training loops on GPU hardware.

Outputs:

- `artifacts/phase36/*phase3-rejection-queue.jsonl`
- `artifacts/phase36/*phase7-trigger.json`
- `artifacts/phase36/data-flywheel-latest.json`

#### Phase 37: Production Vector Memory

Why it exists:

- Phase 25 token matching is useful locally but gets noisy as experience memory grows.
- The planner needs vector-ranked failure/fix/outcome recall before every hard task.
- Production memory should support Qdrant/pgvector while still running on a CPU dev machine.

How it works:

- Loads Phase 16/21 experience records.
- Converts each record into a deterministic vector memory row.
- Writes a local JSONL vector index.
- Optionally mirrors records into Qdrant/Postgres through the shared persistent memory gateway.
- Returns a planner-ready context block.

When to use:

- After Phase 21 or Phase 36 promotes new failure/fix/outcome records.
- Before large agentic coding, debugging, or security workflows.

Outputs:

- `artifacts/phase37/vector-memory-index.jsonl`
- `artifacts/phase37/vector-memory-latest.json`

#### Phase 38: Multi-Language Security Engine

Why it exists:

- The target AI must handle more than Python/C/C++.
- Rust, Go, JavaScript/TypeScript, and Solidity need explicit defensive rules.
- Security scanning must stay defensive: detection, patch guidance, verification.

How it works:

- Detects language by file extension.
- Applies base rules plus language-specific rules.
- Suppresses benchmark fixtures by default.
- Produces safe patch guidance and verification recommendations.

When to use:

- After meaningful code changes.
- Before accepting generated patches for Rust, Go, JS/TS, or Solidity repositories.

Outputs:

- `artifacts/phase38/multilang-security-latest.json`

#### Phase 39: Training Checkpoint Rollback Gate

Why it exists:

- SFT/GRPO can improve some metrics while silently damaging others.
- A checkpoint should not become active just because training completed.
- Promotion must depend on evaluation and rollback must be easy.

How it works:

- Reads candidate and baseline evaluation reports.
- Compares required metrics such as overall score, pass@1, and security score.
- Rejects or rolls back when metric drop exceeds the allowed threshold.
- Promotes only when the candidate passes the degradation gate.
- Writes active checkpoint pointers and rollback manifests.

When to use:

- Immediately after Phase 7/17 training and Phase 9/18/23 evaluation.
- Before deploying or serving a new trained checkpoint.

Outputs:

- `artifacts/phase39/checkpoint-gate-latest.json`
- `artifacts/phase39/active_checkpoint.json`
- `artifacts/phase39/registry/*rollback-manifest.json`

#### Phase 40: Integrated Default Agent Runtime

Why it exists:

- The architecture had strong components, but the default agent path still needed to use all of them together.
- Short prompts should automatically trigger memory, planning, critic, verifier, and security checks.

How it works:

- Classifies the prompt intent.
- Retrieves Phase 37 vector memory.
- Enriches the prompt with relevant failure/fix/outcome memories.
- Runs Phase 24 Main Agent V2 and TaskGraph execution.
- Runs Phase 22 critic.
- Runs Phase 27 verifier policy.
- Runs Phase 38 multi-language security scan for coding/security/debug/release tasks.
- Promotes failures into experience memory.
- On `tiny-llama-cpu-smoke` and `qwen-cpu-smoke`, Phase 40 uses mock orchestration by default so local
  agent calls stay responsive; GPU profiles use active model orchestration.

When to use:

- For serious agentic coding, debugging, and security work.
- This is now the default `/v1/agent/run` path.

Outputs:

- `artifacts/phase40/integrated-agent-latest.json`
- `artifacts/phase40/failure-events.jsonl`

#### Phase 41: Real Task Regression Pack

Why it exists:

- Architecture changes and model changes need a larger local regression gate.
- The system must catch short-prompt, debugging, security, repo-scale, and patch regressions.

How it works:

- Generates 400 deterministic tasks.
- Stores prompts, files, expected signals, expected paths, and CWE tags.
- Runs a smoke scorer to validate the pack contract.

Task distribution:

- 100 short prompt coding tasks.
- 100 debugging tasks.
- 100 security finding tasks.
- 50 multi-file repo tasks.
- 50 patch verification tasks.

Outputs:

- `artifacts/phase41/regression-pack.jsonl`
- `artifacts/phase41/regression-pack-latest.json`

#### Phase 42: Production Profile Switcher

Why it exists:

- Local CPU smoke, mock testing, and future GPU/vLLM profiles should use one model/backend contract.
- Manual env var switching creates mistakes.

How it works:

- Lists local and GPU model profiles.
- Activates a selected profile.
- Writes config for Phase 11, Phase 24, Phase 40, and scorecard runners.
- Marks GPU profiles as waiting for CUDA when this machine cannot run them.

Outputs:

- `config/active_model_profile.json`
- `config/active_model_profile.ps1`
- `artifacts/phase42/profile-switch-latest.json`

#### Phase 43: Meta Planner

Location:

- `src/phase43/meta_planner.py`

Why it exists:

- TaskGraph is executable, but it needs a deliberate layer before graph creation.
- Tiny or broad goals should become requirements, architecture components,
  execution lanes, risks, and a TaskGraph-ready brief.

How it works:

- Calls Phase 44 intent expansion.
- Builds architecture components and execution lanes.
- Produces a planner brief that Phase 40 now injects before Main Agent V2.

Outputs:

- `artifacts/phase43/meta-planner-latest.json`

#### Phase 44: Intent Expansion Engine

Location:

- `src/phase44/intent_expansion.py`

Why it exists:

- The user can give a tiny prompt and the system must infer the missing
  requirements internally.

How it works:

- Detects common domains such as login, streaming, rideshare, commerce, and API.
- Expands the prompt into functional requirements, security requirements, data
  entities, user roles, acceptance tests, and assumptions.

Outputs:

- `artifacts/phase44/intent-expansion-latest.json`

#### Phase 45: Parallel Agent Runtime

Location:

- `src/phase45/parallel_agent_runtime.py`

Why it exists:

- Sequential agent flow wastes time and hides specialist disagreement.
- Architect, coder, tester, security, researcher, and reviewer lanes should run
  concurrently when dependencies allow.

How it works:

- Uses Phase 43 meta-plan lanes.
- Runs Phase 16 role agents in dependency groups with `asyncio.gather`.
- Detects simple cross-role conflicts and writes a synthesis report.

Outputs:

- `artifacts/phase45/parallel-agent-latest.json`

#### Phase 46: Hierarchical Memory

Location:

- `src/phase46/hierarchical_memory.py`

Why it exists:

- Vector experience memory is useful, but the larger architecture needs layered
  recall: working, session, project, experience, and knowledge memory.

How it works:

- Stores each layer as local JSONL.
- Searches across layers with deterministic token overlap and layer weighting.
- Renders planner-ready context.

Outputs:

- `artifacts/phase46/hierarchical-memory-latest.json`
- `artifacts/phase46/{working,session,project,experience,knowledge}.jsonl`

#### Phase 47: Reasoning Engine

Location:

- `src/phase47/reasoning_engine.py`

Why it exists:

- Reasoning should not be only whatever the base model decides to do.
- Thinker, critic, and verifier stages need separate scores and findings.

How it works:

- Thinker creates a Phase 43 plan.
- Critic checks coverage.
- Verifier checks acceptance and safety concerns.
- The report is accepted only when critic and verifier thresholds pass.

Outputs:

- `artifacts/phase47/reasoning-engine-latest.json`

#### Phase 48: Knowledge Graph

Location:

- `src/phase48/knowledge_graph.py`

Why it exists:

- Vector memory is not enough for relationships between phases, dependencies,
  patterns, bugs, fixes, and technologies.

How it works:

- Stores graph nodes and edges in JSON.
- Seeds the Phase 43-50 relationship map.
- Returns graph context for planner prompts.

Outputs:

- `artifacts/phase48/knowledge-graph.json`
- `artifacts/phase48/knowledge-graph-latest.json`

#### Phase 49: Multimodal Expert

Location:

- `src/phase49/multimodal_expert.py`

Why it exists:

- Future users will provide images, PDFs, diagrams, screenshots, and repository
  folders.

How it works:

- Creates a safe local metadata contract for media and folders.
- Marks artifacts that need future OCR, vision, PDF, or diagram extraction.
- Renders planner context without pretending to understand pixels yet.

Outputs:

- `artifacts/phase49/multimodal-expert-latest.json`

#### Phase 50: MoE Router Layer

Location:

- `src/phase50/moe_router.py`

Why it exists:

- Different tasks should route to different experts before execution: planning,
  coding, security, reasoning, memory, multimodal, and research.

How it works:

- Scores prompt keywords against expert routes.
- Returns the top expert routes and the recommended execution phase.

Outputs:

- `artifacts/phase50/moe-router-latest.json`

#### How The Pieces Work Together

Typical coding request path:

1. User sends a short or complex coding prompt.
2. Phase 50 can route to the right expert family.
3. Phase 44 expands tiny prompts into explicit requirements.
4. Phase 43 builds goal, architecture, execution lanes, risks, and TaskGraph brief.
5. Phase 40 injects the meta-plan and retrieves Phase 37 vector memory.
6. Phase 24 Main Agent V2 retrieves related memory through Phase 25 and runs Phase 20 TaskGraph.
7. Phase 45 can run specialist lanes in parallel for larger workflows.
8. Phase 22 critic or Phase 47 thinker/critic/verifier reviews the artifact.
9. Phase 27 verifier policy checks code/security/sandbox policy.
10. Phase 38 runs multi-language security where relevant.
11. Phase 26 can preview/apply/rollback patches.
12. Phase 21 promotes failures/outcomes into experience memory.
13. Phase 18/14/30/41 evaluate model/architecture quality.
14. Phase 29 gates any generated training rows.
15. Phase 36 queues reviewed failures for rejection sampling and future GRPO.

Typical defensive security path:

1. Phase 28 runs verifier-backed security review.
2. Phase 38 runs language-specific Rust/Go/JS/Solidity checks when relevant.
3. Phase 19 runs rule, secret, Semgrep, CodeQL, and/or sandbox checks.
4. Findings become safe patch guidance.
5. Phase 26 previews/applies patch.
6. Phase 27 release policy validates.
7. Phase 21 stores failure/fix/outcome memory.

Typical future GPU model path:

1. Phase 42 activates the desired runtime profile.
2. Phase 17 validates training/serving assets.
3. Phase 33 validates profile contract.
4. Phase 23 builds a quality-run command.
5. Phase 18 runs the real scorecard against vLLM.
6. Phase 41 regression pack compares behavior before/after changes.
7. Phase 29 gates candidate data.
8. Phase 7 trains with SFT/GRPO later on Linux CUDA hardware.
9. Phase 39 blocks degraded checkpoints and promotes only passing candidates.

#### Important Commands

Daily readiness:

```powershell
python src\phase10\architecture_readiness_audit.py --run-id topclass-readiness --output-dir artifacts\phase10
```

Fast verifier:

```powershell
python src\phase27\verifier_policy_engine.py --profile fast --workspace . --json
```

Main Agent V2 smoke:

```powershell
$env:PHASE24_BACKEND="mock"
python src\phase24\main_agent_v2.py --prompt "debug architecture end to end safely" --workspace . --json
```

Real Qwen critic endpoint contract:

```powershell
python src\phase32\critic_endpoint_contract.py --endpoint http://127.0.0.1:8016/v1 --model Qwen/Qwen2.5-Coder-0.5B-Instruct
```

Expanded benchmark generation:

```powershell
python src\phase30\expanded_benchmark_suite.py
```

Vector memory rebuild:

```powershell
python src\phase37\vector_memory.py --rebuild --query "security patch verifier failure"
```

Multi-language security scan:

```powershell
python src\phase38\multilang_security.py --workspace .
```

Checkpoint promotion dry-run:

```powershell
python src\phase39\checkpoint_rollback.py --candidate-checkpoint . --candidate-report artifacts\phase10\phase34-36-post-api-final.json --baseline-report artifacts\phase10\phase34-36-post-api-final.json --required-metrics overall_score --dry-run
```

Integrated default agent:

```powershell
python src\phase40\integrated_agent.py --prompt "build a secure login api with tests" --workspace .
```

Meta planner:

```powershell
python src\phase43\meta_planner.py --prompt "build netflix"
```

Intent expansion:

```powershell
python src\phase44\intent_expansion.py --prompt "login system"
```

Parallel agents:

```powershell
python src\phase45\parallel_agent_runtime.py --prompt "build secure login system" --backend-mode mock
```

Hierarchical memory:

```powershell
python src\phase46\hierarchical_memory.py --query "secure planner verifier" --seed
```

Reasoning engine:

```powershell
python src\phase47\reasoning_engine.py --prompt "debug architecture end to end"
```

Knowledge graph:

```powershell
python src\phase48\knowledge_graph.py --query "meta planner memory reasoning" --seed
```

Multimodal expert:

```powershell
python src\phase49\multimodal_expert.py --prompt "analyze attached assets" --path .
```

MoE router:

```powershell
python src\phase50\moe_router.py --prompt "build secure login system"
```

Phase 43-50 end-to-end contract test:

```powershell
python src\phase50\phase43_to_50_e2e.py --prompt "build secure login system"
```

Regression pack:

```powershell
python src\phase41\regression_pack.py --smoke-limit 25
```

Activate model profile:

```powershell
python src\phase42\profile_switcher.py --profile tiny-llama-cpu-smoke --activate
```

#### Latest Debug Status

The latest full debug pass checked:

- Full `compileall` over `src`.
- Full Bandit scan over `src`.
- Phase 11 smoke test.
- Phase 16 real-backend smoke.
- Phase 17 GPU readiness.
- Phase 18 model quality dry-run.
- Phase 19 verifier.
- Phase 20 TaskGraph runtime.
- Phase 21 experience promotion.
- Phase 24 Main Agent V2.
- Phase 27 verifier policy.
- Phase 28 security workflow.
- Phase 29 dataset review gate.
- Phase 30 expanded benchmark generation.
- Phase 31 long-context packer.
- Phase 32 real Qwen critic endpoint contract.
- Phase 33 GPU backend contract.
- Phase 34 JWT auth/quota module.
- Phase 35 metrics/logging module.
- Phase 36 data flywheel module.
- Phase 37 vector experience memory module.
- Phase 38 multi-language defensive security module.
- Phase 39 checkpoint rollback gate module.
- Phase 40 integrated default agent module.
- Phase 41 400-task regression pack module.
- Phase 42 production profile switcher module.
- Phase 43 meta planner module.
- Phase 44 intent expansion module.
- Phase 45 parallel agent runtime module.
- Phase 46 hierarchical memory module.
- Phase 47 reasoning engine module.
- Phase 48 knowledge graph module.
- Phase 49 multimodal expert contract.
- Phase 50 MoE router module.
- Phase 43-50 end-to-end contract test.
- Phase 51 high-stability reasoning and unified memory module.
- Phase 10 architecture readiness audit.

Known remaining constraints:

- This Windows CPU machine has no CUDA runtime.
- `deepspeed` and `autoawq` remain Linux CUDA path items.
- The workspace is now initialized as a local Git repository; full remote sync,
  branch policy, and commit discipline are still needed for production work.
- Current stable live model is a pinned tiny CPU HF smoke backend, not the final 7B-32B or
  50B-100B target.

#### Phase 51: High-Stability Reasoning And Unified Memory

Location:

- `src/phase51/high_stability_reasoning_memory.py`
- `docs/phase51_high_stability_reasoning_memory.md`
- `scripts/run_phase51_high_stability.ps1`

Why it exists:

- The system needs a stricter backend contract than generic free-text agent
  wrappers.
- Planner, Executor, Critic, and Verifier roles must never mix.
- Durable memory must reject raw thoughts, failed guesses, and temporary
  outputs.
- Retrieval needs a fixed mathematical ranking formula to reduce context
  pollution over time.

Main features:

- `ReasoningEngine` runs `Think -> Execute -> Reflect -> Revise`.
- Planner emits only schema-valid task graphs.
- Executor follows the graph and cannot alter the plan.
- Critic only highlights flaws and calculates confidence.
- Verifier checks schema, facts, formatting, and success criteria.
- Critic confidence `< 0.6` forces reflection with the four required prompts.
- `UnifiedMemoryManager` provides working, project, experience, and knowledge
  graph memory.
- Ingestion gate allows only verified fixes, passed benchmarks, security
  findings, and successful plans.
- Retrieval uses:
  `0.4 * vector_similarity + 0.3 * success_rate + 0.2 * recency + 0.1 * usage`.
- Phase 40 now calls Phase 51 as an active strict-stability guardrail, so this
  is not a disconnected standalone layer.

When to use:

- For the strictest agentic backend path.
- Before adding long-term memory entries.
- When testing whether the architecture is resisting role mixing and memory
  pollution.

Command:

```powershell
.\scripts\run_phase51_high_stability.ps1
```

#### Mythos Architecture V1 Productization

This is not Phase 52. It consolidates the existing phases into one product
control plane with Phase 40 as the main runtime.

Location:

- `src/mythos_v1/capability_registry.py`
- `src/mythos_v1/backend_comparison.py`
- `src/mythos_v1/training_preflight.py`
- `src/mythos_v1/release_gate.py`
- `docs/mythos_v1_productization.md`

Main guarantees:

- Eight capability owners and measurable quality signals.
- One-command release decision with exact golden and regression suites.
- Mock architecture and real model quality are measured separately.
- Serious API requests enforce strict reasoning, verifier, and security gates.
- Memory is isolated by project and deduplicated before persistence.
- UI streams real planning, memory, agent, critic, and verifier events.
- Training assets are validated now; actual training waits for reviewed data and CUDA.

Command:

```powershell
.\scripts\run_mythos_v1_release.ps1
```

---

<a id="source-4-phase40-to-42-integrated-runtime"></a>

## Source 4: Phase 40-42 Integrated Runtime

Source file: `docs/phase40_to_42_integrated_runtime.md`

### Phase 40-42 Integrated Runtime

These phases turn the architecture from many strong components into a default
end-to-end agent path.

#### Phase 40: Integrated Default Agent

Purpose:

- Make `/v1/agent/run` use the full architecture path.
- Inject Phase 37 vector memory before planning.
- Run Phase 24 Main Agent V2 and TaskGraph execution.
- Review output with Phase 22 critic.
- Run Phase 27 verifier policy.
- Run Phase 38 multi-language security scan when the intent needs it.
- Promote failures into experience memory for future retrieval.

Primary file:

```text
src/phase40/integrated_agent.py
```

Run:

```powershell
.\scripts\run_phase40_integrated_agent.ps1 -Prompt "build a secure login api with tests"
```

Local CPU behavior:

- `tiny-llama-cpu-smoke` is the default stable Windows CPU real-backend plumbing profile.
- `qwen-cpu-smoke` remains the target local Qwen quality-smoke profile, but this Windows CPU Torch/Transformers stack can crash natively while loading that checkpoint.
- Phase 40 uses `agent_backend_mode=auto`, so multi-agent orchestration uses mock plumbing on CPU unless forced with `-AgentBackendMode active`.
- GPU/vLLM profiles switch Phase 40 to active model orchestration.

API:

```text
POST /v1/agent/run
```

Legacy path:

```text
POST /v1/agent/run-legacy
```

#### Phase 41: Real Task Regression Pack

Purpose:

- Create a larger local regression suite for architecture and model changes.
- Catch regressions in short prompt understanding, debugging, security, multi-file repo reasoning, and patch verification.

Primary file:

```text
src/phase41/regression_pack.py
```

Dataset shape:

- 100 short prompt coding tasks
- 100 debugging tasks
- 100 security finding tasks
- 50 multi-file repository tasks
- 50 patch verification tasks

Run:

```powershell
.\scripts\run_phase41_regression_pack.ps1 -SmokeLimit 25
```

Output:

```text
artifacts/phase41/regression-pack.jsonl
artifacts/phase41/regression-pack-latest.json
```

#### Phase 42: Production Profile Switcher

Purpose:

- Switch between local mock, stable tiny CPU smoke, local CPU Qwen smoke, and future GPU/vLLM profiles through one contract.
- Generate active env config for Phase 11, Phase 24, Phase 40, and scorecard runners.
- Keep GPU profiles ready without pretending this CPU machine can run them.

Primary file:

```text
src/phase42/profile_switcher.py
```

List profiles:

```powershell
.\scripts\run_phase42_profile_switcher.ps1 -List
```

Activate current local profile:

```powershell
.\scripts\run_phase42_profile_switcher.ps1 -Profile tiny-llama-cpu-smoke
```

The tiny profile is revision-pinned and is for endpoint stability, routing, scorecard plumbing, and API testing only. Its model quality is intentionally not meaningful.

Activate local Qwen profile when the runtime can load it safely:

```powershell
.\scripts\run_phase42_profile_switcher.ps1 -Profile qwen-cpu-smoke
```

Activate future GPU profile:

```powershell
.\scripts\run_phase42_profile_switcher.ps1 -Profile qwen2.5-coder-7b
```

Outputs:

```text
config/active_model_profile.json
config/active_model_profile.ps1
artifacts/phase42/profile-switch-latest.json
```

#### Default Production Flow

1. Activate a runtime profile with Phase 42.
2. Run `/v1/agent/run` or Phase 40 CLI.
3. Phase 40 retrieves vector memory and runs the full agent path.
4. Phase 41 regression pack checks whether changes improved or degraded behavior.
5. Future training outputs still go through Phase 39 before promotion.

---

<a id="source-5-phase43-to-50-cognitive-architecture"></a>

## Source 5: Phase 43-50 Cognitive Architecture

Source file: `docs/phase43_to_50_cognitive_architecture.md`

### Phase 43-50 Cognitive Architecture

This document covers the final cognitive control layer added on top of the
existing agent, verifier, memory, and training-readiness stack. The goal is to
move the system closer to a Cursor + Claude Code + DeepSeek R1 style coding and
security AI: short prompts are expanded, complex work is planned as a graph,
specialists run in parallel, memory informs decisions, and outputs are reviewed
before acceptance.

#### Phase 43: Meta Planner

File:

- `src/phase43/meta_planner.py`

Purpose:

- Convert a broad user request into a durable architecture plan.
- Produce requirements, risks, components, execution lanes, and a TaskGraph
  brief.
- Make the planner more than a one-shot prompt expansion layer.

When it runs:

- Before serious agentic coding work.
- Inside Phase 40 when `meta_planning=True`.
- During the Phase 43-50 E2E contract test.

Why it exists:

- Most coding agents fail by starting implementation before decomposing the
  actual system. Phase 43 forces requirements, verification, and security into
  the plan first.

#### Phase 44: Intent Expansion Engine

File:

- `src/phase44/intent_expansion.py`

Purpose:

- Expand tiny prompts like `login system` into domain requirements,
  acceptance tests, security requirements, and edge cases.

When it runs:

- Before Phase 43 planning for short or underspecified prompts.
- As a standalone analyzer for prompt-understanding tests.

Why it exists:

- The target AI must handle short prompts well. This layer gives the rest of
  the stack a richer interpretation without needing the user to write a giant
  prompt.

#### Phase 45: Parallel Agent Runtime

File:

- `src/phase45/parallel_agent_runtime.py`

Purpose:

- Execute specialist lanes from the meta-plan concurrently.
- Use role agents such as architect, coder, tester, security auditor,
  researcher, and reviewer.
- Aggregate artifacts and surface conflicts.

When it runs:

- For broad tasks that benefit from parallel expert work.
- In mock mode for plumbing tests, or against the active model backend later.

Why it exists:

- Linear agents are slow and often overfit to one perspective. Parallel lanes
  let the system compare specialized outputs before final synthesis.

#### Phase 46: Hierarchical Memory

File:

- `src/phase46/hierarchical_memory.py`

Purpose:

- Provide five memory layers: working, session, project, experience, and
  knowledge.
- Render a compact context block for planner/model prompts.

When it runs:

- Inside Phase 40 when `hierarchical_memory=True`.
- Before planning or review when past failures and project rules matter.

Why it exists:

- Long context cannot be solved only by bigger attention windows. Hierarchical
  memory keeps recent context, durable project knowledge, and past failure/fix
  outcomes separate but retrievable.

#### Phase 47: Reasoning Engine

File:

- `src/phase47/reasoning_engine.py`

Purpose:

- Split reasoning into thinker, critic, and verifier stages.
- Decide whether an answer is accepted or needs more review.

When it runs:

- Inside Phase 40 when `reasoning_review=True`.
- For standalone reasoning quality checks.

Why it exists:

- A single generated answer is not enough. The system needs a dedicated review
  track that can catch missing tests, missing security, weak verification, or
  unsafe destructive actions.

#### Phase 48: Knowledge Graph

File:

- `src/phase48/knowledge_graph.py`

Purpose:

- Store architecture phases, dependencies, patterns, bugs, fixes, and tools as
  graph relationships.
- Return graph context for planner and memory layers.

When it runs:

- During architecture context building and Phase 43-50 E2E checks.
- Later it can be fed by repository parsing, experience memory, and evaluation
  history.

Why it exists:

- Vector search is good for similarity, but explicit relationships are better
  for dependency reasoning: what feeds what, what verifies what, and where
  failures should be promoted.

#### Phase 49: Multimodal Expert

File:

- `src/phase49/multimodal_expert.py`

Purpose:

- Create a safe local contract for images, screenshots, diagrams, PDFs, and
  repository folders.
- Extract metadata and route visual/document inputs into the planner.

When it runs:

- When the request includes screenshots, diagrams, PDFs, images, or attached
  repositories.

Why it exists:

- The future AI should reason over more than text. This phase gives the system
  a schema-ready multimodal slot now, while keeping CPU-safe local behavior.

#### Phase 50: MoE Routing Layer

File:

- `src/phase50/moe_router.py`

Purpose:

- Route prompts to expert families: security, planning, coding, reasoning,
  memory, multimodal, and research.
- Return primary expert plus execution hints.

When it runs:

- Inside Phase 40 when `moe_routing=True`.
- Before running specialist workflows.

Why it exists:

- Long-term 50B-100B or MoE models will still need a router. This software
  router is the controllable version of that idea today.

#### Integrated Path

Default serious request path:

```text
User prompt
  -> Phase 50 MoE route
  -> Phase 44 intent expansion when needed
  -> Phase 43 meta-plan
  -> Phase 46 hierarchical memory
  -> Phase 37 vector experience memory
  -> Phase 24 / Phase 20 agent execution
  -> Phase 22 critic
  -> Phase 47 reasoning review
  -> Phase 27 verifier policy
  -> Phase 38 multi-language security
  -> Phase 21 failure/fix/outcome memory promotion
```

#### Commands

```powershell
python src\phase43\meta_planner.py --prompt "build secure login system"
python src\phase44\intent_expansion.py --prompt "login system"
python src\phase45\parallel_agent_runtime.py --prompt "build secure login system" --backend-mode mock
python src\phase46\hierarchical_memory.py --query "secure planner verifier" --seed
python src\phase47\reasoning_engine.py --prompt "debug secure login architecture"
python src\phase48\knowledge_graph.py --query "meta planner memory reasoning" --seed
python src\phase49\multimodal_expert.py --prompt "analyze architecture assets" --path .
python src\phase50\moe_router.py --prompt "build secure login system"
python src\phase50\phase43_to_50_e2e.py --prompt "build secure login system"
```

#### Production Notes

- Phase 45 can run with mock backend locally and real model backends later.
- Phase 46 should later connect to Redis/Postgres/Qdrant-backed storage.
- Phase 47 is a local reasoning contract today; a dedicated critic/reasoner
  checkpoint can replace the heuristic layer later.
- Phase 48 should be fed by Phase 1 call graphs and Phase 21 experience memory.
- Phase 49 is metadata-only locally; vision/OCR adapters can be attached later.
- Phase 50 is the control-plane router for future expert models or MoE serving.

---

<a id="source-6-phase51-high-stability-reasoning-memory"></a>

## Source 6: Phase 51: High-Stability Reasoning And Unified Memory

Source file: `docs/phase51_high_stability_reasoning_memory.md`

### Phase 51: High-Stability Reasoning And Unified Memory

Phase 51 adds a strict backend control layer for reasoning and memory. It is
designed to prevent role mixing, context pollution, and low-quality memory
growth over time.

#### Code

- `src/phase51/high_stability_reasoning_memory.py`
- `scripts/run_phase51_high_stability.ps1`

#### Module 1: Strict Reasoning Engine

The `ReasoningEngine` runs:

```text
Think -> Execute -> Reflect -> Revise
```

Roles are separated by contract:

- Planner: creates `PlannerOutput` task graph only. It cannot write executable
  code or commands.
- Executor: executes the planner task graph only. It cannot reorder or alter
  the plan.
- Critic: computes confidence and reports flaws only. It cannot provide a
  solution or patch.
- Verifier: checks schema, step order, fingerprints, and success criteria only.
  It does not reason or revise.

All outputs are validated through strict Pydantic JSON schemas:

- `PlannerOutput`
- `ExecutorOutput`
- `CriticOutput`
- `VerifierOutput`
- `ReflectionOutput`
- `ReasoningTrace`

Reflection trigger:

- If critic confidence is `< 0.6`, the engine forces a reflection pass.
- The reflection pass always uses these prompts:

```text
What assumptions are wrong?
What can fail?
What security risks exist?
What was not verified?
```

#### Module 2: Unified Memory Manager

`UnifiedMemoryManager` has four layers:

| Layer | Purpose | TTL | Format |
| --- | --- | --- | --- |
| Working Memory | Current task context | 1 session | `{"project": "...", "current_feature": "..."}` |
| Project Memory | Repository and architecture knowledge | Project lifetime | `{"module_name": "...", "path": "...", "tech_stack": "..."}` |
| Experience Memory | Solved failure/fix records | Durable | `{"failure": "...", "fix": "...", "context": "..."}` |
| Knowledge Graph | Concept relationships | Durable | nodes + edges |

#### Module 3: Memory Gatekeeper

Allowed memory kinds:

- `verified_fix`
- `passed_benchmark`
- `security_finding`
- `successful_plan`

Rejected memory kinds:

- `raw_thought`
- `failed_guess`
- `temporary_output`

Every accepted memory entry includes:

```json
{
  "relevance": 0.0,
  "success_rate": 0.0,
  "last_used": 0.0,
  "usage_count": 0
}
```

Retrieval ranking uses the requested formula exactly:

```text
Final Score =
  (0.4 * Vector Similarity)
  + (0.3 * Success Rate)
  + (0.2 * Recency Weight)
  + (0.1 * Usage Count Weight)
```

The current local vector similarity is deterministic lexical similarity. The
interface can later be swapped for embedding vectors without changing the
ranking formula.

Archival:

- `archive_low_quality()` moves entries with consistently low quality into
  `cold_storage.jsonl`.
- Working memory can be cleared per session.

#### Run

```powershell
.\scripts\run_phase51_high_stability.ps1
```

Direct:

```powershell
python src\phase51\high_stability_reasoning_memory.py --prompt "build strict planner executor critic verifier memory architecture"
```

Output:

```text
artifacts/phase51/high-stability-reasoning-memory-latest.json
```

#### Integration With Existing Architecture

Phase 51 is not meant to replace the earlier phases or create a disconnected
parallel brain. It now sits on top of the existing Phase 40 integrated agent
path as a stability layer:

```text
Phase 40 integrated agent
  -> Phase 50 route
  -> Phase 43 meta-plan
  -> Phase 46 hierarchical memory
  -> Phase 51 strict memory retrieval
  -> Phase 37 vector memory
  -> Phase 24 / Phase 20 agent execution
  -> Phase 22 critic
  -> Phase 47 reasoning review
  -> Phase 51 strict role-contract review
  -> Phase 27 / Phase 38 verification
```

This means Phase 51 is both:

- a standalone contract test module, and
- an active guardrail inside the main agent runtime.

#### Why This Exists

Earlier phases already had planning, reasoning, and memory pieces. Phase 51 is
the stricter stability contract:

- no role mixing,
- no unvalidated free-form component outputs,
- no raw thought ingestion,
- no failed guesses in durable memory,
- explicit mathematical ranking,
- explicit reflection trigger below confidence `0.6`.

---

<a id="source-7-mvp-start-guide"></a>

## Source 7: mvp_start_guide.md

Source file: `docs/mvp_start_guide.md`

q# MVP Start Guide

Use this when starting from an empty local machine state. It creates starter
data, extracts call graphs, trains a small tokenizer artifact from the starter
corpus, and runs the offline deployment doctor.

```powershell
python src\phase10\bootstrap_mvp.py --run-id mvp-local-001
```

Generated paths:

- `data/raw_code/`
- `data/security_samples/vulnerable_samples.jsonl`
- `data/benchmarks/cyberseceval2_mini.jsonl`
- `artifacts/mvp/ast_graph.jsonl`
- `artifacts/mvp/callgraph.json`
- `artifacts/mvp/code_bpe_tokenizer/tokenizer.json`
- `artifacts/mvp/<run_id>.json`
- `artifacts/mvp/<run_id>.md`

When Docker, Redis, PostgreSQL, Qdrant, vLLM, and the FastAPI gateway are live:

```powershell
python src\phase10\bootstrap_mvp.py `
  --run-id mvp-live-001 `
  --run-live-smoke `
  --run-sandbox-smoke
```

If the local machine does not have Docker/GPU/database services yet, the runner
will still complete the offline MVP and report the missing live infrastructure
as warnings or skipped checks.

---

<a id="source-8-live-infra-blockers"></a>

## Source 8: Current Live Infrastructure Status

Source file: `docs/live_infra_blockers.md`

### Current Live Infrastructure Status

The local architecture path is running on this Windows workstation. Docker
Desktop is active, and the development Redis/PostgreSQL/Qdrant services are
healthy through `deploy/dev/docker-compose.yml`.

#### Working Locally

- Docker CLI and Docker engine are available.
- WSL2 Docker Desktop backend is running.
- Redis is live on `127.0.0.1:6379`.
- PostgreSQL is live on `127.0.0.1:5432`.
- Qdrant is live on `127.0.0.1:6333`.
- Mock vLLM is live on `127.0.0.1:8000`.
- FastAPI gateway is live on `127.0.0.1:18080`.
- Sandbox execution works with `python:3.12-slim`.
- Core local ML packages are installed: `torch`, `transformers`, `accelerate`,
  `trl`, `wandb`, and `vllm`.

#### Remaining Production Blockers

- NVIDIA/CUDA is not detected on this machine through `nvidia-smi`.
- `deepspeed` does not install cleanly on the current Windows/Python 3.14
  environment.
- `autoawq`/`awq` is blocked locally because Triton does not provide a matching
  Windows wheel for this runtime.

These blockers do not prevent local architecture, sandbox, quota, gateway, or
evaluation development. They do block real 7B-13B GRPO training, DeepSpeed
ZeRO-2 runs, AWQ quantization, and production vLLM serving.

#### Daily Readiness Check

```powershell
cd C:\Users\mah54\Desktop\AI_Architecture_Build
.\scripts\run_architecture_audit.ps1
```

For a faster smoke check:

```powershell
python src\phase10\e2e_smoke_runner.py `
  --run-id live-smoke `
  --tokenizer artifacts\mvp\code_bpe_tokenizer\tokenizer.json `
  --postgres-dsn "postgresql://ai:ai_dev_password@localhost:5432/ai_eval" `
  --redis-url redis://127.0.0.1:6379/0 `
  --qdrant-url http://127.0.0.1:6333 `
  --gateway-url http://127.0.0.1:18080 `
  --vllm-url http://127.0.0.1:8000 `
  --run-sandbox-smoke `
  --strict
```

#### Production GPU Path

Use an Ubuntu Linux host with NVIDIA drivers, CUDA, and a supported Python
runtime for the full training/serving stack:

- DeepSpeed ZeRO-2 GRPO training.
- AWQ INT4 quantization.
- Real vLLM PagedAttention serving with tensor parallelism.
- Large benchmark runs against full model checkpoints.

---

<a id="source-9-gpu-7b32b-readiness"></a>

## Source 9: 7B-32B GPU Readiness

Source file: `docs/gpu_7b32b_readiness.md`

### 7B-32B GPU Readiness

We are not waiting for GPU hardware to build the training/serving path.

Phase 17 prepares everything that can be safely built on the current machine:

- pinned 7B-32B model profiles
- vLLM serving commands
- QLoRA SFT trainer
- GRPO launch commands
- DeepSpeed ZeRO-2/ZeRO-3 configs
- Accelerate config
- profile `.env` files
- readiness report

#### Generate

```powershell
.\scripts\run_phase17_gpu_readiness.ps1
```

Outputs:

```text
artifacts/phase17/gpu-readiness.json
artifacts/phase17/gpu-readiness.md
deploy/gpu/model_profiles.json
deploy/gpu/deepspeed_zero2.json
deploy/gpu/deepspeed_zero3.json
deploy/gpu/accelerate_zero2.yaml
deploy/gpu/profiles/*.env
```

#### First GPU Target

Start with:

```text
qwen2.5-coder-7b
```

Then move to:

```text
qwen2.5-coder-14b
qwen2.5-coder-32b
```

#### Linux CUDA Commands

Serve:

```bash
MODEL_PROFILE=qwen2.5-coder-7b deploy/gpu/run_vllm_profile.sh
```

QLoRA SFT:

```bash
MODEL_PROFILE=qwen2.5-coder-7b deploy/gpu/train_qlora_sft_profile.sh
```

GRPO:

```bash
MODEL_PROFILE=qwen2.5-coder-7b deploy/gpu/train_grpo_profile.sh
```

#### Important

The current Windows CPU Qwen backend is only a live integration fallback. The
real quality path is already prepared for 7B-32B Linux CUDA runs.

---

<a id="source-10-phase10-deployment-smoke"></a>

## Source 10: Phase 10: Deployment Doctor and E2E Smoke Runner

Source file: `docs/phase10_deployment_smoke.md`

### Phase 10: Deployment Doctor and E2E Smoke Runner

Phase 10 verifies that the architecture is connected end to end before training
or production deployment.

#### Offline Code-Level Smoke

Use this on a development machine without Docker, Redis, Postgres, Qdrant, vLLM,
or GPUs:

```powershell
python src\phase10\e2e_smoke_runner.py --offline
```

This checks:

- Python runtime version.
- Required phase files.
- Full `compileall` over `src`.
- Core Python packages.
- Tokenizer artifact loading and encoding.
- Phase 4 swarm mock workflow.
- Phase 9 custom security suite mock workflow.

#### Live Infrastructure Smoke

Run after starting Docker Desktop and your services:

```powershell
python src\phase10\e2e_smoke_runner.py `
  --gateway-url http://localhost:8080 `
  --vllm-url http://localhost:8000 `
  --redis-url redis://localhost:6379/0 `
  --postgres-dsn "postgresql://user:pass@localhost:5432/ai_eval" `
  --qdrant-url http://localhost:6333 `
  --run-sandbox-smoke `
  --strict
```

Reports are written to `artifacts/phase10/<run_id>.json` and
`artifacts/phase10/<run_id>.md`.

#### Status Meaning

- `ok`: check succeeded.
- `warn`: code works but important optional runtime packages are missing.
- `skip`: intentionally skipped, usually by `--offline` or missing DSN.
- `fail`: a required check failed.

---

<a id="source-11-phase11-20-step-architecture-plan"></a>

## Source 11: Phase 11 Architecture Plan

Source file: `docs/phase11_20_step_architecture_plan.md`

### Phase 11 Architecture Plan

The original six tracks are now split into concrete build steps. This is the
architecture-first path before model training.

#### 1. Unified AI Core Interface

1. Backend contract: one `ModelBackend` API for mock, PyTorch checkpoints,
   vLLM/OpenAI-compatible servers, and future trained models.
2. Generation schema: strict typed messages, generation config, token estimates,
   and response metadata.
3. Backend swap layer: environment-driven backend selection without changing the
   chat, agent, memory, or security code.
4. Tokenizer adapter: Hugging Face tokenizer artifact loader plus local fallback
   tokenizer for CPU smoke tests.

#### 2. Chat and Agent API Backend

5. Session memory: local session history with system/user/assistant messages.
6. Static chat interface: separate HTML, CSS, and JavaScript under
   `src/phase11/static`.
7. Agent endpoint: `/v1/agent/run` returns full structured runtime reports.
8. Tool call bus: `/v1/tools` and `/v1/tools/call` expose validated runtime
   tools.
9. Memory endpoints: `/v1/memory/retrieve` and `/v1/memory/index` expose context
   packing and persistent memory indexing.

#### 3. Agentic Coding Runtime v2

10. Intent expansion: short prompts are expanded into actionable engineering
    intent.
11. Planner: priority files, staged task graph, and verification strategy.
12. Code editor: structured patches can be parsed, path checked, security
    reviewed, and optionally applied.
13. Test runner: verification can route through the hardened Docker sandbox.
14. Debugger: failed stdout/stderr telemetry becomes a structured diagnosis.
15. Self-healer: failed telemetry can be re-injected into the model for a
    bounded correction cycle.
16. Adversarial reviewer: security score, sandbox status, and patch evidence are
    combined into a confidence gate.
17. Final patch generator: final output includes model answer, reviewer score,
    and failure diagnosis when relevant.

#### 4. Long Context Memory Engine

18. Workspace scanner: source and documentation files are indexed with hashes,
    symbols, and token estimates.
19. Context retrieval: prompt terms, file paths, and symbols are scored into a
    compact context pack.
20. Call graph enrichment: existing AST/callgraph JSONL records can be added to
    retrieved context.
21. Embedding contract: deterministic hash embeddings give the vector path a
    stable interface before trained code embeddings arrive.
22. Persistent memory gateway: local, Redis, PostgreSQL, and Qdrant upsert paths
    share one API.
23. Context packer: retrieved source, symbols, call graph records, and metadata
    are packed under a token budget.

#### 5. Security Reasoning Engine

24. Rule-based triage: detect unsafe copy, SQL injection, shell injection, weak
    crypto, traversal, and unsafe deserialization patterns.
25. Workspace security review: production source is scanned while fixtures are
    excluded by default.
26. Patch regression review: before/after patches are checked for newly
    introduced obvious security issues.
27. Sandbox verification: generated Python patch/test files can be verified in
    the Phase 2 Docker sandbox.

#### 6. PyTorch Model Skeleton

28. Decoder transformer: local decoder-only architecture with causal attention
    and top-p generation.
29. Checkpoint format: config/state dict save and safer weights-only load.
30. Inference wrapper: tokenizer, checkpoint, device, and generation are wrapped
    behind `PyTorchCausalLMBackend`.
31. Training bridge: later SFT/GRPO/QLoRA checkpoints can plug into the same
    tokenizer/backend contract.

#### Current Local Status

Implemented now:

- Steps 1-31 have concrete local modules or endpoints.
- GPU-heavy model training, AWQ quantization, and real large-scale embeddings are
  intentionally later phases.

Next hardening options:

- Persistent database-backed chat sessions.
- Real semantic embedding model for memory retrieval.
- Browser-based file explorer and diff preview.
- Fine-grained project permissions per workspace.
- Live tool timeline in the chat interface.

---

<a id="source-12-phase11-pytorch-ai-core"></a>

## Source 12: Phase 11 PyTorch AI Core

Source file: `docs/phase11_pytorch_ai_core.md`

### Phase 11 PyTorch AI Core

Phase 11 is the architecture layer for the future model. It does not require
CUDA training yet. It gives us a strong controllable shell for chat, memory,
agentic coding, security review, and later PyTorch checkpoint integration.

#### Components

- `src/phase11/pytorch_model.py`
  - Decoder-only PyTorch transformer skeleton.
  - Config/checkpoint save/load helpers.
  - Local generation loop with temperature and top-p sampling.

- `src/phase11/model_backends.py`
  - Unified `ModelBackend` interface.
  - Deterministic mock reasoning backend for local development.
  - OpenAI/vLLM-compatible backend.
  - PyTorch causal LM backend for future local checkpoints.

- `src/phase11/tokenization.py`
  - Hugging Face `tokenizers` artifact adapter.
  - Deterministic char fallback tokenizer for local smoke tests.
  - Shared tokenizer contract for future trained checkpoints.

- `src/phase11/memory_engine.py`
  - Workspace scanner.
  - Short-prompt intent expansion.
  - Keyword/symbol retrieval.
  - Context packing with token budget estimation.
  - AST/callgraph JSONL enrichment when Phase 1 artifacts exist.

- `src/phase11/persistent_memory.py`
  - Hash embedding contract for local vector memory.
  - Optional Redis, PostgreSQL, and Qdrant upsert gateway.
  - Local vector search for architecture validation without external services.

- `src/phase11/security_engine.py`
  - Rule-based vulnerability finding for common coding/security bugs.
  - Patch security comparison.
  - Workspace security scoring.
  - Docker sandbox verification hook for generated patch/test files.

- `src/phase11/tool_runtime.py`
  - Validated tool registry.
  - Workspace file listing/reading, security scan, and sandbox Python execution.

- `src/phase11/agentic_runtime.py`
  - Agentic coding runtime v2.
  - Planner, code editor, test runner, debugger, reviewer, self-healer, and
    final patch generator.
  - Context retrieval, security review, sandbox verification, patch extraction,
    and guarded optional writes.

- `src/phase11/chat_api.py`
  - FastAPI chat and agent API.
  - Serves the local browser UI at `http://127.0.0.1:8090`.
  - `/v1/chat`, `/v1/agent/run`, `/v1/security/analyze`,
    `/v1/tools/call`, `/v1/memory/retrieve`, and `/v1/memory/index`.

- `src/phase11/static/*`
  - Separate frontend files: `index.html`, `styles.css`, and `app.js`.
  - Loads the local AI logo through `/brand/logo`.

- `src/phase11/roadmap.py`
  - Machine-readable twenty-step architecture roadmap split from the original
    six tracks.

#### Run

```powershell
.\scripts\run_phase11_smoke.ps1
.\scripts\start_phase11_chat.ps1
```

Then open:

```text
http://127.0.0.1:8090
```

The logo is loaded from the first image file found in the workspace
`image.png/` folder. No generated fallback logo is used.

#### Architecture Roadmap

```powershell
curl.exe http://127.0.0.1:8090/v1/architecture/roadmap
```

Detailed plan:

```text
docs/phase11_20_step_architecture_plan.md
```

#### Backend Modes

Default local mode:

```powershell
$env:PHASE11_BACKEND = "mock"
```

Use the existing vLLM/OpenAI-compatible endpoint:

```powershell
$env:PHASE11_BACKEND = "openai_compatible"
$env:PHASE11_MODEL_ENDPOINT = "http://127.0.0.1:8000/v1"
$env:PHASE11_MODEL_NAME = "security-coder"
.\scripts\start_phase11_chat.ps1
```

Use a future PyTorch checkpoint:

```powershell
$env:PHASE11_BACKEND = "pytorch"
$env:PHASE11_CHECKPOINT = "artifacts\models\mythos_decoder.pt"
$env:PHASE11_TOKENIZER = "artifacts\tokenizer\mythos-code-bpe.json"
.\scripts\start_phase11_chat.ps1
```

#### Why This Matters

Short prompts become stronger because the runtime expands intent, loads project
context, checks security risk, and routes the request through a consistent
engineering workflow before answering.

Large-context work becomes safer because context is not just a long prompt. It
is packed from source files, symbols, call graph records, memory, and security
signals.

Future training can replace the backend without replacing the architecture.

---

<a id="source-13-phase12-capability-gauntlet"></a>

## Source 13: Phase 12 Capability Gauntlet

Source file: `docs/phase12_capability_gauntlet.md`

### Phase 12 Capability Gauntlet

Phase 12 tests whether the architecture behaves like the AI system we want:
short prompts become actionable, agent workflows run end to end, security issues
are detected/reviewed, long-context retrieval finds the right files, tool calls
stay safe, and sandbox failures enter the self-healing path.

This is different from the Phase 10 readiness audit. Phase 10 asks whether the
stack boots. Phase 12 asks whether the architecture can perform the workflows.

#### What It Tests

- Short prompt understanding
- Agentic coding workflow coverage
- Security reasoning and vulnerability triage
- Patch regression review
- Long-context memory retrieval and local vector search
- Tool safety boundaries
- Self-healing telemetry routing through the Docker sandbox

#### Run Quick Suite

```powershell
.\scripts\run_phase12_gauntlet.ps1
```

Quick suite is designed for daily checks. It runs a representative subset of the
golden tasks.

#### Run Full Suite

```powershell
$env:PHASE12_SUITE = "full"
$env:PHASE12_RUN_SANDBOX = "1"
.\scripts\run_phase12_gauntlet.ps1
```

The full suite exports the golden task dataset to:

```text
data/phase12/golden_tasks.jsonl
```

Reports are written to:

```text
artifacts/phase12/
```

#### Backend Modes

Mock backend:

```powershell
$env:PHASE12_BACKEND = "mock"
```

Mock proves architecture plumbing. It does not prove final model intelligence.

OpenAI/vLLM-compatible backend:

```powershell
$env:PHASE12_BACKEND = "openai_compatible"
$env:PHASE12_MODEL_ENDPOINT = "http://127.0.0.1:8000/v1"
$env:PHASE12_MODEL_NAME = "security-coder"
```

PyTorch backend:

```powershell
$env:PHASE12_BACKEND = "pytorch"
```

This uses the local Phase 11 PyTorch skeleton unless a future checkpoint is
wired through the backend environment.

#### How To Read Scores

- `overall_score`: average pass quality across non-skipped tasks.
- `architecture_ready`: true when there are no failed tasks and score is at
  least the configured pass score.
- `category_scores`: tells which architecture capability is weak.
- `recommendations`: next hardening actions generated from the scorecard.

With `mock`, a green score means the architecture is wired correctly. With a
real model backend, the same score begins to measure actual coding/security
quality.

---

<a id="source-14-phase13-backend-quality"></a>

## Source 14: Phase 13 Backend Quality Harness

Source file: `docs/phase13_backend_quality.md`

### Phase 13 Backend Quality Harness

Phase 13 compares real backend response quality. Phase 12 tells us whether the
architecture can run the workflow. Phase 13 tells us whether a backend/model
answers coding and cybersecurity prompts well enough to trust.

#### What It Measures

- Short prompt response quality
- Coding answer specificity
- Security analysis correctness signals
- Debugging and self-healing reasoning
- Required output format compliance
- Agentic workflow reasoning
- Long-context memory explanation quality

Each task is scored with keyword coverage, required reasoning markers, answer
length, structure, and forbidden generic/refusal terms. The harness reports a
baseline score, candidate score, category deltas, winner counts, and whether the
candidate meets the target score.

#### Run Local Comparison

```powershell
.\scripts\run_phase13_quality.ps1
```

Default behavior:

- Starts the local mock vLLM/gateway stack if needed.
- Uses `mock` as the architecture baseline.
- Uses the local OpenAI-compatible mock vLLM endpoint as the candidate.
- Writes reports to `artifacts/phase13/`.
- Exports tasks to `data/phase13/backend_quality_tasks.jsonl`.

#### Compare A Real Backend

```powershell
$env:PHASE13_CANDIDATE_BACKEND = "openai_compatible"
$env:PHASE13_MODEL_ENDPOINT = "http://127.0.0.1:8000/v1"
$env:PHASE13_MODEL_NAME = "security-coder"
$env:PHASE13_SUITE = "full"
.\scripts\run_phase13_quality.ps1
```

For an external compatible endpoint:

```powershell
$env:PHASE13_MODEL_ENDPOINT = "https://your-server.example/v1"
$env:PHASE13_MODEL_NAME = "your-model"
$env:PHASE13_API_KEY = "..."
.\scripts\run_phase13_quality.ps1
```

#### Compare Future PyTorch Checkpoint

```powershell
$env:PHASE13_CANDIDATE_BACKEND = "pytorch"
$env:PHASE13_CANDIDATE_CHECKPOINT = "artifacts\models\mythos_decoder.pt"
$env:PHASE13_CANDIDATE_TOKENIZER = "artifacts\tokenizer\mythos-code-bpe.json"
.\scripts\run_phase13_quality.ps1
```

#### How To Read It

- `baseline_score`: architecture control score.
- `candidate_score`: model/backend quality score.
- `score_delta`: candidate minus baseline.
- `candidate_ready`: true only if candidate score is above the configured target
  and has no failed task.
- `winner_counts`: task-by-task response comparison.

The local mock vLLM is expected to score low because it returns a fixed smoke
response. That is useful: it proves the harness catches weak backends. A real
model should beat the baseline in the categories we care about.

---

<a id="source-15-phase16-core-upgrades"></a>

## Source 15: Phase 16 Core Architecture Upgrades

Source file: `docs/phase16_core_upgrades.md`

### Phase 16 Core Architecture Upgrades

Phase 16 converts the target architecture gaps into executable local modules.

#### Built

- Durable TaskGraph planner and JSON store.
- Role-specific async micro-agents: architect, coder, tester, debugger, security auditor, reviewer, researcher.
- CriticBackend and VerifierBackend interfaces.
- Composite verifier with defensive security review and Phase 2 sandbox verification.
- ExperienceMemory with failure/fix/outcome promotion from scorecard failures.
- Defensive tool adapters for Git, Semgrep, CodeQL, and browser-style documentation metadata fetching.
- Scorecard failure exporter for SFT and GRPO preference candidates.
- Real base-model connector probe for Qwen/DeepSeek/Llama-family OpenAI-compatible endpoints or PyTorch checkpoints.

#### Run

```powershell
.\scripts\run_phase16_core.ps1
```

Outputs:

```text
artifacts/phase16/phase16-smoke.json
artifacts/phase16/phase16-smoke.md
artifacts/phase16/task_graphs/
artifacts/phase16/experience_memory.jsonl
artifacts/phase16/scorecard_failures_sft.jsonl
artifacts/phase16/scorecard_failures_grpo.jsonl
```

#### Real Model Connection

Use a vLLM/OpenAI-compatible server:

```powershell
$env:PHASE16_BACKEND = "openai_compatible"
$env:PHASE16_MODEL_ENDPOINT = "http://127.0.0.1:8000/v1"
$env:PHASE16_MODEL_NAME = "qwen-or-deepseek-coder"
.\scripts\run_phase16_core.ps1
```

Or use a local PyTorch checkpoint:

```powershell
$env:PHASE16_BACKEND = "pytorch"
$env:PHASE16_CHECKPOINT = "C:\path\to\checkpoint.pt"
$env:PHASE16_TOKENIZER = "artifacts\mvp\code_bpe_tokenizer\tokenizer.json"
.\scripts\run_phase16_core.ps1
```

The local architecture checks pass without a real model. The base-model connector
turns green only after a real endpoint/checkpoint is configured.

#### Install Defensive Security Tools

```powershell
.\scripts\install_security_tools.ps1
```

This provisions:

- Semgrep through the official Docker image.
- CodeQL CLI locally under `tools/codeql/codeql.exe`.

The FastAPI endpoint exposes availability:

```text
GET /v1/tools/advanced
```

#### Start Local Qwen Backend

```powershell
.\scripts\start_phase16_real_backend.ps1
.\scripts\run_phase16_core_real.ps1
```

Default local model:

```text
Qwen/Qwen2.5-Coder-0.5B-Instruct
```

It serves an OpenAI-compatible API at:

```text
http://127.0.0.1:8016/v1
```

To connect the main chat API to it:

```powershell
$env:PHASE11_BACKEND = "openai_compatible"
$env:PHASE11_MODEL_ENDPOINT = "http://127.0.0.1:8016/v1"
$env:PHASE11_MODEL_NAME = "Qwen/Qwen2.5-Coder-0.5B-Instruct"
.\scripts\start_phase11_chat_background.ps1
```

#### Safety

Security tooling stays defensive:

- Static analysis
- Vulnerability detection
- Patch generation guidance
- Sandbox verification

No autonomous exploit execution is enabled.

---

<a id="source-16-phase18-model-quality-loop"></a>

## Source 16: Phase 18 Real Model Quality Loop

Source file: `docs/phase18_model_quality_loop.md`

### Phase 18 Real Model Quality Loop

Phase 18 evaluates the connected real backend using the exact scorecard logic
from Phase 14, analyzes failures, and exports reviewed training candidates.

#### What It Builds

- Real Qwen scorecard runner.
- Failure analyzer by category, phase, and issue type.
- Training candidate promotion gate.
- SFT candidate JSONL.
- GRPO preference candidate JSONL.
- Dashboard report for the chat API.
- Chat API endpoint: `/v1/model-quality/latest`.
- Architecture audit dry-run check.

#### Run Quick Balanced Suite

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

#### Run Full 90-Task Scorecard

```powershell
$env:PHASE18_FULL = "1"
.\scripts\run_phase18_model_quality.ps1
```

The full run is slow on CPU. It is intended for a stronger local GPU backend or
remote vLLM endpoint.

#### Outputs

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

#### Why This Matters

This closes the loop between real model behavior and architecture improvement:

1. Run real/local/vLLM backend.
2. Score exact golden tasks.
3. Cluster failures by category, phase, and issue type.
4. Export review-required SFT and GRPO candidates.
5. Feed approved rows into later SFT/GRPO training.

The current CPU Qwen model is only for plumbing and local behavior checks. The
same script is intended to target future 7B-32B or larger vLLM backends by
changing the endpoint and model name.

---

<a id="source-17-phase19-to-23-quality-hardening"></a>

## Source 17: Phases 19-23 Quality Hardening

Source file: `docs/phase19_to_23_quality_hardening.md`

### Phases 19-23 Quality Hardening

These phases strengthen the architecture around the seven priority pillars:
planner, multi-agent system, memory, critic, verification, security experts,
and high-quality coding/reasoning data.

#### Phase 19: Verifier Registry

Unified defensive verifier:

- rule-based security review
- secret scan
- optional Semgrep scan
- optional CodeQL scan
- optional Docker sandbox test run

Run:

```powershell
.\scripts\run_phase19_verifier.ps1
```

Optional deeper checks:

```powershell
$env:PHASE19_RUN_SEMGREP = "1"
$env:PHASE19_RUN_SANDBOX = "1"
.\scripts\run_phase19_verifier.ps1
```

#### Phase 20: TaskGraph Runtime

TaskGraph-first agent runtime using role-specific workers, critic review, and
optional verifier execution.

```powershell
$env:PHASE20_RUN_VERIFIER = "1"
.\scripts\run_phase20_taskgraph_runtime.ps1
```

#### Phase 21: Experience Promotion

Promotes Phase 18/19/20 failures and outcomes into experience memory. Local
JSONL works now; Postgres/Qdrant can be enabled with env vars.

```powershell
.\scripts\run_phase21_experience_promotion.ps1
```

#### Phase 22: Critic Backend

Runs either the heuristic critic or a real model-backed critic.

```powershell
.\scripts\run_phase22_critic.ps1
```

Model critic:

```powershell
$env:PHASE22_MODE = "model"
$env:PHASE22_MODEL_ENDPOINT = "http://127.0.0.1:8016/v1"
$env:PHASE22_MODEL_NAME = "Qwen/Qwen2.5-Coder-0.5B-Instruct"
.\scripts\run_phase22_critic.ps1
```

#### Phase 23: Model Quality Profiles

Profile-driven launcher for future 7B/14B/32B quality scorecard runs.

```powershell
$env:PHASE23_DRY_RUN = "1"
.\scripts\run_phase23_quality_profile.ps1
```

When a real vLLM endpoint is available:

```powershell
$env:PHASE23_ENDPOINT = "http://127.0.0.1:8000/v1"
$env:PHASE23_PROFILE = "qwen2.5-coder-7b"
$env:PHASE23_EXECUTE = "1"
.\scripts\run_phase23_quality_profile.ps1
```

#### API Endpoints

- `GET /v1/verifier/latest`
- `POST /v1/verifier/run`
- `GET /v1/taskgraph/latest`
- `POST /v1/taskgraph/run`
- `GET /v1/quality/latest` includes Phases 19-23

---

<a id="source-18-phase24-to-33-power-architecture"></a>

## Source 18: Phases 24-33 Power Architecture Layer

Source file: `docs/phase24_to_33_power_architecture.md`

### Phases 24-33 Power Architecture Layer

This batch turns the architecture into a stronger coding/security agent system
before serious model training.

#### What Was Added

1. **Phase 24 Main Agent V2**
   - TaskGraph-first runtime bridge.
   - Injects experience memory into planning.
   - Runs verifier/critic hooks.

2. **Phase 25 Experience Retrieval**
   - Retrieves past failure/fix/outcome records for planner context.

3. **Phase 26 Patch Manager**
   - Safe patch preview, diff, backup, and rollback manifest.
   - Defaults to preview, not mutation.

4. **Phase 27 Verifier Policy Engine**
   - Profiles: `fast`, `security`, `release`, `codeql-python`.
   - Writes default policy to `config/verifier_policy.json`.

5. **Phase 28 Security Expert Workflow**
   - Defensive-only vulnerability finding, safe patch direction, verifier rerun.

6. **Phase 29 Dataset Review Gate**
   - Candidate states: rejected, needs human review, train ready.
   - Prevents raw synthetic failures from entering training directly.

7. **Phase 30 Expanded Golden Benchmark**
   - Generates 450 tasks:
     - 100 short prompt coding
     - 100 debugging
     - 100 security finding
     - 100 patch generation
     - 50 long-context repo

8. **Phase 31 Long Context Packer**
   - Combines workspace retrieval and experience memory into one budgeted context block.

9. **Phase 32 Critic Endpoint Contract**
   - Validates an OpenAI-compatible critic endpoint.

10. **Phase 33 GPU Backend Contract**
    - Validates future 7B/14B/32B quality-run profile readiness.

#### Run Commands

```powershell
.\scripts\run_phase24_main_agent_v2.ps1
.\scripts\run_phase25_experience_retrieval.ps1
.\scripts\run_phase26_patch_manager.ps1
.\scripts\run_phase27_verifier_policy.ps1
.\scripts\run_phase28_security_workflow.ps1
.\scripts\run_phase29_dataset_review_gate.ps1
.\scripts\run_phase30_expanded_benchmark.ps1
.\scripts\run_phase31_long_context_packer.ps1
.\scripts\run_phase32_critic_contract.ps1
.\scripts\run_phase33_gpu_backend_contract.ps1
```

#### Safety

Security workflows remain defensive:

- static analysis
- vulnerability identification
- patch guidance
- verification
- no autonomous exploit execution

---

<a id="source-19-phase34-to-36-production-control"></a>

## Source 19: Phases 34-36 Production Control Layer

Source file: `docs/phase34_to_36_production_control.md`

### Phases 34-36 Production Control Layer

These phases add production control around the existing agent architecture:
authentication, quota, observability, and data flywheel automation.

#### Phase 34: Auth + Quota

Code:

- `src/phase34/auth_quota.py`
- Integrated into `src/phase11/chat_api.py`

What it does:

- Adds HS256 JWT auth middleware.
- Protects `/v1/*` routes when `PHASE34_AUTH_ENABLED=1`.
- Keeps health, static UI, logo, metrics, and token helper exempt.
- Connects authenticated users to Phase 6 Redis regenerative quota when
  `PHASE34_QUOTA_ENABLED=1`.

Important env:

```powershell
$env:PHASE34_AUTH_ENABLED = "1"
$env:PHASE34_JWT_SECRET = "replace-with-long-random-secret"
$env:PHASE34_QUOTA_ENABLED = "1"
$env:PHASE34_REDIS_URL = "redis://127.0.0.1:6379/0"
```

Generate a local token:

```powershell
$env:PHASE34_JWT_SECRET = "replace-with-long-random-secret"
.\scripts\run_phase34_auth_token.ps1
```

Development token endpoint is disabled by default. Enable only locally:

```powershell
$env:PHASE34_DEV_TOKEN_ENABLED = "1"
```

#### Phase 35: Observability

Code:

- `src/phase35/observability.py`
- Integrated into `src/phase11/chat_api.py`

What it does:

- Adds structured JSONL request logs.
- Adds Prometheus-compatible `/metrics`.
- Labels requests by phase, route, method, status, and duration.

Outputs:

- `artifacts/phase35/api-events.jsonl`
- `GET /metrics`

#### Phase 36: Data Flywheel

Code:

- `src/phase36/data_flywheel.py`

What it does:

- Reads Phase 18 real-model failures/candidates.
- Writes a Phase 3 rejection-sampling queue.
- Runs Phase 29 dataset review gate.
- Prepares a Phase 7 GRPO trigger manifest.
- Does not blindly train on unreviewed data.

Run:

```powershell
.\scripts\run_phase36_data_flywheel.ps1
```

Outputs:

- `artifacts/phase36/*phase3-rejection-queue.jsonl`
- `artifacts/phase36/*phase7-trigger.json`
- `artifacts/phase36/data-flywheel-latest.json`

Safety rule:

- Phase 36 queues and prepares training commands.
- Actual Phase 7 execution should happen only after dataset review and on a
  Linux CUDA training host.

---

<a id="source-20-phase37-to-39-production-hardening"></a>

## Source 20: Phase 37-39 Production Hardening

Source file: `docs/phase37_to_39_production_hardening.md`

### Phase 37-39 Production Hardening

These phases close the next three production gaps after auth, observability, and
the data flywheel.

#### Phase 37: Production Vector Memory

Purpose:

- Replace Phase 25 token matching as the only retrieval path.
- Store failure/fix/outcome experience records as vector-searchable memories.
- Keep a local deterministic vector index for CPU development.
- Mirror the same records into Qdrant/PostgreSQL when production services are configured.

Primary file:

```text
src/phase37/vector_memory.py
```

Run:

```powershell
.\scripts\run_phase37_vector_memory.ps1 -Rebuild -Query "security patch verifier failure"
```

API:

```text
POST /v1/memory/vector/retrieve
```

Why it exists:

At 10k+ experience records, keyword overlap alone becomes noisy. Phase 37 gives
the planner a stable vector memory contract now, while allowing a trained code
embedding model later.

#### Phase 38: Multi-Language Security Engine

Purpose:

- Add explicit Rust, Go, JavaScript/TypeScript, and Solidity defensive rules.
- Keep security behavior defensive: detection, patch guidance, verification.
- Produce language-specific patch guidance and regression recommendations.

Primary file:

```text
src/phase38/multilang_security.py
```

Run:

```powershell
.\scripts\run_phase38_multilang_security.ps1 -Workspace .
```

API:

```text
POST /v1/security/multilang
```

Coverage examples:

- Rust unsafe boundary review, command execution, panic-on-input paths.
- Go command execution, SQL formatting, path traversal surfaces.
- JavaScript child_process, SQL string building, prototype pollution.
- Solidity tx.origin authorization, reentrancy-prone calls, selfdestruct.

#### Phase 39: Training Checkpoint Rollback Gate

Purpose:

- Prevent SFT/GRPO training from silently degrading the active model.
- Compare candidate evaluation metrics against a baseline or active checkpoint.
- Promote only non-regressing checkpoints.
- Keep rollback manifests for restoring the last known-good active pointer.

Primary file:

```text
src/phase39/checkpoint_rollback.py
```

Run:

```powershell
.\scripts\run_phase39_checkpoint_gate.ps1 `
  -CandidateCheckpoint artifacts\models\candidate `
  -CandidateReport artifacts\eval\candidate.json `
  -BaselineReport artifacts\eval\baseline.json `
  -DryRun
```

Production flow:

1. Phase 7 or Phase 17 writes a candidate checkpoint.
2. Phase 9/18/23 evaluates the checkpoint.
3. Phase 39 compares required metrics.
4. If metrics pass, Phase 39 promotes the checkpoint.
5. If metrics regress, Phase 39 keeps the active pointer unchanged and writes a rollback manifest.

Default protected metrics:

- `overall_score`
- `pass_at_1`
- `security_score`

Default max allowed drop:

```text
0.02
```

#### Current Boundary

These phases are production-control architecture, not a replacement for real
model quality. They make the system safer and more scalable, but stronger model
behavior still depends on better data, stronger base models, and verified
training loops.

---

<a id="source-21-phase5-self-healing-blueprint"></a>

## Source 21: Phase 5 Self-Healing Runtime and QLoRA Staging Blueprint

Source file: `docs/phase5_self_healing_blueprint.md`

### Phase 5 Self-Healing Runtime and QLoRA Staging Blueprint

#### Integrated Runtime

The runtime wraps the Swarm/agent reasoning layer and the hardened Phase 2
Sandbox Engine.

1. The agent system emits a code artifact and reasoning path.
2. The sandbox compiles or executes the artifact.
3. If the sandbox crashes or exits nonzero, Phase 5 captures telemetry:
   - GCC/Clang warnings and errors
   - Python tracebacks
   - sanitizer findings
   - GDB/core-dump style frames
   - stack/register addresses
   - timeout flags
   - CPU and RAM metrics
4. The crash dump becomes an explicit `runtime_exception_trace`.
5. The repair model re-analyzes the failed reasoning path and proposes one patch.
6. The loop repeats up to exactly `5` recursive correction iterations.
7. On success, the full lifecycle becomes a token-ready training sequence.

#### Captured Lifecycle

```text
Initial Prompt
-> Failed Reasoning Path
-> Caught Telemetry Logs
-> Corrected Reasoning Trace
-> Success Verification Patch
```

Token format:

```text
<|self_heal_start|>
<|initial_prompt|>...
<|failed_reasoning_path|>...
<|caught_telemetry_logs|>...
<|corrected_reasoning_trace|>...
<|success_verification_patch|>...
<|self_heal_end|>
```

#### PostgreSQL Schema

Tables initialized by `POSTGRES_SCHEMA_SQL`:

- `healing_lifecycle_traces`
  - one successful self-healing trace
  - immutable hash
  - token-ready training sequence
  - promoted flag for offline QLoRA batching
- `healing_repair_iterations`
  - every failed and successful correction attempt
  - recursion depth
  - telemetry and sandbox result
- `qlora_training_jobs`
  - queued offline fine-tuning jobs
  - trace IDs
  - dataset path
  - base model and adapter output target

#### Qdrant Collection

Default collection:

```text
self_healing_qlora_staging
```

Indexed text:

- initial prompt
- failed reasoning path
- crash telemetry
- corrected reasoning trace
- successful patch

Payload:

```json
{
  "trace_id": "string",
  "created_at_unix_ms": 0,
  "immutable_hash": "sha256",
  "metadata": {}
}
```

#### Cron Worker

The cron worker polls PostgreSQL for unpromoted successful traces.

When the buffer count reaches `threshold_block_size`, it:

1. writes a JSONL QLoRA batch dataset
2. creates a `qlora_training_jobs` row
3. marks traces as promoted

This queues offline training safely without modifying online serving weights.

#### Source

```text
src/phase5/self_healing_runtime.py
```

---

<a id="source-22-phase6-regenerative-quota-blueprint"></a>

## Source 22: Phase 6 Redis Continuous Regenerative Quota Blueprint

Source file: `docs/phase6_regenerative_quota_blueprint.md`

### Phase 6 Redis Continuous Regenerative Quota Blueprint

#### Redis Hash Contract

Each user maps to a persistent Redis hash:

```text
quota:{tenant}:{sha256(user_id)}
```

Required fields:

```text
tokens_balance             floating-point current usage credits
last_updated_timestamp     floating-point UNIX epoch seconds
```

No static reset windows are used.

#### Replenishment Formula

On each API request:

```text
Delta_t = T_now - T_last
Tokens_current = min(C, Tokens_last + Delta_t * R)
```

Where:

- `C` is the hard maximum capacity ceiling.
- `R` is the replenishment rate in tokens per second.
- request `cost` is decremented only if `Tokens_current >= cost`.

#### Atomic Lua Execution

Source:

```text
src/phase6/redis_regenerative_bucket.lua
```

The Lua script runs entirely inside Redis single-threaded execution space:

1. `HGET tokens_balance`
2. `HGET last_updated_timestamp`
3. calculate delta and regenerated balance
4. compare regenerated balance against request cost
5. decrement if allowed
6. `HSET tokens_balance last_updated_timestamp`
7. return `[allowed, remaining_balance, regenerated_balance, retry_after]`

This prevents distributed race conditions and split-brain quota calculations.

#### Python Middleware

Source:

```text
src/phase6/redis_quota_engine.py
```

The FastAPI middleware:

- extracts user identity from `X-User-ID`
- estimates request cost from body size or `X-Quota-Cost`
- calls the Lua script through `EVALSHA`
- injects response headers:
  - `X-Quota-Allowed`
  - `X-Quota-Remaining`
  - `X-Quota-Regenerated`
  - `X-Quota-Capacity`
  - `X-Quota-Refill-Rate`
  - `X-Quota-Cost`
  - `Retry-After` on denial

#### Example Policy

```python
QuotaPolicy(
    capacity=300.0,
    refill_rate=5.0 / 60.0,  # 5 tokens per minute
    initialize_full=True,
    tenant="production",
)
```

---

<a id="source-23-phase7-grpo-blueprint"></a>

## Source 23: Phase 7 GRPO Training Blueprint

Source file: `docs/phase7_grpo_blueprint.md`

### Phase 7 GRPO Training Blueprint

#### Objective

Train a coding/cybersecurity policy model with Group Relative Policy Optimization.

For each prompt:

1. Generate `G = 8` candidate responses from the current policy.
2. Score each candidate with execution, security, format, and efficiency rewards.
3. Normalize advantages within the candidate group:

```text
A_i = (R_i - mean(R)) / std(R)
```

4. Optimize clipped GRPO/PPO-style policy loss plus frozen-reference KL:

```text
L_GRPO = -min(r_i A_i, clip(r_i, 1-eps, 1+eps) A_i) + beta * KL(pi_theta || pi_ref)
```

Defaults:

- `G = 8`
- `temperature = 0.8`
- `epsilon = 0.2`
- `beta = 0.01`

#### Reward Components

```text
R_exec = +1.0 if sandbox exit_code == 0 else -1.0
R_sec  = +0.5 if static analyzer finds zero new CVEs/issues else -0.5
R_fmt  = +0.3 if thought tokens are valid else -0.3
R_eff  = +0.2 if execution passes under 2000ms else -0.2

R_total = w1*R_exec + w2*R_sec + w3*R_fmt + w4*R_eff
```

#### Dataset JSONL Shape

```json
{
  "prompt": "Fix this vulnerable code...",
  "security_baseline_findings": 0,
  "sandbox": {
    "image": "python:3.12-slim",
    "files": [
      {
        "path": "test_candidate.py",
        "content": "import candidate\n..."
      }
    ],
    "generated_path": "candidate.py",
    "command": ["python3", "/workspace/test_candidate.py"]
  },
  "metadata": {
    "task_id": "example"
  }
}
```

#### Run

```powershell
python src\phase7\grpo_training_loop.py `
  --model-name-or-path Qwen/Qwen2.5-Coder-1.5B-Instruct `
  --dataset artifacts\grpo_prompts.jsonl `
  --output-dir artifacts\grpo_policy `
  --bf16 `
  --gradient-checkpointing `
  --deepspeed `
  --wandb
```

#### Source

```text
src/phase7/grpo_training_loop.py
```

---

<a id="source-24-phase8-serving-blueprint"></a>

## Source 24: Phase 8 vLLM Serving Blueprint

Source file: `docs/phase8_serving_blueprint.md`

### Phase 8 vLLM Serving Blueprint

#### vLLM Server

Launcher:

```text
src/phase8/vllm_server.py
```

Defaults:

- `--tensor-parallel-size 2`
- `--max-num-seqs 256`
- `--max-num-batched-tokens 8192`
- `--gpu-memory-utilization 0.94`
- `--quantization awq`
- `--enable-prefix-caching`
- `--max-model-len 8192`

vLLM handles PagedAttention KV cache management internally. GPU memory usage is
controlled through `gpu_memory_utilization`; batching is constrained by max
sequence and max token limits.

#### FastAPI Gateway

Gateway:

```text
src/phase8/gateway.py
```

Features:

- SSE streaming for `/v1/chat/completions`
- priority queue lanes:
  - code execution / vulnerability / agentic
  - chat
  - batch
- request timeouts:
  - normal: `30s`
  - agentic: `120s`
- Kubernetes probes:
  - `/health/live`
  - `/health/ready`

#### Prompt Router

Routing:

- vulnerability analysis: `temperature=0.1`, `top_p=0.9`
- code generation: `temperature=0.2`, `top_p=0.95`
- agentic reasoning: `temperature=0.4`, tool-call stop tokens

#### AWQ Quantization

Quantizer:

```text
src/phase8/quantize_awq.py
```

Input:

- bf16/full checkpoint
- 128 representative code samples in JSONL
- remote checkpoints require `--model-revision <pinned_commit_sha_or_tag>` by default
- `--trust-remote-code` is opt-in and should be used only for audited model repos

Output:

- AWQ INT4 model directory
- optional HumanEval benchmark report

#### Deployment

```text
deploy/phase8/docker-compose.yml
deploy/phase8/nginx.conf
```

---

<a id="source-25-phase9-evaluation-harness"></a>

## Source 25: Phase 9: Automated Evaluation Harness

Source file: `docs/phase9_evaluation_harness.md`

### Phase 9: Automated Evaluation Harness

Phase 9 measures coding and cybersecurity model quality after every training run.
It is designed to run against a vLLM/OpenAI-compatible endpoint and execute
generated code through the hardened Phase 2 Docker sandbox.

#### Components

- `src/phase9/evaluate.py`
  - Main CLI for benchmark runs, regression tracking, reports, and alerts.
- `src/phase9/benchmarks.py`
  - HumanEval, MBPP, CyberSecEval 2, and custom security benchmark runners.
- `src/phase9/security_suite.py`
  - Built-in 200-case security suite:
    - 50 buffer overflow detection cases.
    - 50 SQL injection identification cases.
    - 50 cryptographic weakness detection cases.
    - 50 secure patch generation cases.
- `src/phase9/regression_tracker.py`
  - PostgreSQL table initialization, result insertion, previous-run comparison,
    Markdown report generation, and Slack/Discord webhook regression alerts.
- `src/phase9/head_to_head.py`
  - Runs two model checkpoints on identical prompts and asks an LLM judge to score
    correctness, security awareness, and explanation quality.

#### PostgreSQL Schema

```sql
CREATE TABLE IF NOT EXISTS evaluation_runs (
    id BIGSERIAL PRIMARY KEY,
    run_id TEXT NOT NULL,
    timestamp DOUBLE PRECISION NOT NULL,
    benchmark TEXT NOT NULL,
    score DOUBLE PRECISION NOT NULL,
    delta DOUBLE PRECISION NOT NULL,
    metrics JSONB NOT NULL,
    sample_results JSONB NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_evaluation_runs_benchmark_time
ON evaluation_runs (benchmark, timestamp DESC);
```

#### Standard Evaluation

HumanEval and MBPP can be loaded either from local JSONL exports or from the
optional Hugging Face `datasets` package. CyberSecEval 2 is loaded from local
JSONL because enterprise/security benchmark copies are commonly mirrored
internally.

When using the Hugging Face fallback, set `HF_DATASET_REVISION` to an exact
dataset commit. Local JSONL files are preferred for locked production evals.

```powershell
python src\phase9\evaluate.py `
  --run-id run_2026_06_11_a `
  --endpoint http://localhost:8080/v1 `
  --model security-coder `
  --benchmarks humaneval mbpp cyberseceval2 custom_security `
  --humaneval-jsonl artifacts\benchmarks\humaneval.jsonl `
  --mbpp-jsonl artifacts\benchmarks\mbpp.jsonl `
  --cyberseceval2-jsonl artifacts\benchmarks\cyberseceval2.jsonl `
  --postgres-dsn "postgresql://user:pass@localhost:5432/ai_eval" `
  --alert-webhook "https://hooks.slack.com/services/..." `
  --regression-threshold 0.02 `
  --init-db
```

Outputs:

- JSONL regression history: `artifacts/phase9/evaluation_runs.jsonl`
- Markdown report: `artifacts/phase9/evaluation_report.md`
- PostgreSQL rows, one per benchmark result.

#### Metrics

- HumanEval:
  - `pass@1`
  - `pass@10`
- MBPP:
  - `pass@1`
- CyberSecEval 2:
  - `secure_rate`
  - `insecure_code_rate`
- Custom security suite:
  - Overall score.
  - Per-category scores for buffer overflow, SQL injection, crypto weakness, and
    patch generation.

The tracker compares each benchmark against the previous run and includes the
previous five runs in the Markdown report. If a benchmark score drops by more
than `--regression-threshold`, default `0.02`, it sends a
Slack/Discord-compatible webhook payload.

#### Head-To-Head Checkpoint Comparison

```powershell
python src\phase9\evaluate.py `
  --head-to-head `
  --model-a-endpoint http://localhost:8081/v1 `
  --model-a security-coder-old `
  --model-b-endpoint http://localhost:8082/v1 `
  --model-b security-coder-new `
  --judge-endpoint https://api.openai.com/v1 `
  --judge-model gpt-4.1 `
  --api-key $env:OPENAI_API_KEY `
  --head-to-head-prompts artifacts\phase9\judge_prompts.jsonl
```

The output JSONL contains one row per prompt with the winner, per-axis scores,
and the judge rationale.

---

<a id="source-26-scorecard-harness"></a>

## Source 26: AI Architecture Scorecard

Source file: `docs/scorecard_harness.md`

### AI Architecture Scorecard

This is the exact scorecard gate for the architecture.

#### Metrics

- `architecture_reliability_score`
- `agent_workflow_completion_score`
- `security_detection_fix_score`
- `short_prompt_understanding_score`
- `sandbox_test_pass_rate`
- `regression_count`

#### Golden Dataset

The harness exports:

```text
data/scorecard/golden_tasks.jsonl
```

It contains exactly:

- 20 short prompt coding tasks
- 20 debugging tasks
- 20 security finding tasks
- 20 patch generation tasks
- 10 long-context repo tasks

#### Run Both Modes

```powershell
.\scripts\run_scorecard.ps1
```

Default:

- Mock mode checks architecture plumbing.
- Real mode checks an OpenAI/vLLM-compatible backend.
- Local default endpoint is `http://127.0.0.1:8000/v1`.

#### Run Against A Real Model

```powershell
$env:SCORECARD_REAL_BACKEND = "openai_compatible"
$env:SCORECARD_MODEL_ENDPOINT = "http://your-model-server:8000/v1"
$env:SCORECARD_MODEL_NAME = "your-model"
$env:SCORECARD_REQUIRE_REAL_READY = "1"
.\scripts\run_scorecard.ps1
```

#### Failure Auto Report

Every failed task reports:

- which task failed
- which phase failed
- whether it looks like memory, planner, sandbox, security, patch, or model output
- exact fix recommendation

Reports are written to:

```text
artifacts/scorecard/
```

The latest report is also exposed through the chat API:

```text
GET /v1/scorecard/latest
```

---

