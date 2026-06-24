$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)
$endpoint = if ($env:PHASE32_ENDPOINT) { $env:PHASE32_ENDPOINT } else { "http://127.0.0.1:8016/v1" }
$model = if ($env:PHASE32_MODEL) { $env:PHASE32_MODEL } else { "Qwen/Qwen2.5-Coder-0.5B-Instruct" }
python src\phase32\critic_endpoint_contract.py --endpoint $endpoint --model $model

