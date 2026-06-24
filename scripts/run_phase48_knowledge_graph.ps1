param(
  [string]$Query = "meta planner memory reasoning",
  [switch]$Seed,
  [string]$RunId = ""
)

$ErrorActionPreference = "Stop"
$argsList = @("src\phase48\knowledge_graph.py", "--query", $Query)
if ($Seed) { $argsList += "--seed" }
if ($RunId) { $argsList += @("--run-id", $RunId) }
python @argsList
