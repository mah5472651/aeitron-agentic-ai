param(
    [string]$Pattern = "tests.test_mythos_mvp_foundation"
)

$ErrorActionPreference = "Stop"

python -m unittest $Pattern
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

python -m compileall -q src\mythos
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

Write-Host "Mythos MVP foundation checks passed"
