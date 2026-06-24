param(
  [string]$Prompt = "build secure login system",
  [int]$TopK = 3,
  [string]$RunId = ""
)

$ErrorActionPreference = "Stop"
$argsList = @("src\phase50\moe_router.py", "--prompt", $Prompt, "--top-k", $TopK)
if ($RunId) { $argsList += @("--run-id", $RunId) }
python @argsList
