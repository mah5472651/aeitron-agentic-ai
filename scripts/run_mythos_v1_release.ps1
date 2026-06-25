$ErrorActionPreference = "Stop"

Set-Location (Split-Path -Parent $PSScriptRoot)

$mode = if ($env:MYTHOS_RELEASE_MODE) { $env:MYTHOS_RELEASE_MODE } else { "quick" }
$arguments = @(
    "src\mythos_v1\release_gate.py",
    "--mode", $mode,
    "--run-id", "mythos-v1-release",
    "--strict"
)

if ($env:MYTHOS_COMPARE_REAL -eq "1") {
    $arguments += "--include-real-backend"
}
if ($env:MYTHOS_REQUIRE_REAL -eq "1") {
    $arguments += "--require-real-backend"
}

python @arguments

