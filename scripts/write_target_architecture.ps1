$ErrorActionPreference = "Stop"

Set-Location (Split-Path -Parent $PSScriptRoot)

$runId = if ($env:TARGET_ARCH_RUN_ID) { $env:TARGET_ARCH_RUN_ID } else { "mythos-target-architecture" }

python src\phase15\target_architecture.py --run-id $runId
