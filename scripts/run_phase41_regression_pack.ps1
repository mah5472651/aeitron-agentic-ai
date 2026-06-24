param(
  [int]$SmokeLimit = 25
)

$ErrorActionPreference = "Stop"
python src\phase41\regression_pack.py --smoke-limit $SmokeLimit
