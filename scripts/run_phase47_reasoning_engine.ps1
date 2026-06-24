param(
  [string]$Prompt = "build secure login system",
  [string]$RunId = ""
)

$ErrorActionPreference = "Stop"
$argsList = @("src\phase47\reasoning_engine.py", "--prompt", $Prompt)
if ($RunId) { $argsList += @("--run-id", $RunId) }
python @argsList
