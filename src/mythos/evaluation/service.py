"""Consolidated Evaluation Service."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from src.mythos.db import LocalStore
from src.mythos.indexing import ContextBuilder, RepositoryIndexer


class EvaluationService:
    async def run_scorecard_mock(self, *, run_id: str, output_dir: Path = Path("artifacts/scorecard")) -> dict[str, Any]:
        output_dir.mkdir(parents=True, exist_ok=True)
        return {
            "run_id": run_id,
            "status": "passed",
            "score": 1.0,
            "checks": ["gateway", "indexing", "context_builder", "taskgraph", "patch_verifier"],
            "output_dir": str(output_dir),
        }

    def run_release_gate(self, *, run_id: str = "mythos-consolidated-release") -> dict[str, Any]:
        return {"run_id": run_id, "ok": True, "summary": "native release gate is scripts/run_mythos_mvp_foundation.ps1"}

    def run_scorecard_mock_sync(self, *, run_id: str) -> dict[str, Any]:
        return {"run_id": run_id, "status": "passed", "score": 1.0}
