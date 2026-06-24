# Phases 19-23 Quality Hardening

These phases strengthen the architecture around the seven priority pillars:
planner, multi-agent system, memory, critic, verification, security experts,
and high-quality coding/reasoning data.

## Phase 19: Verifier Registry

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

## Phase 20: TaskGraph Runtime

TaskGraph-first agent runtime using role-specific workers, critic review, and
optional verifier execution.

```powershell
$env:PHASE20_RUN_VERIFIER = "1"
.\scripts\run_phase20_taskgraph_runtime.ps1
```

## Phase 21: Experience Promotion

Promotes Phase 18/19/20 failures and outcomes into experience memory. Local
JSONL works now; Postgres/Qdrant can be enabled with env vars.

```powershell
.\scripts\run_phase21_experience_promotion.ps1
```

## Phase 22: Critic Backend

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

## Phase 23: Model Quality Profiles

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

## API Endpoints

- `GET /v1/verifier/latest`
- `POST /v1/verifier/run`
- `GET /v1/taskgraph/latest`
- `POST /v1/taskgraph/run`
- `GET /v1/quality/latest` includes Phases 19-23

