$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)

$argsList = @("src\phase36\data_flywheel.py")
if ($env:PHASE36_AUTO_APPROVE_VERIFIER -eq "1") { $argsList += "--auto-approve-verifier" }
if ($env:PHASE36_EXECUTE_TRAINING -eq "1") { $argsList += "--execute-training" }

python @argsList
