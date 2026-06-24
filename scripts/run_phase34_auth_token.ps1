$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)

$userId = if ($env:PHASE34_USER_ID) { $env:PHASE34_USER_ID } else { "local-user" }
$secret = if ($env:PHASE34_JWT_SECRET) { $env:PHASE34_JWT_SECRET } else { "local-dev-secret-change-before-production" }

python src\phase34\auth_quota.py --user-id $userId --secret $secret

