$ErrorActionPreference = "Stop"

Set-Location (Split-Path -Parent $PSScriptRoot)

$suite = if ($env:PHASE12_SUITE) { $env:PHASE12_SUITE } else { "quick" }
$backend = if ($env:PHASE12_BACKEND) { $env:PHASE12_BACKEND } else { "mock" }
$runId = if ($env:PHASE12_RUN_ID) { $env:PHASE12_RUN_ID } else { "phase12-local-gauntlet" }
$concurrency = if ($env:PHASE12_CONCURRENCY) { [int]$env:PHASE12_CONCURRENCY } else { 4 }
$contextBudget = if ($env:PHASE12_CONTEXT_BUDGET) { [int]$env:PHASE12_CONTEXT_BUDGET } else { 2500 }

$argsList = @(
    "src\phase12\capability_gauntlet.py",
    "--run-id", $runId,
    "--suite", $suite,
    "--backend", $backend,
    "--concurrency", "$concurrency",
    "--context-budget", "$contextBudget",
    "--strict"
)

if ($env:PHASE12_RUN_SANDBOX -ne "0") {
    $argsList += "--run-sandbox"
}

if ($env:PHASE12_MAX_TASKS) {
    $argsList += @("--max-tasks", "$env:PHASE12_MAX_TASKS")
}

python @argsList
