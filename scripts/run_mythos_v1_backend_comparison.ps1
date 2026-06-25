$ErrorActionPreference = "Stop"

Set-Location (Split-Path -Parent $PSScriptRoot)

$suite = if ($env:MYTHOS_BACKEND_SUITE) { $env:MYTHOS_BACKEND_SUITE } else { "quick" }
python src\mythos_v1\backend_comparison.py `
    --run-id mythos-v1-backend `
    --suite $suite

