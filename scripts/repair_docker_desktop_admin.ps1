$ErrorActionPreference = "Stop"

$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
$isAdmin = $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    throw "Run this script from PowerShell as Administrator."
}

Write-Host "Checking Docker Desktop service..."
$service = Get-Service com.docker.service -ErrorAction SilentlyContinue
if (-not $service) {
    throw "Docker Desktop service com.docker.service was not found. Reinstall Docker Desktop."
}

if ($service.Status -ne "Running") {
    Write-Host "Starting Docker Desktop service..."
    Start-Service com.docker.service
}

$dockerDesktop = "C:\Program Files\Docker\Docker\Docker Desktop.exe"
if (Test-Path $dockerDesktop) {
    Write-Host "Launching Docker Desktop..."
    Start-Process -FilePath $dockerDesktop
}

Write-Host "Waiting for Docker Engine..."
for ($i = 0; $i -lt 30; $i++) {
    Start-Sleep -Seconds 5
    docker version *> $null
    if ($LASTEXITCODE -eq 0) {
        Write-Host "Docker Engine is ready."
        docker version
        exit 0
    }
}

throw "Docker Engine did not become ready within 150 seconds. Open Docker Desktop and check its UI diagnostics."

