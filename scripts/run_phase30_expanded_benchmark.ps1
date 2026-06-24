$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)
python src\phase30\expanded_benchmark_suite.py

