$ErrorActionPreference = "Continue"

Write-Host "=== Python launchers ==="
py -0p

Write-Host "`n=== Docker ==="
where.exe docker
docker version

Write-Host "`n=== WSL ==="
$wslOutput = wsl -l -v 2>&1
($wslOutput -join "`n") -replace "`0", ""

Write-Host "`n=== NVIDIA ==="
if (Get-Command nvidia-smi -ErrorAction SilentlyContinue) {
    nvidia-smi
} else {
    Write-Warning "nvidia-smi not found. Local orchestration can run, but real GRPO/vLLM GPU workloads need a CUDA Linux/GPU machine."
}

Write-Host "`n=== Mythos MVP Foundation Smoke ==="
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\run_mythos_mvp_foundation.ps1
