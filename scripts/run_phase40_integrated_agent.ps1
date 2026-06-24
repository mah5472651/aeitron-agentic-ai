param(
  [string]$Prompt = "debug this architecture and recommend the safest next patch",
  [string]$Workspace = ".",
  [string]$VerifierProfile = "",
  [string]$AgentBackendMode = "auto",
  [switch]$ModelCritic,
  [switch]$RebuildVectorMemory,
  [switch]$NoVerifier,
  [switch]$NoSecurity
)

$ErrorActionPreference = "Stop"
$argsList = @(
  "src\phase40\integrated_agent.py",
  "--prompt", $Prompt,
  "--workspace", $Workspace,
  "--agent-backend-mode", $AgentBackendMode
)

if ($VerifierProfile) { $argsList += @("--verifier-profile", $VerifierProfile) }
if ($ModelCritic) { $argsList += "--model-critic" }
if ($RebuildVectorMemory) { $argsList += "--rebuild-vector-memory" }
if ($NoVerifier) { $argsList += "--no-verifier" }
if ($NoSecurity) { $argsList += "--no-security" }

python @argsList
