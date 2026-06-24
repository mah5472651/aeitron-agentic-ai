$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)
$workspace = if ($env:PHASE28_WORKSPACE) { $env:PHASE28_WORKSPACE } else { (Get-Location).Path }
$argsList = @("src\phase28\security_expert_workflow.py", "--workspace", $workspace)
if ($env:PHASE28_RUN_SEMGREP -eq "1") { $argsList += "--run-semgrep" }
if ($env:PHASE28_RUN_SANDBOX -eq "1") { $argsList += "--run-sandbox" }
python @argsList

