$ErrorActionPreference = "Stop"

Set-Location (Split-Path -Parent $PSScriptRoot)

$runId = if ($env:GPU_READINESS_RUN_ID) { $env:GPU_READINESS_RUN_ID } else { "gpu-readiness" }

python src\phase17\gpu_readiness.py --run-id $runId --json

