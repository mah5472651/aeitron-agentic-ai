$ErrorActionPreference = "Stop"

Set-Location (Split-Path -Parent $PSScriptRoot)

$workspace = if ($env:PHASE19_WORKSPACE) { $env:PHASE19_WORKSPACE } else { (Get-Location).Path }
$runId = if ($env:PHASE19_RUN_ID) { $env:PHASE19_RUN_ID } else { "phase19-local-verifier" }

$argsList = @(
    "src\phase19\verifier_registry.py",
    "--workspace", $workspace,
    "--run-id", $runId,
    "--json"
)

if ($env:PHASE19_RUN_SEMGREP -eq "1") { $argsList += "--run-semgrep" }
if ($env:PHASE19_RUN_CODEQL -eq "1") { $argsList += "--run-codeql" }
if ($env:PHASE19_RUN_SANDBOX -eq "1") { $argsList += "--run-sandbox" }
if ($env:PHASE19_STRICT -eq "1") { $argsList += "--strict" }

python @argsList

