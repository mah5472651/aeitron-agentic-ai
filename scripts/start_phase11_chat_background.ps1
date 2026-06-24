$ErrorActionPreference = "Stop"

Set-Location (Split-Path -Parent $PSScriptRoot)

$runtimeDir = "artifacts\runtime"
New-Item -ItemType Directory -Force -Path $runtimeDir | Out-Null
$phase11Port = if ($env:PHASE11_PORT) { [int]$env:PHASE11_PORT } else { 8090 }
$activeProfileScript = "config\active_model_profile.ps1"
if (Test-Path $activeProfileScript) {
    . ".\$activeProfileScript"
}

function Test-Port($Port) {
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $async = $client.BeginConnect("127.0.0.1", $Port, $null, $null)
        $ok = $async.AsyncWaitHandle.WaitOne(500)
        if ($ok) { $client.EndConnect($async) }
        return $ok
    } catch {
        return $false
    } finally {
        $client.Close()
    }
}

if (-not $env:PHASE11_BACKEND) {
    $env:PHASE11_BACKEND = "mock"
}
if (-not $env:PHASE11_WORKSPACE) {
    $env:PHASE11_WORKSPACE = (Get-Location).Path
}

if (-not (Test-Port $phase11Port)) {
    Start-Process -WindowStyle Hidden `
        -FilePath "python" `
        -ArgumentList @("-m", "uvicorn", "src.phase11.chat_api:app", "--host", "127.0.0.1", "--port", "$phase11Port") `
        -RedirectStandardOutput "$runtimeDir\phase11_chat.out.log" `
        -RedirectStandardError "$runtimeDir\phase11_chat.err.log" `
        -PassThru | Select-Object -ExpandProperty Id | Set-Content "$runtimeDir\phase11_chat.pid"
}

for ($i = 0; $i -lt 10; $i++) {
    try {
        Invoke-RestMethod "http://127.0.0.1:$phase11Port/health/ready"
        $owner = Get-NetTCPConnection -LocalPort $phase11Port -ErrorAction SilentlyContinue |
            Where-Object { $_.State -eq "Listen" } |
            Select-Object -First 1 -ExpandProperty OwningProcess
        if ($owner) {
            Set-Content "$runtimeDir\phase11_chat.pid" $owner
        }
        exit 0
    } catch {
        Start-Sleep -Milliseconds 500
    }
}

throw "Phase 11 chat API did not become ready on http://127.0.0.1:$phase11Port"
