param(
    [switch]$EnableWSL,
    [switch]$InstallDocker,
    [switch]$InstallPython312
)

$ErrorActionPreference = "Stop"

Write-Host "Checking Windows AI architecture prerequisites..."

if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
    throw "winget is not available. Install App Installer from Microsoft Store first."
}

if ($EnableWSL) {
    Write-Host "Enabling Windows Subsystem for Linux. This may require administrator rights and a reboot..."
    wsl --install --no-distribution
}

if ($InstallPython312) {
    Write-Host "Installing Python 3.12 via winget..."
    winget install --id Python.Python.3.12 -e --accept-package-agreements --accept-source-agreements
}

if ($InstallDocker) {
    Write-Host "Installing Docker Desktop via winget..."
    winget install --id Docker.DockerDesktop -e --accept-package-agreements --accept-source-agreements
    Write-Host "Docker Desktop may require logout/restart and WSL2 enablement before first use."
}

Write-Host "Prerequisite installer finished."
