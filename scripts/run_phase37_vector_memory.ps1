param(
  [string]$Query = "model output verifier failure security patch",
  [int]$Limit = 8,
  [switch]$Rebuild
)

$ErrorActionPreference = "Stop"
$argsList = @(
  "src\phase37\vector_memory.py",
  "--query", $Query,
  "--limit", "$Limit"
)

if ($Rebuild) {
  $argsList += "--rebuild"
}

python @argsList
