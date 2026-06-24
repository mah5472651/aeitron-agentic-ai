# Phase 10: Deployment Doctor and E2E Smoke Runner

Phase 10 verifies that the architecture is connected end to end before training
or production deployment.

## Offline Code-Level Smoke

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

## Live Infrastructure Smoke

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

## Status Meaning

- `ok`: check succeeded.
- `warn`: code works but important optional runtime packages are missing.
- `skip`: intentionally skipped, usually by `--offline` or missing DSN.
- `fail`: a required check failed.
