#!/usr/bin/env python
"""Defensive security expert workflow: find, explain, patch direction, verify."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.phase11.memory_engine import safe_workspace
from src.phase19.verifier_registry import VerifierPolicy, VerifierRegistry


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class SecurityWorkflowReport(StrictModel):
    run_id: str
    workspace: str
    status: str
    security_review: dict[str, Any]
    patch_guidance: list[dict[str, Any]]
    verifier: dict[str, Any]
    safety_position: str
    duration_ms: float
    created_at_unix: float = Field(default_factory=time.time)


class SecurityExpertWorkflow:
    async def run(self, workspace: str | Path, *, run_semgrep: bool = False, run_sandbox: bool = False, run_id: str | None = None) -> SecurityWorkflowReport:
        started = time.time()
        root = safe_workspace(workspace)
        verifier = await VerifierRegistry(
            VerifierPolicy(run_semgrep=run_semgrep, run_sandbox=run_sandbox, fail_on_medium=False)
        ).run(root, run_id=f"{run_id or 'phase28'}-verifier")
        guidance = [self._guidance(finding.model_dump()) for finding in verifier.findings[:50]]
        review = {
            "target": str(root),
            "findings": [finding.model_dump() for finding in verifier.findings[:100]],
            "score": verifier.score / 100.0,
            "summary": f"Verifier-backed security review status={verifier.status}; findings={len(verifier.findings)}.",
        }
        status = "needs_patch" if verifier.findings or verifier.status == "fail" else "complete"
        return SecurityWorkflowReport(
            run_id=run_id or f"phase28-{int(started)}",
            workspace=str(root),
            status=status,
            security_review=review,
            patch_guidance=guidance,
            verifier=verifier.model_dump(),
            safety_position="defensive_only_static_analysis_patch_generation_verification_no_autonomous_exploit_execution",
            duration_ms=(time.time() - started) * 1000,
        )

    def _guidance(self, finding: dict[str, Any]) -> dict[str, Any]:
        return {
            "finding_id": finding.get("finding_id"),
            "file_path": finding.get("file_path"),
            "line": finding.get("line"),
            "cwe": finding.get("cwe"),
            "title": finding.get("title"),
            "safe_patch_direction": finding.get("recommendation"),
            "verification": ["rerun Phase 19 verifier", "add regression test", "confirm no new findings"],
        }


def write_report(report: SecurityWorkflowReport, output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / f"{report.run_id}.json"
    latest_path = output_dir / "security-workflow-latest.json"
    md_path = output_dir / f"{report.run_id}.md"
    json_path.write_text(json.dumps(report.model_dump(), indent=2, ensure_ascii=False), encoding="utf-8")
    latest_path.write_text(json.dumps(report.model_dump(), indent=2, ensure_ascii=False), encoding="utf-8")
    md_path.write_text(
        f"# Phase 28 Security Expert Workflow\n\n- Run ID: `{report.run_id}`\n- Status: `{report.status}`\n- Findings: `{len(report.patch_guidance)}`\n- Safety: `{report.safety_position}`\n",
        encoding="utf-8",
    )
    return json_path, md_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run defensive security workflow.")
    parser.add_argument("--workspace", default=str(ROOT))
    parser.add_argument("--run-id", default=f"phase28-{int(time.time())}")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "artifacts" / "phase28")
    parser.add_argument("--run-semgrep", action="store_true")
    parser.add_argument("--run-sandbox", action="store_true")
    return parser.parse_args()


async def async_main() -> int:
    args = parse_args()
    report = await SecurityExpertWorkflow().run(args.workspace, run_semgrep=args.run_semgrep, run_sandbox=args.run_sandbox, run_id=args.run_id)
    json_path, md_path = write_report(report, args.output_dir)
    print(json.dumps({"run_id": report.run_id, "status": report.status, "findings": len(report.patch_guidance), "json": str(json_path), "markdown": str(md_path)}, indent=2))
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(async_main()))


if __name__ == "__main__":
    main()
