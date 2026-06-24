$ErrorActionPreference = "Stop"

Set-Location (Split-Path -Parent $PSScriptRoot)

$runId = if ($env:PHASE21_RUN_ID) { $env:PHASE21_RUN_ID } else { "phase21-local-promotion" }
$workspace = if ($env:PHASE21_WORKSPACE) { $env:PHASE21_WORKSPACE } else { (Get-Location).Path }

$argsList = @(
    "src\phase21\experience_promotion.py",
    "--run-id", $runId,
    "--workspace", $workspace,
    "--json"
)

if ($env:PHASE21_POSTGRES_DSN) { $argsList += @("--postgres-dsn", $env:PHASE21_POSTGRES_DSN) }
if ($env:PHASE21_QDRANT_URL) { $argsList += @("--qdrant-url", $env:PHASE21_QDRANT_URL) }
if ($env:PHASE21_REDIS_URL) { $argsList += @("--redis-url", $env:PHASE21_REDIS_URL) }

python @argsList

