# Phase 12 Capability Gauntlet

Phase 12 tests whether the architecture behaves like the AI system we want:
short prompts become actionable, agent workflows run end to end, security issues
are detected/reviewed, long-context retrieval finds the right files, tool calls
stay safe, and sandbox failures enter the self-healing path.

This is different from the Phase 10 readiness audit. Phase 10 asks whether the
stack boots. Phase 12 asks whether the architecture can perform the workflows.

## What It Tests

- Short prompt understanding
- Agentic coding workflow coverage
- Security reasoning and vulnerability triage
- Patch regression review
- Long-context memory retrieval and local vector search
- Tool safety boundaries
- Self-healing telemetry routing through the Docker sandbox

## Run Quick Suite

```powershell
.\scripts\run_phase12_gauntlet.ps1
```

Quick suite is designed for daily checks. It runs a representative subset of the
golden tasks.

## Run Full Suite

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

## Backend Modes

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

## How To Read Scores

- `overall_score`: average pass quality across non-skipped tasks.
- `architecture_ready`: true when there are no failed tasks and score is at
  least the configured pass score.
- `category_scores`: tells which architecture capability is weak.
- `recommendations`: next hardening actions generated from the scorecard.

With `mock`, a green score means the architecture is wired correctly. With a
real model backend, the same score begins to measure actual coding/security
quality.
