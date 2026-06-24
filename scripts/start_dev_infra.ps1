$ErrorActionPreference = "Stop"

Set-Location (Split-Path -Parent $PSScriptRoot)

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw "Docker CLI not found. Install/start Docker Desktop first."
}

docker compose -f deploy/dev/docker-compose.yml up -d

Write-Host "Waiting for dev services..."
Start-Sleep -Seconds 5
$gatewayPort = if ($env:GATEWAY_PORT) { [int]$env:GATEWAY_PORT } else { 18080 }

python src\phase10\e2e_smoke_runner.py `
  --run-id dev-infra-smoke `
  --redis-url redis://127.0.0.1:6379/0 `
  --postgres-dsn "postgresql://ai:ai_dev_password@localhost:5432/ai_eval" `
  --qdrant-url http://localhost:6333 `
  --gateway-url "http://localhost:$gatewayPort" `
  --vllm-url http://localhost:8000
