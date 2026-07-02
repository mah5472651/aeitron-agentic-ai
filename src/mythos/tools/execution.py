"""Compatibility exports for the native Tool Execution Layer."""

from __future__ import annotations

from pydantic import Field

from src.mythos.shared.schemas import StrictModel
from src.mythos.tools.runtime import ToolExecuteRequest, ToolExecuteResponse, ToolRuntime


class SandboxPolicy(StrictModel):
    timeout_ms: int = 30_000
    network_disabled: bool = True


class SandboxRequest(StrictModel):
    command: list[str] = Field(min_length=1)


class ExecutionRequest(ToolExecuteRequest):
    pass


class ExecutionResult(ToolExecuteResponse):
    pass


class SandboxEngine:
    async def run(self, request: ExecutionRequest) -> ExecutionResult:
        result = ToolRuntime().execute(request)
        return ExecutionResult.model_validate(result.model_dump())

__all__ = [
    "ExecutionRequest",
    "ExecutionResult",
    "SandboxEngine",
    "SandboxRequest",
    "SandboxPolicy",
]
