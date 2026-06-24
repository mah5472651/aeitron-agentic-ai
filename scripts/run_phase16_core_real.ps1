$ErrorActionPreference = "Stop"

Set-Location (Split-Path -Parent $PSScriptRoot)

$port = if ($env:PHASE16_REAL_PORT) { [int]$env:PHASE16_REAL_PORT } else { 8016 }
$modelId = if ($env:PHASE16_HF_MODEL_ID) { $env:PHASE16_HF_MODEL_ID } else { "Qwen/Qwen2.5-Coder-0.5B-Instruct" }

$env:PHASE16_BACKEND = "openai_compatible"
$env:PHASE16_MODEL_ENDPOINT = "http://127.0.0.1:$port/v1"
$env:PHASE16_MODEL_NAME = $modelId

python src\phase16\smoke_test.py --json

