$ErrorActionPreference = "Stop"

Set-Location (Split-Path -Parent $PSScriptRoot)

if (-not $env:PHASE11_BACKEND) {
    $env:PHASE11_BACKEND = "mock"
}
if (-not $env:PHASE11_WORKSPACE) {
    $env:PHASE11_WORKSPACE = (Get-Location).Path
}
$phase11Port = if ($env:PHASE11_PORT) { [int]$env:PHASE11_PORT } else { 8090 }

python -m uvicorn src.phase11.chat_api:app --host 127.0.0.1 --port $phase11Port
