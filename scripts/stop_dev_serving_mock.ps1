$ErrorActionPreference = "Continue"

Set-Location (Split-Path -Parent $PSScriptRoot)
$runtimeDir = "artifacts\runtime"

foreach ($name in @("gateway", "mock_vllm")) {
    $pidFile = Join-Path $runtimeDir "$name.pid"
    if (Test-Path $pidFile) {
        $pidValue = Get-Content $pidFile | Select-Object -First 1
        if ($pidValue) {
            Stop-Process -Id ([int]$pidValue) -Force -ErrorAction SilentlyContinue
        }
    }
}

