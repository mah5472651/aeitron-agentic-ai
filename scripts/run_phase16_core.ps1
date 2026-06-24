$ErrorActionPreference = "Stop"

Set-Location (Split-Path -Parent $PSScriptRoot)

python src\phase16\smoke_test.py --json

