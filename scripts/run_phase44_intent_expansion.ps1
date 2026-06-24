param(
  [string]$Prompt = "build login system",
  [string]$RunId = ""
)

$ErrorActionPreference = "Stop"
$argsList = @("src\phase44\intent_expansion.py", "--prompt", $Prompt)
if ($RunId) { $argsList += @("--run-id", $RunId) }
python @argsList
