$ErrorActionPreference = "Stop"

Set-Location (Split-Path -Parent $PSScriptRoot)

$prompt = if ($env:PHASE20_PROMPT) { $env:PHASE20_PROMPT } else { "Build a safe coding plan and verification path for this repository." }
$workspace = if ($env:PHASE20_WORKSPACE) { $env:PHASE20_WORKSPACE } else { (Get-Location).Path }

$argsList = @(
    "src\phase20\taskgraph_runtime.py",
    "--prompt", $prompt,
    "--workspace", $workspace,
    "--json"
)

if ($env:PHASE20_RUN_VERIFIER -eq "1") { $argsList += "--run-verifier" }
if ($env:PHASE20_RUN_SEMGREP -eq "1") { $argsList += "--run-semgrep" }
if ($env:PHASE20_RUN_SANDBOX -eq "1") { $argsList += "--run-sandbox" }
if ($env:PHASE20_MODEL_CRITIC -eq "1") { $argsList += "--model-critic" }
if ($env:PHASE20_STRICT -eq "1") { $argsList += "--strict" }

python @argsList

