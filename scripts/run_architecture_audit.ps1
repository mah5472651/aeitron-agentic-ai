$ErrorActionPreference = "Stop"

Set-Location (Split-Path -Parent $PSScriptRoot)
$gatewayPort = if ($env:GATEWAY_PORT) { [int]$env:GATEWAY_PORT } else { 18080 }

python src\phase10\architecture_readiness_audit.py `
    --run-id topclass-readiness `
    --tokenizer artifacts\mvp\code_bpe_tokenizer\tokenizer.json `
    --postgres-dsn "postgresql://ai:ai_dev_password@localhost:5432/ai_eval" `
    --redis-url redis://127.0.0.1:6379/0 `
    --qdrant-url http://127.0.0.1:6333 `
    --gateway-url "http://127.0.0.1:$gatewayPort" `
    --vllm-url http://127.0.0.1:8000
