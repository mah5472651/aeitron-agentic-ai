$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)
$workspace = if ($env:PHASE26_WORKSPACE) { $env:PHASE26_WORKSPACE } else { (Get-Location).Path }
$argsList = @("src\phase26\patch_manager.py", "--workspace", $workspace)
if ($env:PHASE26_PATCH_JSON) { $argsList += @("--patch-json", $env:PHASE26_PATCH_JSON) }
if ($env:PHASE26_APPLY -eq "1") { $argsList += "--apply" }
if ($env:PHASE26_ROLLBACK_MANIFEST) { $argsList += @("--rollback-manifest", $env:PHASE26_ROLLBACK_MANIFEST) }
python @argsList

