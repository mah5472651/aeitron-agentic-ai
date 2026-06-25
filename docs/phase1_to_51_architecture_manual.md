# Phase 1-51 Architecture Manual

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

## Phase 1: Data Ingestion And Code Tokenizer

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

## Phase 2: Docker Sandbox Engine

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

## Phase 3: Rejection Sampling Pipeline

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

## Phase 4: Dynamic Swarm Orchestrator

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

## Phase 5: Self-Healing Runtime

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

## Phase 6: Redis Regenerative Quota Engine

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

## Phase 7: GRPO Training Loop

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

## Phase 8: Serving Stack

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

## Phase 9: Evaluation Harness

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

## Phase 10: Deployment Smoke And Readiness Audit

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

## Phase 11: PyTorch AI Core And Chat Interface

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

## Phase 12: Capability Gauntlet

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

## Phase 13: Backend Quality Harness

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

## Phase 14: Exact Scorecard Harness

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

## Phase 15: Target Architecture Blueprint

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

## Phase 16: Core Architecture Upgrades

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

## Phase 17: GPU 7B-32B Readiness

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

## Phase 18: Real Model Quality Loop

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

## Phase 19: Unified Verifier Registry

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

## Phase 20: TaskGraph Runtime

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

## Phase 21: Experience Promotion

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

## Phase 22: Critic Service

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

## Phase 23: Model Quality Profiles

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

## Phase 24: Main Agent V2

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

## Phase 25: Experience Retrieval

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

## Phase 26: Patch Manager

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

## Phase 27: Verifier Policy Engine

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

## Phase 28: Security Expert Workflow

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

## Phase 29: Dataset Review Gate

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

## Phase 30: Expanded Golden Benchmark

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

## Phase 31: Long Context Packer

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

## Phase 32: Critic Endpoint Contract

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

## Phase 33: GPU Backend Contract

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

## Phase 34: Auth And Quota Control

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

## Phase 35: Observability

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

## Phase 36: Data Flywheel

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

## Phase 37: Production Vector Memory

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

## Phase 38: Multi-Language Security Engine

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

## Phase 39: Training Checkpoint Rollback Gate

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

## Phase 40: Integrated Default Agent Runtime

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
- On `qwen-cpu-smoke`, Phase 40 uses mock orchestration by default so local
  agent calls stay responsive; GPU profiles use active model orchestration.

When to use:

- For serious agentic coding, debugging, and security work.
- This is now the default `/v1/agent/run` path.

Outputs:

- `artifacts/phase40/integrated-agent-latest.json`
- `artifacts/phase40/failure-events.jsonl`

## Phase 41: Real Task Regression Pack

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

## Phase 42: Production Profile Switcher

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

## Phase 43: Meta Planner

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

## Phase 44: Intent Expansion Engine

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

## Phase 45: Parallel Agent Runtime

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

## Phase 46: Hierarchical Memory

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

## Phase 47: Reasoning Engine

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

## Phase 48: Knowledge Graph

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

## Phase 49: Multimodal Expert

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

## Phase 50: MoE Router Layer

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

## How The Pieces Work Together

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

## Important Commands

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
python src\phase42\profile_switcher.py --profile qwen-cpu-smoke --activate
```

## Latest Debug Status

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
- Current live model is a small CPU Qwen smoke backend, not the final 7B-32B or
  50B-100B target.

## Phase 51: High-Stability Reasoning And Unified Memory

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

## Mythos Architecture V1 Productization

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
