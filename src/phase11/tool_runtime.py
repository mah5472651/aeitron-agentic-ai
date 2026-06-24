#!/usr/bin/env python
"""Validated tool runtime for chat and agent workflows."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Callable, Awaitable

from pydantic import BaseModel, Field

from src.phase11.memory_engine import iter_source_files, safe_workspace
from src.phase11.schemas import ToolResult
from src.phase11.security_engine import SecurityReasoningEngine


class ToolCallRequest(BaseModel):
    name: str = Field(min_length=1)
    args: dict[str, Any] = Field(default_factory=dict)
    workspace: str


class ToolSpec(BaseModel):
    name: str
    description: str
    args_schema: dict[str, str]


def resolve_inside(root: Path, relative: str) -> Path:
    normalized = relative.replace("\\", "/").lstrip("/")
    candidate = (root / normalized).resolve()
    if candidate != root and root not in candidate.parents:
        raise ValueError(f"path escapes workspace: {relative}")
    return candidate


class ToolRegistry:
    """Small safe tool bus shared by the FastAPI backend and agent runtime."""

    def __init__(self, workspace: str | Path, *, security: SecurityReasoningEngine | None = None) -> None:
        self.workspace = safe_workspace(workspace)
        self.security = security or SecurityReasoningEngine()
        self._tools: dict[str, Callable[[dict[str, Any]], Awaitable[ToolResult]]] = {
            "list_files": self._list_files,
            "read_file": self._read_file,
            "security_analyze_workspace": self._security_analyze_workspace,
            "sandbox_python": self._sandbox_python,
        }

    def specs(self) -> list[ToolSpec]:
        return [
            ToolSpec(
                name="list_files",
                description="List source files indexed from the current workspace.",
                args_schema={"max_files": "integer <= 500"},
            ),
            ToolSpec(
                name="read_file",
                description="Read one file inside the workspace with path escape protection.",
                args_schema={"path": "relative workspace file path", "max_bytes": "integer <= 200000"},
            ),
            ToolSpec(
                name="security_analyze_workspace",
                description="Run rule-based vulnerability triage over source files.",
                args_schema={"include_fixtures": "boolean"},
            ),
            ToolSpec(
                name="sandbox_python",
                description="Run a Python snippet inside the hardened Phase 2 Docker sandbox.",
                args_schema={"code": "python source string", "image": "optional Docker image"},
            ),
        ]

    async def call(self, name: str, args: dict[str, Any] | None = None) -> ToolResult:
        tool = self._tools.get(name)
        if tool is None:
            return ToolResult(
                tool=name,
                ok=False,
                summary=f"unknown tool: {name}",
                data={"available_tools": [spec.name for spec in self.specs()]},
            )
        try:
            return await tool(args or {})
        except Exception as exc:
            return ToolResult(tool=name, ok=False, summary=f"{type(exc).__name__}: {exc}")

    async def _list_files(self, args: dict[str, Any]) -> ToolResult:
        max_files = min(int(args.get("max_files", 100)), 500)
        files = [path.relative_to(self.workspace).as_posix() for path in iter_source_files(self.workspace, max_files=max_files)]
        return ToolResult(
            tool="list_files",
            ok=True,
            summary=f"listed {len(files)} source files",
            data={"files": files},
        )

    async def _read_file(self, args: dict[str, Any]) -> ToolResult:
        relative = str(args.get("path") or "")
        if not relative:
            return ToolResult(tool="read_file", ok=False, summary="missing required arg: path")
        max_bytes = min(int(args.get("max_bytes", 64_000)), 200_000)
        path = resolve_inside(self.workspace, relative)
        if not path.exists() or not path.is_file():
            return ToolResult(tool="read_file", ok=False, summary=f"file not found: {relative}")
        raw = await asyncio.to_thread(path.read_bytes)
        text = raw[:max_bytes].decode("utf-8", errors="replace")
        return ToolResult(
            tool="read_file",
            ok=True,
            summary=f"read {relative}",
            stdout=text,
            data={"path": relative, "bytes_read": min(len(raw), max_bytes), "truncated": len(raw) > max_bytes},
        )

    async def _security_analyze_workspace(self, args: dict[str, Any]) -> ToolResult:
        include_fixtures = bool(args.get("include_fixtures", False))
        review = await asyncio.to_thread(
            self.security.analyze_workspace,
            self.workspace,
            include_fixtures=include_fixtures,
        )
        return ToolResult(
            tool="security_analyze_workspace",
            ok=review.score >= 0.5,
            summary=review.summary,
            data=review.model_dump(),
        )

    async def _sandbox_python(self, args: dict[str, Any]) -> ToolResult:
        code = str(args.get("code") or "print('phase11 sandbox ok')")
        image = str(args.get("image") or "python:3.12-slim")
        try:
            from src.phase2.docker_sandbox_engine import ExecutionRequest, SandboxEngine, SandboxFile

            request = ExecutionRequest(
                files=[SandboxFile(path="main.py", content=code)],
                compile_command=None,
                run_command="python3 /workspace/main.py",
                image=image,
                env={"PYTHONDONTWRITEBYTECODE": "1"},
                request_id="phase11-tool-sandbox",
                pull_missing_image=False,
            )
            async with SandboxEngine(pool_size=1) as engine:
                result = await engine.run(request)
            return ToolResult(
                tool="sandbox_python",
                ok=result.ok,
                summary=f"exit_code={result.exit_code} timeout={result.timeout} flag={result.flag}",
                stdout=result.stdout,
                stderr=result.stderr,
                data={
                    "exit_code": result.exit_code,
                    "timeout": result.timeout,
                    "flag": result.flag,
                    "metrics": result.metrics.__dict__,
                    "image": result.image,
                    "command": result.command,
                    "error": result.error,
                },
            )
        except Exception as exc:
            return ToolResult(
                tool="sandbox_python",
                ok=False,
                summary=f"sandbox unavailable or failed: {type(exc).__name__}: {exc}",
                data={"image": image},
            )
