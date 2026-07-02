# Mythos Agentic AI

Mythos is an AI coding-agent backend for repository understanding, code editing,
patch verification, and model-agnostic serving.

The final architecture lives under `src/mythos`. The old numbered
architecture has been removed.

## What Works Now

- FastAPI gateway
- JWT auth middleware
- Model-agnostic backend adapter
- Project and session APIs
- Repository indexing
- Context building
- Durable TaskGraph runtime
- Tool command execution
- Patch preview/apply/rollback
- Verifier runtime
- Native MVP tests

## Repository Layout

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
tests/
scripts/
deploy/
docs/
```

## Quick Check

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\run_mythos_mvp_foundation.ps1
```

## Start Gateway

```powershell
python -m uvicorn src.mythos.gateway.api:app --host 127.0.0.1 --port 8090
```

## Run CLI

```powershell
python -m src.mythos.cli --prompt "fix auth bug" --workspace . --agent-backend-mode mock --no-verifier --no-security
```

## Real Model Serving

Set:

```powershell
$env:MYTHOS_MODEL_BACKEND = "openai_compatible"
$env:MYTHOS_MODEL_ENDPOINT = "http://127.0.0.1:8000/v1"
$env:MYTHOS_MODEL_NAME = "Qwen/Qwen2.5-Coder-7B-Instruct"
```

Then run a vLLM OpenAI-compatible server separately on GPU hardware.

## Final Rule

All new production code belongs under `src/mythos`.
