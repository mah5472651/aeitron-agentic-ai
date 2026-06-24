$ErrorActionPreference = "Stop"

Set-Location (Split-Path -Parent $PSScriptRoot)

if ($env:PHASE13_START_SERVING -ne "0") {
    powershell -ExecutionPolicy Bypass -File scripts\start_dev_serving_mock.ps1 | Out-Host
}

$suite = if ($env:PHASE13_SUITE) { $env:PHASE13_SUITE } else { "quick" }
$runId = if ($env:PHASE13_RUN_ID) { $env:PHASE13_RUN_ID } else { "phase13-local-quality" }
$baseline = if ($env:PHASE13_BASELINE_BACKEND) { $env:PHASE13_BASELINE_BACKEND } else { "mock" }
$candidate = if ($env:PHASE13_CANDIDATE_BACKEND) { $env:PHASE13_CANDIDATE_BACKEND } else { "openai_compatible" }
$concurrency = if ($env:PHASE13_CONCURRENCY) { [int]$env:PHASE13_CONCURRENCY } else { 4 }

if (-not $env:PHASE13_MODEL_ENDPOINT) {
    $env:PHASE13_MODEL_ENDPOINT = "http://127.0.0.1:8000/v1"
}
if (-not $env:PHASE13_MODEL_NAME) {
    $env:PHASE13_MODEL_NAME = "security-coder"
}

$argsList = @(
    "src\phase13\backend_quality_harness.py",
    "--run-id", $runId,
    "--suite", $suite,
    "--baseline-backend", $baseline,
    "--candidate-backend", $candidate,
    "--concurrency", "$concurrency"
)

if ($env:PHASE13_MAX_TASKS) {
    $argsList += @("--max-tasks", "$env:PHASE13_MAX_TASKS")
}

if ($env:PHASE13_STRICT -eq "1") {
    $argsList += "--strict"
}

python @argsList
