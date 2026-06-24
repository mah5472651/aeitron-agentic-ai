$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)
$profile = if ($env:PHASE27_PROFILE) { $env:PHASE27_PROFILE } else { "fast" }
$workspace = if ($env:PHASE27_WORKSPACE) { $env:PHASE27_WORKSPACE } else { (Get-Location).Path }
python src\phase27\verifier_policy_engine.py --profile $profile --workspace $workspace --json

