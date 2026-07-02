"""Consolidated Learning Pipeline facade."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any


class LearningPipeline:
    def dataset_gate(self, dataset_path: str | Path, *, run_id: str = "mythos-dataset-review") -> dict[str, Any]:
        path = Path(dataset_path)
        valid = 0
        invalid = 0
        if path.exists():
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                try:
                    row = json.loads(line)
                    valid += int(bool(row.get("prompt") and row.get("chosen")))
                except json.JSONDecodeError:
                    invalid += 1
        return {"run_id": run_id, "dataset": str(path), "valid": valid, "invalid": invalid, "ready": valid > 0 and invalid == 0}

    def run_flywheel(self, args: Any) -> dict[str, Any]:
        return {"status": "queued", "created_at_unix": time.time(), "args": str(args)}

    def checkpoint_gate(self) -> dict[str, Any]:
        return {"status": "not_configured", "policy": "promote only after evaluation improves"}
