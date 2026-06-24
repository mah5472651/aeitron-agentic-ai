# Phase 11 PyTorch AI Core

Phase 11 is the architecture layer for the future model. It does not require
CUDA training yet. It gives us a strong controllable shell for chat, memory,
agentic coding, security review, and later PyTorch checkpoint integration.

## Components

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

## Run

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

## Architecture Roadmap

```powershell
curl.exe http://127.0.0.1:8090/v1/architecture/roadmap
```

Detailed plan:

```text
docs/phase11_20_step_architecture_plan.md
```

## Backend Modes

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

## Why This Matters

Short prompts become stronger because the runtime expands intent, loads project
context, checks security risk, and routes the request through a consistent
engineering workflow before answering.

Large-context work becomes safer because context is not just a long prompt. It
is packed from source files, symbols, call graph records, memory, and security
signals.

Future training can replace the backend without replacing the architecture.
