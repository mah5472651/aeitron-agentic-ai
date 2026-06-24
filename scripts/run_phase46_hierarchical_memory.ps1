param(
  [string]$Query = "secure planner verifier",
  [switch]$Seed,
  [int]$Limit = 10,
  [string]$RunId = ""
)

$ErrorActionPreference = "Stop"
$argsList = @("src\phase46\hierarchical_memory.py", "--query", $Query, "--limit", $Limit)
if ($Seed) { $argsList += "--seed" }
if ($RunId) { $argsList += @("--run-id", $RunId) }
python @argsList
