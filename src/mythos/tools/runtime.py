"""MVP tool runtime for bounded local command execution."""

from __future__ import annotations

import subprocess  # nosec B404
import time
import uuid
from pathlib import Path

from pydantic import Field

from src.mythos.db import LocalStore
from src.mythos.shared.schemas import StrictModel


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


def project_root(store: LocalStore, project_id: str) -> Path:
    project = store.get_project(project_id)
    if project is None:
        raise KeyError(f"unknown project: {project_id}")
    root = Path(str(project["repo_path"])).resolve()
    if not root.exists() or not root.is_dir():
        raise FileNotFoundError(f"project repo_path is not a directory: {root}")
    return root


class ToolRuntime:
    def __init__(self, store: LocalStore | None = None) -> None:
        self.store = store or LocalStore()

    def execute(self, request: ToolExecuteRequest) -> ToolExecuteResponse:
        root = project_root(self.store, request.project_id)
        started = time.perf_counter()
        try:
            completed = subprocess.run(  # nosec B603 - argv list, shell disabled.
                request.command,
                cwd=root,
                capture_output=True,
                text=True,
                shell=False,
                timeout=request.timeout_ms / 1000,
                check=False,
            )
            return ToolExecuteResponse(
                project_id=request.project_id,
                run_id=request.run_id,
                tool=request.tool,
                status="ok" if completed.returncode == 0 else "failed",
                stdout=completed.stdout[-20_000:],
                stderr=completed.stderr[-20_000:],
                exit_code=completed.returncode,
                duration_ms=(time.perf_counter() - started) * 1000,
            )
        except subprocess.TimeoutExpired as exc:
            stdout = exc.stdout if isinstance(exc.stdout, str) else ""
            stderr = exc.stderr if isinstance(exc.stderr, str) else ""
            return ToolExecuteResponse(
                project_id=request.project_id,
                run_id=request.run_id,
                tool=request.tool,
                status="timeout",
                stdout=stdout[-20_000:],
                stderr=stderr[-20_000:],
                exit_code=None,
                duration_ms=(time.perf_counter() - started) * 1000,
            )
