param(
  [string]$Workspace = ".",
  [int]$MaxFiles = 3000,
  [switch]$IncludeFixtures
)

$ErrorActionPreference = "Stop"
$argsList = @(
  "src\phase38\multilang_security.py",
  "--workspace", $Workspace,
  "--max-files", "$MaxFiles"
)

if ($IncludeFixtures) {
  $argsList += "--include-fixtures"
}

python @argsList
