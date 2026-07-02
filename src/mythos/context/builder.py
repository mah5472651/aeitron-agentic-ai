"""Workspace Index & Context Builder facade."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from src.mythos.db import LocalStore
from src.mythos.indexing import ContextBuilder, RepositoryIndexer


class WorkspaceContextBuilder:
    def __init__(self, workspace: str | Path) -> None:
        self.workspace = Path(workspace)

    def pack(self, query: str, *, budget: int = 8000) -> dict[str, Any]:
        store = LocalStore()
        project = store.create_project(name=f"context-{self.workspace.name}", repo_path=str(self.workspace))
        RepositoryIndexer(store).index_project(project_id=project["id"])
        report = ContextBuilder(store).build(project_id=project["id"], query=query, token_budget=budget)
        return report.model_dump()

    def call_graph_command(self, *, output: str | Path = "artifacts/mythos/context/callgraph.jsonl") -> list[str]:
        return [
            "python",
            "-m",
            "src.mythos.cli",
            "--workspace",
            str(self.workspace),
            "--prompt",
            "build repository context",
        ]
