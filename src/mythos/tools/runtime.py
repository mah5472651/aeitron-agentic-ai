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


class SandboxPolicy(StrictModel):
    timeout_ms: int = 30_000
    network_disabled: bool = True


class SandboxRequest(StrictModel):
    command: list[str] = Field(min_length=1)


class ExecutionRequest(ToolExecuteRequest):
    pass


class ExecutionResult(ToolExecuteResponse):
    pass


ALLOWED_TOOL_COMMANDS: dict[str, set[str]] = {
    "git_diff": {"git"},
    "test": {"python", "python.exe", "python3", "pytest", "pytest.exe", "npm", "npm.cmd", "node", "node.exe"},
    "shell": {"python", "python.exe", "python3", "pytest", "pytest.exe", "git", "npm", "npm.cmd", "node", "node.exe"},
}


def project_root(store: LocalStore, project_id: str) -> Path:
    project = store.get_project(project_id)
    if project is None:
        raise KeyError(f"unknown project: {project_id}")
    root = Path(str(project["repo_path"])).resolve()
    if not root.exists() or not root.is_dir():
        raise FileNotFoundError(f"project repo_path is not a directory: {root}")
    return root


def validate_command_policy(request: ToolExecuteRequest) -> None:
    executable = Path(request.command[0]).name.lower()
    allowed = ALLOWED_TOOL_COMMANDS.get(request.tool, set())
    if executable not in allowed:
        raise ValueError(f"command {executable!r} is not allowed for tool {request.tool!r}")
    if request.tool == "git_diff" and request.command[:2] != ["git", "diff"]:
        raise ValueError("git_diff tool may only run: git diff")


class ToolRuntime:
    def __init__(self, store: LocalStore | None = None) -> None:
        self.store = store or LocalStore()

    def execute(self, request: ToolExecuteRequest) -> ToolExecuteResponse:
        root = project_root(self.store, request.project_id)
        validate_command_policy(request)
        started = time.perf_counter()
        try:
            completed = subprocess.run(  # nosec B603 - argv list, shell disabled. # nosemgrep: python.django.security.injection.command.subprocess-injection.subprocess-injection
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


class SandboxEngine:
    async def run(self, request: ExecutionRequest) -> ExecutionResult:
        result = ToolRuntime().execute(request)
        return ExecutionResult.model_validate(result.model_dump())
