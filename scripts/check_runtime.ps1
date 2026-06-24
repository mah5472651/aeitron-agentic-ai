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

Write-Host "`n=== Phase 10 Offline Smoke ==="
python src\phase10\e2e_smoke_runner.py --offline --run-id runtime-check-offline
