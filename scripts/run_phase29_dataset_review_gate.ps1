$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)
$argsList = @("src\phase29\dataset_review_gate.py")
if ($env:PHASE29_INPUT) { $argsList += @("--input", $env:PHASE29_INPUT) }
if ($env:PHASE29_AUTO_APPROVE_VERIFIER -eq "1") { $argsList += "--auto-approve-verifier" }
python @argsList

