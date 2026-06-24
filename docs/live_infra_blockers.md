# Current Live Infrastructure Status

The local architecture path is running on this Windows workstation. Docker
Desktop is active, and the development Redis/PostgreSQL/Qdrant services are
healthy through `deploy/dev/docker-compose.yml`.

## Working Locally

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

## Remaining Production Blockers

- NVIDIA/CUDA is not detected on this machine through `nvidia-smi`.
- `deepspeed` does not install cleanly on the current Windows/Python 3.14
  environment.
- `autoawq`/`awq` is blocked locally because Triton does not provide a matching
  Windows wheel for this runtime.

These blockers do not prevent local architecture, sandbox, quota, gateway, or
evaluation development. They do block real 7B-13B GRPO training, DeepSpeed
ZeRO-2 runs, AWQ quantization, and production vLLM serving.

## Daily Readiness Check

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

## Production GPU Path

Use an Ubuntu Linux host with NVIDIA drivers, CUDA, and a supported Python
runtime for the full training/serving stack:

- DeepSpeed ZeRO-2 GRPO training.
- AWQ INT4 quantization.
- Real vLLM PagedAttention serving with tensor parallelism.
- Large benchmark runs against full model checkpoints.
