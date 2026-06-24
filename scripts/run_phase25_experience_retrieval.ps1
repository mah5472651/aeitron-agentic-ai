$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)
$query = if ($env:PHASE25_QUERY) { $env:PHASE25_QUERY } else { "model output verifier failure security patch" }
python src\phase25\experience_retrieval.py --query $query

