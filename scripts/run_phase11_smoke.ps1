$ErrorActionPreference = "Stop"

Set-Location (Split-Path -Parent $PSScriptRoot)

python src\phase11\smoke_test.py --json

