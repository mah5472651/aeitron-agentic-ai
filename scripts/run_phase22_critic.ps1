$ErrorActionPreference = "Stop"

Set-Location (Split-Path -Parent $PSScriptRoot)

$mode = if ($env:PHASE22_MODE) { $env:PHASE22_MODE } else { "heuristic" }
$prompt = if ($env:PHASE22_PROMPT) { $env:PHASE22_PROMPT } else { "Review this coding/security artifact." }
$artifact = if ($env:PHASE22_ARTIFACT) {
    $env:PHASE22_ARTIFACT
} else {
    "Plan: inspect target files, implement minimal code changes, and document risks. Verification: run unit tests, sandbox smoke checks, and static security review. Security: validate inputs, avoid shell execution, keep secrets out of code, and add regression tests."
}

python src\phase22\critic_service.py --mode $mode --prompt $prompt --artifact $artifact --json
