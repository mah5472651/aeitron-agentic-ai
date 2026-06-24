param(
  [string]$Profile = "qwen-cpu-smoke",
  [switch]$List
)

$ErrorActionPreference = "Stop"
if ($List) {
  python src\phase42\profile_switcher.py --list
} else {
  python src\phase42\profile_switcher.py --profile $Profile --activate
}
