param(
  [string]$Prompt = "build secure login system",
  [string]$BackendMode = "mock",
  [int]$MaxParallel = 5,
  [string]$RunId = ""
)

$ErrorActionPreference = "Stop"
$argsList = @(
  "src\phase45\parallel_agent_runtime.py",
  "--prompt", $Prompt,
  "--backend-mode", $BackendMode,
  "--max-parallel", $MaxParallel
)
if ($RunId) { $argsList += @("--run-id", $RunId) }
python @argsList
