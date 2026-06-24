$ErrorActionPreference = "Stop"

Set-Location (Split-Path -Parent $PSScriptRoot)

$profile = if ($env:PHASE23_PROFILE) { $env:PHASE23_PROFILE } else { "qwen2.5-coder-7b" }
$endpoint = if ($env:PHASE23_ENDPOINT) { $env:PHASE23_ENDPOINT } else { "http://127.0.0.1:8000/v1" }

$argsList = @(
    "src\phase23\model_quality_profiles.py",
    "--profile", $profile,
    "--endpoint", $endpoint,
    "--json"
)

if ($env:PHASE23_DRY_RUN -eq "1") { $argsList += "--dry-run" }
if ($env:PHASE23_EXECUTE -eq "1") { $argsList += "--execute" }
if ($env:PHASE23_FULL -eq "1") { $argsList += "--full" }
if ($env:PHASE23_RUN_SANDBOX -eq "1") { $argsList += "--run-sandbox" }

python @argsList
