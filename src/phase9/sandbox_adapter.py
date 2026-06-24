#!/usr/bin/env python
"""Sandbox execution adapter for benchmark code."""

from __future__ import annotations

from src.phase2.docker_sandbox_engine import ExecutionRequest, SandboxEngine, SandboxFile


class SandboxBenchmarkRunner:
    """Runs generated benchmark programs through the hardened Phase 2 sandbox."""

    def __init__(self, image: str = "python:3.12-slim", pool_size: int = 4) -> None:
        self.image = image
        self.engine = SandboxEngine(pool_size=pool_size)

    async def __aenter__(self) -> "SandboxBenchmarkRunner":
        await self.engine.start()
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        await self.engine.close()

    async def run_python(self, code: str, *, request_id: str | None = None) -> tuple[bool, int | None, str, str, float]:
        request = ExecutionRequest(
            files=[SandboxFile(path="main.py", content=code)],
            compile_command=None,
            run_command="python3 /workspace/main.py",
            image=self.image,
            request_id=request_id,
        )
        result = await self.engine.run(request)
        return result.ok, result.exit_code, result.stdout, result.stderr, result.metrics.wall_time_us / 1000


class MockSandboxBenchmarkRunner:
    """Drop-in replacement for offline tests that should not touch Docker."""

    def __init__(self, always_pass: bool = True) -> None:
        self.always_pass = always_pass

    async def __aenter__(self) -> "MockSandboxBenchmarkRunner":
        return self

    async def __aexit__(self, *_: object) -> None:
        pass

    async def run_python(self, code: str, *, request_id: str | None = None) -> tuple[bool, int, str, str, float]:
        del code, request_id
        if self.always_pass:
            return True, 0, "OK", "", 10.0
        return False, 1, "", "mock failure", 10.0
