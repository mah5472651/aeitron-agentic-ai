$ErrorActionPreference = "Stop"

Set-Location (Split-Path -Parent $PSScriptRoot)

$runtimeDir = "artifacts\runtime"
New-Item -ItemType Directory -Force -Path $runtimeDir | Out-Null
$gatewayPort = if ($env:GATEWAY_PORT) { [int]$env:GATEWAY_PORT } else { 18080 }

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

if (-not (Test-Port 8000)) {
    Start-Process -WindowStyle Hidden `
        -FilePath "python" `
        -ArgumentList @("src\phase10\mock_vllm_server.py", "--host", "127.0.0.1", "--port", "8000") `
        -RedirectStandardOutput "$runtimeDir\mock_vllm.out.log" `
        -RedirectStandardError "$runtimeDir\mock_vllm.err.log" `
        -PassThru | Select-Object -ExpandProperty Id | Set-Content "$runtimeDir\mock_vllm.pid"
}

if (-not (Test-Port $gatewayPort)) {
    $env:VLLM_BASE_URL = "http://127.0.0.1:8000"
    $env:SERVED_MODEL_NAME = "security-coder"
    Start-Process -WindowStyle Hidden `
        -FilePath "python" `
        -ArgumentList @("src\phase8\gateway.py", "--host", "127.0.0.1", "--port", "$gatewayPort") `
        -RedirectStandardOutput "$runtimeDir\gateway.out.log" `
        -RedirectStandardError "$runtimeDir\gateway.err.log" `
        -PassThru | Select-Object -ExpandProperty Id | Set-Content "$runtimeDir\gateway.pid"
}

Start-Sleep -Seconds 3
python src\phase10\e2e_smoke_runner.py `
    --run-id dev-serving-mock-smoke `
    --tokenizer artifacts\mvp\code_bpe_tokenizer\tokenizer.json `
    --postgres-dsn "postgresql://ai:ai_dev_password@localhost:5432/ai_eval" `
    --redis-url redis://127.0.0.1:6379/0 `
    --qdrant-url http://localhost:6333 `
    --gateway-url "http://127.0.0.1:$gatewayPort" `
    --vllm-url http://127.0.0.1:8000 `
    --run-sandbox-smoke
