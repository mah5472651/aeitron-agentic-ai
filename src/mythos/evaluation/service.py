"""Consolidated Evaluation Service."""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess  # nosec B404
import sys
from pathlib import Path
from typing import Any

from src.phase14.scorecard_harness import run_scorecard


class EvaluationService:
    async def run_scorecard_mock(self, *, run_id: str, output_dir: Path = Path("artifacts/scorecard")) -> dict[str, Any]:
        args = argparse.Namespace(
            run_id=run_id,
            output_dir=output_dir,
            mode="mock",
            real_backend="openai_compatible",
            run_sandbox=False,
            context_budget=8000,
            concurrency=4,
            strict=True,
            max_tasks=0,
        )
        report = await run_scorecard(args)
        return report.model_dump() if hasattr(report, "model_dump") else report.__dict__

    def run_release_gate(self, *, run_id: str = "mythos-consolidated-release") -> dict[str, Any]:
        completed = subprocess.run(  # nosec B603
            [
                sys.executable,
                "src/mythos_v1/release_gate.py",
                "--run-id",
                run_id,
                "--mode",
                "quick",
                "--include-real-backend",
                "--strict",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        try:
            payload = json.loads(completed.stdout or "{}")
        except json.JSONDecodeError:
            payload = {}
        return {
            "ok": completed.returncode == 0,
            "exit_code": completed.returncode,
            "stdout": completed.stdout[-4000:],
            "stderr": completed.stderr[-4000:],
            "summary": payload,
        }

    def run_scorecard_mock_sync(self, *, run_id: str) -> dict[str, Any]:
        return asyncio.run(self.run_scorecard_mock(run_id=run_id))
