#!/usr/bin/env python
"""Validate GPU/vLLM backend readiness contract without requiring local CUDA."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.phase23.model_quality_profiles import build_plan, load_profiles


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class GPUBackendContractReport(StrictModel):
    run_id: str
    profile_count: int
    profiles: list[str]
    selected_profile: str
    endpoint: str
    quality_plan: dict[str, Any]
    status: str
    recommendation: str
    created_at_unix: float = Field(default_factory=time.time)


def run_contract(args: argparse.Namespace) -> GPUBackendContractReport:
    profiles = load_profiles()
    plan = build_plan(args)
    required = ["qwen2.5-coder-7b", "qwen2.5-coder-14b", "qwen2.5-coder-32b"]
    missing = [name for name in required if name not in profiles]
    status = "complete" if not missing else "needs_attention"
    return GPUBackendContractReport(
        run_id=args.run_id or f"phase33-{int(time.time())}",
        profile_count=len(profiles),
        profiles=sorted(profiles),
        selected_profile=args.profile,
        endpoint=args.endpoint,
        quality_plan=plan.model_dump(),
        status=status,
        recommendation="GPU backend contract ready; point endpoint at vLLM and execute Phase 23." if status == "complete" else f"Missing profiles: {missing}",
    )


def write_report(report: GPUBackendContractReport, output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / f"{report.run_id}.json"
    latest_path = output_dir / "gpu-backend-contract-latest.json"
    json_path.write_text(json.dumps(report.model_dump(), indent=2, ensure_ascii=False), encoding="utf-8")
    latest_path.write_text(json.dumps(report.model_dump(), indent=2, ensure_ascii=False), encoding="utf-8")
    return json_path, latest_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate Phase 33 GPU backend contract.")
    parser.add_argument("--profile", default="qwen2.5-coder-7b")
    parser.add_argument("--endpoint", default="http://127.0.0.1:8000/v1")
    parser.add_argument("--run-id")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "artifacts" / "phase33")
    parser.add_argument("--max-tasks", type=int, default=5)
    parser.add_argument("--per-category", type=int, default=1)
    parser.add_argument("--max-new-tokens", type=int, default=512)
    parser.add_argument("--full", action="store_true")
    parser.add_argument("--run-sandbox", action="store_true")
    parser.add_argument("--dry-run", action="store_true", default=True)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--timeout-s", type=float, default=3600.0)
    args = parser.parse_args()
    report = run_contract(args)
    json_path, _ = write_report(report, args.output_dir)
    print(json.dumps({"run_id": report.run_id, "status": report.status, "profiles": report.profile_count, "json": str(json_path)}, indent=2))


if __name__ == "__main__":
    main()
