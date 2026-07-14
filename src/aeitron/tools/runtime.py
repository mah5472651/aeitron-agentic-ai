"""Compatibility tool runtime.

New code should import :class:`src.aeitron.tools.policy.HardenedToolExecutor`.
This module keeps the historical public schemas stable and delegates execution
to the hardened policy path.
"""

from __future__ import annotations

import uuid
from pathlib import Path

from pydantic import Field

from src.aeitron.db import LocalStore
from src.aeitron.shared.schemas import StrictModel


class ToolExecuteRequest(StrictModel):
    project_id: str
    run_id: str | None = None
    tool: str = Field(pattern="^(shell|test|git_diff)$")
    command: list[str] = Field(min_length=1)
    timeout_ms: int = Field(default=30_000, ge=1_000, le=300_000)


class ToolExecuteResponse(StrictModel):
    tool_call_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    project_id: str
    run_id: str | None = None
    tool: str
    status: str
    stdout: str
    stderr: str
    exit_code: int | None
    duration_ms: float


class SandboxPolicy(StrictModel):
    timeout_ms: int = 30_000
    network_disabled: bool = True


class SandboxRequest(StrictModel):
    command: list[str] = Field(min_length=1)


class ExecutionRequest(ToolExecuteRequest):
    pass


class ExecutionResult(ToolExecuteResponse):
    pass


def project_root(store: LocalStore, project_id: str) -> Path:
    project = store.get_project(project_id)
    if project is None:
        raise KeyError(f"unknown project: {project_id}")
    root = Path(str(project["repo_path"])).resolve()
    if not root.exists() or not root.is_dir():
        raise FileNotFoundError(f"project repo_path is not a directory: {root}")
    return root


def validate_command_policy(request: ToolExecuteRequest) -> None:
    from src.aeitron.tools.policy import HardenedToolExecutor

    HardenedToolExecutor().validate_tool_shape(request)


class ToolRuntime:
    def __init__(self, store: LocalStore | None = None) -> None:
        self.store = store or LocalStore()

    def execute(self, request: ToolExecuteRequest) -> ToolExecuteResponse:
        from src.aeitron.tools.policy import HardenedToolExecutor

        return HardenedToolExecutor(self.store).execute(request)


class SandboxEngine:
    async def run(self, request: ExecutionRequest) -> ExecutionResult:
        result = ToolRuntime().execute(request)
        return ExecutionResult.model_validate(result.model_dump())

