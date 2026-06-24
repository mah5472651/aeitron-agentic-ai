param(
  [string]$Prompt = "build netflix",
  [string]$RunId = ""
)

$ErrorActionPreference = "Stop"
$argsList = @("src\phase43\meta_planner.py", "--prompt", $Prompt)
if ($RunId) { $argsList += @("--run-id", $RunId) }
python @argsList
