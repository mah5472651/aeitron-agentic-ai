$ErrorActionPreference = "Stop"

Set-Location (Split-Path -Parent $PSScriptRoot)

if ($env:SCORECARD_START_SERVING -ne "0") {
    powershell -ExecutionPolicy Bypass -File scripts\start_dev_serving_mock.ps1 | Out-Host
}

$mode = if ($env:SCORECARD_MODE) { $env:SCORECARD_MODE } else { "both" }
$runId = if ($env:SCORECARD_RUN_ID) { $env:SCORECARD_RUN_ID } else { "scorecard-local" }
$realBackend = if ($env:SCORECARD_REAL_BACKEND) { $env:SCORECARD_REAL_BACKEND } else { "openai_compatible" }
$concurrency = if ($env:SCORECARD_CONCURRENCY) { [int]$env:SCORECARD_CONCURRENCY } else { 4 }
$contextBudget = if ($env:SCORECARD_CONTEXT_BUDGET) { [int]$env:SCORECARD_CONTEXT_BUDGET } else { 2500 }

if (-not $env:SCORECARD_MODEL_ENDPOINT) {
    $env:SCORECARD_MODEL_ENDPOINT = "http://127.0.0.1:8000/v1"
}
if (-not $env:SCORECARD_MODEL_NAME) {
    $env:SCORECARD_MODEL_NAME = "security-coder"
}

$argsList = @(
    "src\phase14\scorecard_harness.py",
    "--run-id", $runId,
    "--mode", $mode,
    "--real-backend", $realBackend,
    "--concurrency", "$concurrency",
    "--context-budget", "$contextBudget",
    "--run-sandbox",
    "--strict"
)

if ($env:SCORECARD_REQUIRE_REAL_READY -eq "1") {
    $argsList += "--require-real-ready"
}

python @argsList
