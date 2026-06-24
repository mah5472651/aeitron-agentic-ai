$ErrorActionPreference = "Continue"

Set-Location (Split-Path -Parent $PSScriptRoot)

$pidFile = "artifacts\runtime\phase11_chat.pid"
if (Test-Path $pidFile) {
    $pidValue = Get-Content $pidFile -ErrorAction SilentlyContinue
    if ($pidValue) {
        Stop-Process -Id ([int]$pidValue) -Force -ErrorAction SilentlyContinue
    }
    Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
}

Get-CimInstance Win32_Process |
    Where-Object {
        $_.ProcessId -ne $PID -and
        $_.Name -match "python" -and
        ($_.CommandLine -like "*src\phase11\chat_api.py*" -or $_.CommandLine -like "*src.phase11.chat_api*")
    } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
