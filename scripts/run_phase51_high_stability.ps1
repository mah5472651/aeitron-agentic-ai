$ErrorActionPreference = "Stop"

Set-Location (Split-Path -Parent $PSScriptRoot)

$prompt = if ($args.Count -gt 0) { $args -join " " } else { "build strict planner executor critic verifier memory architecture" }

python src\phase51\high_stability_reasoning_memory.py `
  --prompt $prompt `
  --run-id "phase51-local"

