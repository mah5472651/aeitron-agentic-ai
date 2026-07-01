"""Workspace Index & Context Builder facade."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from src.phase31.long_context_packer import LongContextPacker


class WorkspaceContextBuilder:
    def __init__(self, workspace: str | Path) -> None:
        self.workspace = Path(workspace)

    def pack(self, query: str, *, budget: int = 8000) -> dict[str, Any]:
        report = LongContextPacker().pack(workspace=self.workspace, query=query, token_budget=budget)
        return report.model_dump()

    def call_graph_command(self, *, output: str | Path = "artifacts/mythos/context/callgraph.jsonl") -> list[str]:
        return [
            "python",
            "src/phase1/callgraph_extractor.py",
            "--repo",
            str(self.workspace),
            "--output",
            str(output),
        ]
