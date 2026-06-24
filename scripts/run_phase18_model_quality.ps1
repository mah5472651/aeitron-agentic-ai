$ErrorActionPreference = "Stop"

Set-Location (Split-Path -Parent $PSScriptRoot)

$runId = if ($env:PHASE18_RUN_ID) { $env:PHASE18_RUN_ID } else { "phase18-qwen-local" }
$endpoint = if ($env:PHASE18_MODEL_ENDPOINT) { $env:PHASE18_MODEL_ENDPOINT } else { "http://127.0.0.1:8016/v1" }
$model = if ($env:PHASE18_MODEL_NAME) { $env:PHASE18_MODEL_NAME } else { "Qwen/Qwen2.5-Coder-0.5B-Instruct" }
$maxTasks = if ($env:PHASE18_MAX_TASKS) { [int]$env:PHASE18_MAX_TASKS } else { 5 }
$perCategory = if ($env:PHASE18_PER_CATEGORY) { [int]$env:PHASE18_PER_CATEGORY } else { 1 }
$maxNewTokens = if ($env:PHASE18_MAX_NEW_TOKENS) { [int]$env:PHASE18_MAX_NEW_TOKENS } else { 180 }

$env:SCORECARD_MODEL_ENDPOINT = $endpoint
$env:SCORECARD_MODEL_NAME = $model

$argsList = @(
    "src\phase18\model_quality_loop.py",
    "--run-id", $runId,
    "--backend-kind", "openai_compatible",
    "--model-endpoint", $endpoint,
    "--model-name", $model,
    "--max-tasks", "$maxTasks",
    "--per-category", "$perCategory",
    "--max-new-tokens", "$maxNewTokens",
    "--include-warnings"
)

if ($env:PHASE18_RUN_SANDBOX -ne "0") {
    $argsList += "--run-sandbox"
}
if ($env:PHASE18_FULL -eq "1") {
    $argsList += "--full"
}
if ($env:PHASE18_DRY_RUN -eq "1") {
    $argsList += "--dry-run"
    $argsList += "--json"
}
if ($env:PHASE18_STRICT -eq "1") {
    $argsList += "--strict"
}

python @argsList
