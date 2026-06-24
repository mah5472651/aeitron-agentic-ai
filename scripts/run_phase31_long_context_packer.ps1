$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)
$query = if ($env:PHASE31_QUERY) { $env:PHASE31_QUERY } else { "agentic coding security verifier memory" }
$workspace = if ($env:PHASE31_WORKSPACE) { $env:PHASE31_WORKSPACE } else { (Get-Location).Path }
python src\phase31\long_context_packer.py --workspace $workspace --query $query

