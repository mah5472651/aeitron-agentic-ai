$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)
$profile = if ($env:PHASE33_PROFILE) { $env:PHASE33_PROFILE } else { "qwen2.5-coder-7b" }
$endpoint = if ($env:PHASE33_ENDPOINT) { $env:PHASE33_ENDPOINT } else { "http://127.0.0.1:8000/v1" }
python src\phase33\gpu_backend_contract.py --profile $profile --endpoint $endpoint
