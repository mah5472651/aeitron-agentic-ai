param(
  [Parameter(Mandatory=$true)][string]$CandidateCheckpoint,
  [Parameter(Mandatory=$true)][string]$CandidateReport,
  [string]$BaselineReport = "",
  [double]$MaxDrop = 0.02,
  [string]$RequiredMetrics = "overall_score,pass_at_1,security_score",
  [switch]$Promote,
  [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$argsList = @(
  "src\phase39\checkpoint_rollback.py",
  "--candidate-checkpoint", $CandidateCheckpoint,
  "--candidate-report", $CandidateReport,
  "--max-drop", "$MaxDrop",
  "--required-metrics", $RequiredMetrics
)

if ($BaselineReport) {
  $argsList += @("--baseline-report", $BaselineReport)
}
if ($Promote) {
  $argsList += "--promote"
}
if ($DryRun) {
  $argsList += "--dry-run"
}

python @argsList
