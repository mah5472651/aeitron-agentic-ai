$ErrorActionPreference = "Stop"

Set-Location (Split-Path -Parent $PSScriptRoot)

python src\mythos_v1\training_preflight.py `
    --run-id mythos-v1-training `
    --strict-architecture

