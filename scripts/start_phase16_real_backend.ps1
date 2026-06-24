$ErrorActionPreference = "Stop"

Set-Location (Split-Path -Parent $PSScriptRoot)

$runtimeDir = "artifacts\runtime"
New-Item -ItemType Directory -Force -Path $runtimeDir | Out-Null

$port = if ($env:PHASE16_REAL_PORT) { [int]$env:PHASE16_REAL_PORT } else { 8016 }
$modelId = if ($env:PHASE16_HF_MODEL_ID) { $env:PHASE16_HF_MODEL_ID } else { "Qwen/Qwen2.5-Coder-0.5B-Instruct" }
$revision = if ($env:PHASE16_HF_REVISION) { $env:PHASE16_HF_REVISION } else { "ea3f2471cf1b1f0db85067f1ef93848e38e88c25" }
$device = if ($env:PHASE16_HF_DEVICE) { $env:PHASE16_HF_DEVICE } else { "cpu" }
$pidPath = Join-Path $runtimeDir "phase16_real_backend.pid"
$outLog = Join-Path $runtimeDir "phase16_real_backend.out.log"
$errLog = Join-Path $runtimeDir "phase16_real_backend.err.log"

if (Test-Path $pidPath) {
    $existingPid = Get-Content $pidPath -Raw
    if ($existingPid -and (Get-Process -Id ([int]$existingPid) -ErrorAction SilentlyContinue)) {
        Write-Output "Phase 16 real backend already running on pid=$existingPid"
        exit 0
    }
}

$argsList = @(
    "-m", "src.phase16.local_hf_openai_server",
    "--host", "127.0.0.1",
    "--port", "$port",
    "--model-id", "$modelId",
    "--revision", "$revision",
    "--device", "$device"
)

$process = Start-Process -FilePath "python" -ArgumentList $argsList -WorkingDirectory (Get-Location) -RedirectStandardOutput $outLog -RedirectStandardError $errLog -PassThru -WindowStyle Hidden
Set-Content -Path $pidPath -Value $process.Id -Encoding ASCII

$deadline = (Get-Date).AddMinutes(20)
do {
    Start-Sleep -Seconds 5
    try {
        $ready = Invoke-RestMethod -Uri "http://127.0.0.1:$port/health/ready" -TimeoutSec 5
        if ($ready.status -eq "ready") {
            Write-Output "Phase 16 real backend ready: model=$($ready.model) port=$port pid=$($process.Id)"
            exit 0
        }
    } catch {
        if (-not (Get-Process -Id $process.Id -ErrorAction SilentlyContinue)) {
            throw "Phase 16 real backend exited early. See $errLog"
        }
    }
} while ((Get-Date) -lt $deadline)

throw "Timed out waiting for Phase 16 real backend. See $outLog and $errLog"
