# AI Architecture Build - Mythos V1

# Mythos AI

> A next-generation Agentic AI Architecture for Coding, Cybersecurity, Reasoning, Memory, and Autonomous Execution.

Mythos AI is an advanced AI systems architecture inspired by Cursor, Claude Code, DeepSeek-R1, and modern agentic AI research.

The goal is to build a scalable AI platform capable of:

* Multi-Agent Collaboration
* Autonomous Planning & Execution
* Hierarchical Memory Systems
* Long-Context Reasoning
* Cybersecurity Analysis
* Verification & Self-Correction
* Knowledge Graph Integration
* Future 7B–100B+ Model Deployment

---

## Architecture Overview

User
→ Intent Engine
→ Meta Planner
→ Task Graph
→ Agent Orchestrator
→ Memory Layer
→ Reasoning Engine
→ Critic
→ Verifier
→ Final Response

---

## Core Capabilities

✅ Multi-Agent Runtime

✅ Hierarchical Memory

✅ Thinker → Critic → Verifier Pipeline

✅ Security Analysis Framework

✅ Vector Memory Retrieval

✅ Knowledge Graph

✅ Long Context Processing

✅ MoE Routing System

✅ Evaluation & Benchmarking

✅ Training Data Flywheel

---

## Current Status

* 51+ architecture modules implemented
* Local Qwen backend integration completed
* Vector memory and reasoning layers operational
* Security verification workflows integrated
* GPU-ready 7B–32B deployment path prepared

---

## Long-Term Vision

Build a world-class AI platform capable of competing with modern coding and reasoning systems while maintaining strong security, verification, and memory capabilities.

Target Scale:

* 7B Models
* 14B Models
* 32B Models
* Future 50B–100B+ Research Systems

## Mythos V1 Release Gate

The architecture is consolidated around Phase 40 as the main runtime. Run the
local product release gate before accepting architecture changes:

```powershell
.\scripts\run_mythos_v1_release.ps1
```

The gate checks compilation, static security, strict reasoning and memory,
integrated agent execution, 400 regression tasks, the exact 90-task golden
scorecard, and GPU-training architecture readiness. See
`docs/mythos_v1_productization.md` for full and real-backend modes.

Foundation data ingestion and tokenizer setup for a deliberative LLM pipeline.

## What is included

- `src/phase1/callgraph_extractor.py`
  - Scans raw repositories.
  - Uses tree-sitter multi-language parsing for Python, C, C++, Rust, and Bash.
  - Parses modules into ASTs, extracts function signatures, return types, state
    mutations, dependencies, and call sites.
  - Builds a two-pass globally resolved inter-procedural call graph.
  - Emits dense function-level metadata JSONL plus optional expanded graph JSON.

- `src/phase1/train_code_bpe_tokenizer.py`
  - Trains a Hugging Face raw `tokenizers` byte-level BPE tokenizer with exactly
    64,000 vocabulary entries.
  - Injects reasoning/control tokens, call-graph markers, compile/exploit markers,
    heap allocation markers, and `0x00` through `0xff` hex byte tokens.
  - Biases merge rules toward 2-space, 4-space, 8-space, and deeper indentation
    blocks, memory addresses, heap traces, compiler errors, and hex dumps.
  - Includes CLI tools for training, encoding, decoding, and benchmarking.

- `src/phase2/docker_sandbox_engine.py`
  - Fully async Docker SDK sandbox daemon with a bounded isolated execution pool.
  - Enforces `network_mode="none"`, read-only root, read-only `/workspace`,
    `/tmp` tmpfs at exactly `32m`, dropped capabilities, no privilege escalation,
    unprivileged UID/GID, exactly 1 CPU core, exactly 512MB RAM, and a fixed
    5,000ms timeout.
  - Accepts file-tree layouts, raw source snippets, compiler strings, run commands,
    and returns stdout, stderr, exit code, `<|timeout|>`, CPU microseconds, wall
    microseconds, and RAM footprint metrics.

- `src/phase3/rejection_sampling_pipeline.py`
  - Multi-threaded rejection sampling pipeline for leakage-free SFT curation.
  - Prompts a base reasoning model with vulnerable source plus AST/call-graph
    metadata while withholding ground-truth patches, fix explanations, oracle data,
    and CVE identifiers.
  - Requires the exact token schema
    `<|thought_start|>...<|thought_end|><|patch_start|>...<|patch_end|>`.
  - Injects proposed patches into the sandbox file tree and runs the verification
    suite exactly once.
  - Appends only first-pass sandbox exit-code-0 successes to token-ready JSONL:
    `{ "prompt": "...", "chosen": "..." }`.

- `src/phase4/swarm_orchestrator.py`
  - Native `asyncio` swarm orchestrator with no CrewAI/AutoGen dependency.
  - Uses Pydantic v2 schemas for all task graphs, bus packets, artifacts, reviews,
    correction requests, and final reports.
  - Master planner asks a high-reasoning LLM backend for a prioritized DAG of
    subtasks, then dynamically instantiates role-specific micro-agents.
  - Includes an async data bus for structured JSON context packets between agents.
  - Automatically routes code artifacts to an adversarial `SecurityReviewerAgent`.
  - If review confidence falls below `0.85`, schedules an iterative correction
    cycle through a `CorrectionAgent`.

- `src/phase5/self_healing_runtime.py`
  - Integrated async self-healing runner around the swarm reasoning path and Phase 2
    sandbox execution.
  - Captures compiler warnings/errors, Python tracebacks, sanitizer findings, GDB/core
    dump frames, register addresses, timeout flags, CPU metrics, and RAM metrics.
  - Recursively injects telemetry into the repair context for exactly five maximum
    correction iterations.
  - Converts successful lifecycles into token-ready training traces and streams them
    through an async staging worker into PostgreSQL/Qdrant.
  - Includes a cron-style monitor that queues offline QLoRA batch jobs when the
    staging buffer reaches a threshold block size.

- `src/phase6/redis_quota_engine.py`
  - Redis-backed continuous regenerative quota engine.
  - Uses an atomic Lua token bucket with dynamic floating-point time-delta refill.
  - Stores each user as a Redis hash with exactly `tokens_balance` and
    `last_updated_timestamp`.
  - Includes a lazy FastAPI middleware wrapper that calls the Lua script and emits
    quota response headers.

- `src/phase6/redis_regenerative_bucket.lua`
  - Enterprise Redis Lua script that atomically applies
    `Tokens_current = min(C, Tokens_last + Delta_t * R)`, verifies request cost,
    decrements balance, updates timestamp, and returns allowed/remaining state.

- `src/phase7/grpo_training_loop.py`
  - Custom GRPO training loop for coding/cybersecurity policy optimization.
  - Generates `G=8` candidates per prompt at `temperature=0.8`.
  - Computes group-normalized advantages and PPO-style clipped policy loss.
  - Adds frozen-reference KL penalty with `beta=0.01`.
  - Scores candidates with sandbox execution, static security, format compliance,
    and execution efficiency rewards.
  - Supports gradient checkpointing, bf16, DeepSpeed ZeRO-2, and W&B metrics.

- `src/phase8/vllm_server.py`, `src/phase8/gateway.py`, `src/phase8/quantize_awq.py`
  - Production vLLM serving stack for 7B-13B coding/security models.
  - AWQ INT4 loading, tensor parallel size 2, continuous batching, and high GPU
    KV-cache utilization.
  - FastAPI gateway with SSE streaming, priority lanes, prompt routing, and
    Kubernetes health probes.
  - AWQ calibration/quantization and HumanEval benchmarking helper.

- `src/phase9/evaluate.py` and `src/phase9/*`
  - Automated post-training evaluation harness for coding and cybersecurity models.
  - Runs HumanEval pass@1/pass@10, MBPP pass@1, CyberSecEval 2 insecure-code rate,
    and a built-in 200-case custom security suite.
  - Executes generated benchmark code through the Phase 2 sandbox.
  - Stores regression history in PostgreSQL or JSONL, generates Markdown reports,
    and sends Slack/Discord webhook alerts on score drops over 2%.
  - Includes head-to-head checkpoint comparison with an LLM judge.

- `src/phase10/e2e_smoke_runner.py`
  - Deployment doctor and end-to-end smoke runner.
  - Verifies compile health, tokenizer loading, Phase 4 swarm mock, Phase 9 custom
    security suite, and optional live services: Docker, Redis, PostgreSQL, Qdrant,
    vLLM, gateway, and sandbox execution.

- `src/phase11/*`
  - PyTorch-native AI core architecture shell for the future model.
  - Includes a decoder-only transformer skeleton, unified model backends, long
    context memory, short-prompt intent expansion, security reasoning, agentic
    coding runtime v2, and a FastAPI chat interface.
  - Serves a separate static chat UI from `src/phase11/static` with workspace
    logo support through `/brand/logo`.
  - Tracks the six architecture areas as twenty build steps in
    `docs/phase11_20_step_architecture_plan.md`.
  - Runs locally with a deterministic mock backend now, and can later swap in a
    trained PyTorch checkpoint or vLLM/OpenAI-compatible model endpoint.

- `src/phase16/*`
  - Core architecture upgrades for the Cursor + Claude Code + DeepSeek-style
    target system.
  - Adds a durable TaskGraph planner/store, role-specific async micro-agents,
    critic/verifier backends, ExperienceMemory, defensive Git/Semgrep/CodeQL/
    browser tool adapters, real model endpoint probing, and scorecard failure
    export into SFT/GRPO JSONL candidates.
  - Runs through `scripts/run_phase16_core.ps1` and writes reports under
    `artifacts/phase16/`.

## Install

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements-local-dev.txt
```

Use `requirements-linux-gpu.txt` on an Ubuntu/CUDA GPU host for DeepSpeed,
AWQ, and real vLLM serving. The root `requirements.txt` keeps the complete
phase dependency list, but Windows local development should use the local-dev
file above.

## Extract call graphs

```powershell
python src\phase1\callgraph_extractor.py `
  --repo C:\path\to\raw_repo `
  --out-jsonl artifacts\ast_graph.jsonl `
  --out-graph artifacts\callgraph.json `
  --mermaid-out artifacts\callgraph.mmd
```

The dense JSONL output contains one record per function block:

- `sig`: extracted input/output signature.
- `in` / `out`: parameters and return types.
- `mut`: internal variable state mutations.
- `dep`: imports/includes/use/source dependencies.
- `call`: internal/external call sites with global resolution confidence.
- `ah` / `sh`: AST-block and source-file content hashes.

## Train code BPE tokenizer

```powershell
python src\phase1\train_code_bpe_tokenizer.py train `
  --input C:\path\to\corpus_or_repo `
  --output-dir artifacts\code_bpe_tokenizer `
  --vocab-size 64000 `
  --min-frequency 2
```

The tokenizer pipeline enforces an exact 64,000-token vocabulary. If a small
corpus cannot naturally fill all 64,000 entries, reserved special tokens are
added so model/tokenizer configuration remains stable.

## Encode, decode, and benchmark

```powershell
python src\phase1\train_code_bpe_tokenizer.py encode `
  --tokenizer artifacts\code_bpe_tokenizer\tokenizer.json `
  --text "def add(a, b):`n    return a + b"

python src\phase1\train_code_bpe_tokenizer.py benchmark `
  --tokenizer artifacts\code_bpe_tokenizer\tokenizer.json `
  --input C:\path\to\repo
```

## Run code in the isolated Docker sandbox

Install Docker Desktop first, then make sure the chosen image already contains
the compiler/runtime you need. For example, `ubuntu:24.04` is minimal and may
need a prebuilt internal image for `gcc`, `g++`, `rustc`, or `python3`.

```powershell
python src\phase2\docker_sandbox_engine.py run-code `
  --language python `
  --image python:3.12-slim `
  --code "print('hello from sandbox')"
```

Compile and run C++ with a compiler string that writes binaries only to `/tmp`:

```powershell
python src\phase2\docker_sandbox_engine.py run-code `
  --language cpp `
  --image gcc:14 `
  --compiler "g++ -O3 /workspace/main.cpp -o /tmp/main_bin" `
  --run "/tmp/main_bin" `
  --code "#include <iostream>`nint main(){ std::cout << 42 << '\n'; }"
```

For multiple files or custom commands, use JSON:

```json
{
  "image": "python:3.12-slim",
  "files": [
    {
      "path": "main.py",
      "content": "import helper\nprint(helper.add(2, 3))\n"
    },
    {
      "path": "helper.py",
      "content": "def add(a, b):\n    return a + b\n"
    }
  ],
  "compile_command": null,
  "run_command": "python3 /workspace/main.py",
  "bash_args": ["-lc"]
}
```

```powershell
python src\phase2\docker_sandbox_engine.py run-json --request request.json
```

The root filesystem and source workspace are read-only. Build artifacts, caches,
and temporary files must be written under `/tmp`, which is a 32MB noexec/nosuid
RAM disk.

## Curate SFT data with first-pass rejection sampling

Input samples are JSONL. Hidden test files and commands are used only inside the
sandbox evaluator, never in the model prompt.

```json
{
  "sample_id": "py-path-traversal-001",
  "language": "python",
  "image": "python:3.12-slim",
  "source_files": [
    {
      "path": "app.py",
      "content": "def read_user_file(base, name):\n    return open(base + '/' + name).read()\n"
    }
  ],
  "ast_context_graph": {
    "nodes": [],
    "edges": []
  },
  "hidden_test_files": [
    {
      "path": "test_app.py",
      "content": "from app import read_user_file\nimport tempfile, pathlib\nwith tempfile.TemporaryDirectory() as d:\n    p = pathlib.Path(d)\n    (p / 'ok.txt').write_text('ok')\n    assert read_user_file(str(p), 'ok.txt') == 'ok'\n    try:\n        read_user_file(str(p), '../secret.txt')\n        raise AssertionError('path traversal allowed')\n    except Exception:\n        pass\n"
    }
  ],
  "test_command": ["python3", "/workspace/test_app.py"],
  "timeout_seconds": 10,
  "memory": "256m"
}
```

The model must return exactly:

```text
<|thought_start|>reasoning here<|thought_end|><|patch_start|>{"files":[{"path":"app.py","content":"complete replacement file"}]}<|patch_end|>
```

The patch block may be either JSON full-file replacements or a unified diff
against files present in the prompt. Accepted SFT output is intentionally minimal:

```json
{"prompt": "...", "chosen": "<|thought_start|>...<|thought_end|><|patch_start|>...<|patch_end|>"}
```

Run against an OpenAI-compatible local/base-model endpoint:

```powershell
python src\phase3\rejection_sampling_pipeline.py `
  --input-jsonl artifacts\vulnerable_samples.jsonl `
  --dataset-out artifacts\sft_accepted.jsonl `
  --rejected-out artifacts\sft_rejected.jsonl `
  --endpoint http://localhost:8000/v1 `
  --model base-reasoning-model `
  --workers 8
```

For deterministic offline testing, pass `--mock-response-file` with JSONL records
containing either a full model JSON response or `{ "response": "..." }`.

## Run the dynamic swarm orchestrator

Mock mode uses deterministic local agents and is useful for testing the control
flow without a model server. The mock harness intentionally generates an unsafe
`eval` code artifact, rejects it through security review, then corrects it:

```powershell
python src\phase4\swarm_orchestrator.py `
  --mock `
  --prompt "Build a secure Docker sandbox, database optimizer, and adversarial code review workflow"
```

With an OpenAI-compatible agent model endpoint:

```powershell
python src\phase4\swarm_orchestrator.py `
  --endpoint http://localhost:8000/v1 `
  --model high-reasoning-agent-model `
  --prompt "Design a production multi-agent code repair platform with security review"
```

## Run the self-healing runtime loop

The self-healing request is a JSON file containing the original intent, current
source files, the failed reasoning path, and the sandbox execution command.

```json
{
  "initial_prompt": "Implement a safe add function that passes the test suite.",
  "failed_reasoning_path": [
    {
      "step": "initial_patch",
      "detail": "Generated subtraction by mistake."
    }
  ],
  "source_files": [
    {
      "path": "app.py",
      "content": "def add(a, b):\n    return a - b\n"
    },
    {
      "path": "test_app.py",
      "content": "from app import add\nassert add(2, 3) == 5\n"
    }
  ],
  "execution": {
    "image": "python:3.12-slim",
    "compile_command": null,
    "run_command": "python3 /workspace/test_app.py",
    "bash_args": ["-lc"]
  }
}
```

Run with database streaming:

```powershell
python src\phase5\self_healing_runtime.py run `
  --request-json artifacts\healing_request.json `
  --endpoint http://localhost:8000/v1 `
  --model repair-model `
  --postgres-dsn "postgresql://user:pass@localhost:5432/ai_buffer" `
  --qdrant-url "http://localhost:6333" `
  --init-db
```

For local tests without databases, use a JSONL fallback:

```powershell
python src\phase5\self_healing_runtime.py run `
  --request-json artifacts\healing_request.json `
  --mock-response-file artifacts\mock_healing_responses.jsonl `
  --jsonl-fallback artifacts\healing_buffer.jsonl
```

Queue QLoRA jobs when enough traces are staged:

```powershell
python src\phase5\self_healing_runtime.py cron `
  --postgres-dsn "postgresql://user:pass@localhost:5432/ai_buffer" `
  --threshold-block-size 128 `
  --dataset-dir artifacts\qlora_batches `
  --base-model baseline-reasoning-model `
  --once
```

PostgreSQL tables created by `--init-db`:

- `healing_lifecycle_traces`: successful self-healing traces and token-ready sequences.
- `healing_repair_iterations`: every recursive repair attempt up to depth 5.
- `qlora_training_jobs`: queued offline QLoRA jobs.

Qdrant collection:

- `self_healing_qlora_staging`: vector index over prompt, failed reasoning,
  telemetry, corrected reasoning, and successful patch.

## Run the Redis regenerative quota engine

Seed a user account:

```powershell
python src\phase6\redis_quota_engine.py `
  seed `
  --redis-url redis://127.0.0.1:6379/0 `
  --user-id user_123 `
  --capacity 300 `
  --refill-rate 0.083333333
```

Consume quota:

```powershell
python src\phase6\redis_quota_engine.py `
  consume `
  --redis-url redis://localhost:6379/0 `
  --user-id user_123 `
  --capacity 300 `
  --refill-rate 0.083333333 `
  --cost 7.5
```

The core middleware call is:

```python
decision = await enforce_quota(
    engine,
    user_id="user_123",
    cost=7.5,
)
```

FastAPI app factory:

```python
from src.phase6.redis_quota_engine import QuotaPolicy, create_fastapi_app

app = create_fastapi_app(
    redis_url="redis://localhost:6379/0",
    policy=QuotaPolicy(capacity=300.0, refill_rate=5.0 / 60.0, tenant="prod"),
)
```

## Train with GRPO

```powershell
python src\phase7\grpo_training_loop.py `
  --model-name-or-path Qwen/Qwen2.5-Coder-1.5B-Instruct `
  --model-revision <pinned_commit_sha_or_tag> `
  --dataset artifacts\grpo_prompts.jsonl `
  --output-dir artifacts\grpo_policy `
  --bf16 `
  --gradient-checkpointing `
  --deepspeed `
  --wandb
```

## Serve with vLLM

```powershell
python src\phase8\vllm_server.py `
  --model C:\models\security-coder-awq `
  --served-model-name security-coder `
  --tensor-parallel-size 2 `
  --max-num-seqs 256 `
  --max-num-batched-tokens 8192 `
  --quantization awq
```

Gateway:

```powershell
python src\phase8\gateway.py --host 0.0.0.0 --port 8080
```

Docker Compose:

```powershell
docker compose -f deploy\phase8\docker-compose.yml up
```

## Evaluate after training

```powershell
python src\phase9\evaluate.py `
  --run-id run_001 `
  --endpoint http://localhost:8080/v1 `
  --model security-coder `
  --benchmarks humaneval mbpp cyberseceval2 custom_security `
  --humaneval-jsonl artifacts\benchmarks\humaneval.jsonl `
  --mbpp-jsonl artifacts\benchmarks\mbpp.jsonl `
  --cyberseceval2-jsonl artifacts\benchmarks\cyberseceval2.jsonl `
  --postgres-dsn "postgresql://user:pass@localhost:5432/ai_eval" `
  --regression-threshold 0.02 `
  --init-db
```

If HumanEval or MBPP are loaded through Hugging Face `datasets` instead of local
JSONL files, set `HF_DATASET_REVISION` to an exact dataset commit for
reproducible benchmark input.

Head-to-head checkpoint comparison:

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

## Deployment smoke test

One-command local MVP bootstrap:

```powershell
python src\phase10\bootstrap_mvp.py --run-id mvp-local-001
```

Offline code-level smoke:

```powershell
python src\phase10\e2e_smoke_runner.py --offline
```

Live infrastructure smoke:

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

Start local Redis/PostgreSQL/Qdrant dev services after Docker Desktop is ready:

```powershell
.\scripts\start_dev_infra.ps1
```

Start local mock serving path for gateway smoke tests without a GPU/vLLM install:

```powershell
.\scripts\start_dev_serving_mock.ps1
```

Install helper scripts:

```powershell
.\scripts\install_windows_prereqs.ps1 -InstallDocker -InstallPython312
.\scripts\check_runtime.ps1
```

If WSL is not installed, run PowerShell as Administrator:

```powershell
.\scripts\install_windows_prereqs.ps1 -EnableWSL -InstallDocker -InstallPython312
```

## Phase 1 implementation steps

1. Put raw repositories or exported GitHub files under a corpus directory.
2. Run `callgraph_extractor.py` over each repository and store one call graph per repo.
3. Train the tokenizer on the same raw repositories plus logs, traces, compiler errors,
   and sandbox execution output.
4. Use the call graph JSON as structured metadata during SFT sample generation.
5. Use the trained tokenizer for every later phase so traces, patches, and shell output
   share one stable vocabulary.
6. Send generated code through `docker_sandbox_engine.py` before rejection sampling,
   capture deterministic execution traces, and store the structured result beside the
   candidate answer.
7. Use `rejection_sampling_pipeline.py` to collect only first-pass successful repairs
   into the SFT JSONL. Keep rejected candidates only in the audit log, never in the
   SFT training set.
8. Use `swarm_orchestrator.py` for complex requests that need dynamic specialist
   decomposition, peer review, and validated aggregation.
9. Use `self_healing_runtime.py` to turn production sandbox failures into verified
   repair traces for asynchronous nightly QLoRA fine-tuning.
10. Use `redis_quota_engine.py` as the AI UI backend gate so user capacity
    regenerates continuously instead of waiting for static reset windows.
11. Run `phase9/evaluate.py` after each training run to catch coding and security
    regressions before deployment.
12. Run `phase10/e2e_smoke_runner.py` before training/deploy to confirm the
    architecture is connected end to end.
