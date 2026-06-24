$ErrorActionPreference = "Stop"

Set-Location (Split-Path -Parent $PSScriptRoot)

$pidPath = "artifacts\runtime\phase16_real_backend.pid"
if (Test-Path $pidPath) {
    $pidText = Get-Content $pidPath -Raw
    if ($pidText) {
        $process = Get-Process -Id ([int]$pidText) -ErrorAction SilentlyContinue
        if ($process) {
            Stop-Process -Id $process.Id -Force
        }
    }
    Remove-Item -LiteralPath $pidPath -Force
}

