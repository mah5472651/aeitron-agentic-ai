"""Consolidated Learning Pipeline facade."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from src.phase29.dataset_review_gate import run_gate
from src.phase36.data_flywheel import run_flywheel
from src.phase39.checkpoint_rollback import CheckpointRollbackGate


class LearningPipeline:
    def dataset_gate(self, dataset_path: str | Path, *, run_id: str = "mythos-dataset-review") -> dict[str, Any]:
        report = run_gate([Path(dataset_path)], output_dir=Path("artifacts/mythos/learning"), run_id=run_id)
        return report.model_dump()

    def run_flywheel(self, args: Any) -> dict[str, Any]:
        return run_flywheel(args).model_dump()

    def checkpoint_gate(self) -> CheckpointRollbackGate:
        return CheckpointRollbackGate()
