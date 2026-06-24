$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)
$prompt = if ($env:PHASE24_PROMPT) { $env:PHASE24_PROMPT } else { "Improve this AI architecture safely with verifier and memory." }
$workspace = if ($env:PHASE24_WORKSPACE) { $env:PHASE24_WORKSPACE } else { (Get-Location).Path }
$argsList = @("src\phase24\main_agent_v2.py", "--prompt", $prompt, "--workspace", $workspace, "--json")
if ($env:PHASE24_RUN_SEMGREP -eq "1") { $argsList += "--run-semgrep" }
if ($env:PHASE24_RUN_SANDBOX -eq "1") { $argsList += "--run-sandbox" }
if ($env:PHASE24_NO_VERIFIER -eq "1") { $argsList += "--no-verifier" }
if ($env:PHASE24_NO_EXPERIENCE -eq "1") { $argsList += "--no-experience" }
python @argsList

