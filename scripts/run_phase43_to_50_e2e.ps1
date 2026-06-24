param(
  [string]$Prompt = "build secure login system",
  [string]$RunId = "",
  [switch]$SkipPhase40
)

$ErrorActionPreference = "Stop"
$argsList = @("src\phase50\phase43_to_50_e2e.py", "--prompt", $Prompt)
if ($RunId) { $argsList += @("--run-id", $RunId) }
if ($SkipPhase40) { $argsList += "--skip-phase40" }
python @argsList
