# Mythos Target Architecture

Target: A coding and cybersecurity AI system in the spirit of Cursor + Claude Code + DeepSeek R1, with a practical base-model start and a future 50B-100B+
scale path.

## Core Pipeline

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

## Practical Model Strategy

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

## Future Scale

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



## What Actually Creates A Top AI

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
- Local real Qwen backend connected through an OpenAI-compatible API
- Main chat API is currently connected to Qwen/Qwen2.5-Coder-0.5B-Instruct
- Qwen model revision is pinned for safer reproducible local loading
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
- The V1 release gate currently passes locally in full mode with a release decision; the only warning is that the real Qwen endpoint is unavailable during the local CPU startup window.

Main gaps:

- Current live local backend is a small CPU Qwen model for immediate UI/API behavior checks; the 7B-32B GPU backend/training path is now prepared but cannot be executed on this Windows CPU machine.
- The exact scorecard loop is implemented for Qwen; it should be run regularly in quick mode on CPU and full mode on a stronger GPU/vLLM backend.
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

## Immediate Build Order

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

## What We Need Next To Make It Stronger

- A stronger real model than the 0.5B CPU Qwen smoke model: practical next target is Qwen/DeepSeek/Llama coder class 7B-14B, then 32B.
- A real critic backend trained or prompted specifically to find coding, security, planning, and verification mistakes.
- More golden tasks with hard short prompts, multi-file repos, failing tests, and security patch cases.
- A production memory promotion loop: failure -> fix -> outcome -> embedding -> retrieval during planning. Phase 37 now provides the retrieval layer; next step is making it default in every planner run.
- A verifier registry that combines sandbox tests, Semgrep, CodeQL, security rules, benchmark tests, and peer review.
- A dataset review gate so bad synthetic outputs do not enter SFT/GRPO training.
- A Linux CUDA environment for serious model quality work.
- Real checkpoint promotion policy: Phase 39 now protects checkpoints; next step is connecting it directly after every training/evaluation job.

## Safety Position

Security tooling stays defensive:

- Static analysis
- Vulnerability detection
- Patch generation
- Verification

No autonomous exploit execution.

## Generated Blueprint

Machine-readable and markdown blueprint:

```powershell
.\scripts\write_target_architecture.ps1
```

Outputs:

```text
artifacts/phase15/mythos-target-architecture.json
artifacts/phase15/mythos-target-architecture.md
```
