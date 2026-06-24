# Phases 34-36 Production Control Layer

These phases add production control around the existing agent architecture:
authentication, quota, observability, and data flywheel automation.

## Phase 34: Auth + Quota

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

## Phase 35: Observability

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

## Phase 36: Data Flywheel

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
