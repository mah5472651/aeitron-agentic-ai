param(
  [string]$Prompt = "analyze attached assets",
  [string[]]$Path = @("."),
  [int]$MaxFiles = 200,
  [string]$RunId = ""
)

$ErrorActionPreference = "Stop"
$argsList = @("src\phase49\multimodal_expert.py", "--prompt", $Prompt, "--max-files", $MaxFiles)
foreach ($item in $Path) { $argsList += @("--path", $item) }
if ($RunId) { $argsList += @("--run-id", $RunId) }
python @argsList
