"""Native single-agent Aeitron runtime."""

from __future__ import annotations

import time
from pathlib import Path
from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import Field

from src.aeitron.db import LocalStore
from src.aeitron.indexing import ContextBuilder, RepositoryIndexer
from src.aeitron.model_ops.backends import build_active_backend
from src.aeitron.planning.engine import IntentPlanningEngine
from src.aeitron.runtime.taskgraph import AgentRunCreateRequest, TaskCompleteRequest, TaskFailRequest, TaskGraphRuntime
from src.aeitron.shared.schemas import AeitronRunReport, AeitronRunRequest
from src.aeitron.shared.schemas import StrictModel


AgentWorker = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


class AgentWorkerPoolReport(StrictModel):
    task_graph_id: str
    status: str
    completed: int = 0
    failed: int = 0
    iterations: int = 0
    concurrency: int = Field(default=1, ge=1, le=64)
    missing_handlers: list[str] = Field(default_factory=list)


class AgentWorkerPool:
    """Dependency-aware worker pool for TaskGraph nodes.

    Handlers are injected by the caller. The pool never fabricates agent output:
    an unregistered task kind is a failed orchestration contract, not a fake
    successful worker.
    """

    def __init__(self, runtime: TaskGraphRuntime, *, concurrency: int = 1) -> None:
        if concurrency < 1:
            raise ValueError("concurrency must be >= 1")
        self.runtime = runtime
        self.concurrency = concurrency
        self.handlers: dict[str, AgentWorker] = {}

    def register(self, kind: str, handler: AgentWorker) -> None:
        self.handlers[kind] = handler

    async def run_until_blocked_or_complete(self, task_graph_id: str, *, max_iterations: int = 100) -> AgentWorkerPoolReport:
        completed = 0
        failed = 0
        missing: list[str] = []
        iterations = 0
        while iterations < max_iterations:
            iterations += 1
            advance = self.runtime.advance(task_graph_id)
            if advance.status in {"completed", "failed"} or advance.active_task is None:
                return AgentWorkerPoolReport(
                    task_graph_id=task_graph_id,
                    status=advance.status,
                    completed=completed,
                    failed=failed,
                    iterations=iterations,
                    concurrency=self.concurrency,
                    missing_handlers=missing,
                )
            task = advance.active_task
            kind = str(task.get("kind") or "")
            handler = self.handlers.get(kind)
            if handler is None:
                missing.append(kind)
                self.runtime.fail_task(task["id"], TaskFailRequest(error=f"no worker registered for task kind: {kind}"))
                failed += 1
                continue
            try:
                outputs = await handler(task)
                self.runtime.complete_task(task["id"], TaskCompleteRequest(outputs=outputs))
                completed += 1
            except Exception as exc:
                self.runtime.fail_task(task["id"], TaskFailRequest(error=str(exc)))
                failed += 1
        return AgentWorkerPoolReport(
            task_graph_id=task_graph_id,
            status="blocked",
            completed=completed,
            failed=failed,
            iterations=iterations,
            concurrency=self.concurrency,
            missing_handlers=missing,
        )


class AgentRouter:
    """Role router for the native runtime.

    This is intentionally a lightweight routing policy, not a separate
    framework or fake neural MoE. It helps the runtime choose which worker
    roles should be active for a request.
    """

    def route(self, prompt: str, *, top_k: int = 4) -> dict[str, object]:
        lowered = prompt.lower()
        candidates: list[dict[str, object]] = []
        if any(term in lowered for term in ["security", "vulnerability", "cve", "secret", "xss", "sql injection"]):
            candidates.append({"role": "security", "score": 0.95})
        if any(term in lowered for term in ["test", "pytest", "fail", "bug", "regression"]):
            candidates.append({"role": "testing", "score": 0.9})
        if any(term in lowered for term in ["code", "build", "implement", "fix", "refactor"]):
            candidates.append({"role": "coding", "score": 0.88})
        if any(term in lowered for term in ["design", "architecture", "plan", "system"]):
            candidates.append({"role": "architect", "score": 0.84})
        candidates.append({"role": "planner", "score": 0.75})
        return {"route": candidates[:top_k], "top_role": candidates[0]["role"], "router": "native"}


class AeitronRuntime:
    def __init__(self) -> None:
        self.planner = IntentPlanningEngine()
        self.router = AgentRouter()

    async def run(self, request: AeitronRunRequest) -> AeitronRunReport:
        started = time.perf_counter()
        backend = build_active_backend()
        try:
            plan = await self.planner.plan_structured(
                request.prompt,
                backend=backend,
                allow_dev_fallback=backend.name == "mock" or request.policy_mode == "development",
            )
            route = self.router.route(request.prompt, top_k=request.max_agent_nodes or 4)
            store = LocalStore()
            workspace = str(Path(request.workspace).resolve())
            project = store.create_project(name=f"runtime-{time.time_ns()}", repo_path=workspace)
            index_report = RepositoryIndexer(store).index_project(project_id=project["id"])
            context = ContextBuilder(store).build(project_id=project["id"], query=request.prompt, token_budget=8000)
            agent_run = TaskGraphRuntime(store).create_agent_run(
                AgentRunCreateRequest(
                    project_id=project["id"],
                    prompt=request.prompt,
                    mode=plan.expansion.get("intent", "code_edit"),
                    max_steps=request.max_agent_nodes or 6,
                )
            )
            graph = store.get_task_graph(agent_run.task_graph_id)
            answer = await backend.generate(
                f"{context.prompt_context}\n\nUser request:\n{request.prompt}\n\nReturn a concise implementation plan and patch guidance.",
                temperature=0.2,
                max_tokens=1024,
            )
        finally:
            await backend.aclose()
        return AeitronRunReport(
            run_id=agent_run.run_id,
            status="complete",
            summary="Native Aeitron runtime completed planning, indexing, context packing, and model response.",
            confidence=plan.confidence,
            prompt=request.prompt,
            workspace=workspace,
            final_answer=answer,
            route={"intent": plan.expansion.get("intent"), "runtime": "native-single-agent", **route},
            plan=plan.model_dump(),
            memory={"context_id": context.context_id, "chunks": [chunk.model_dump(exclude={"content"}) for chunk in context.chunks]},
            verification=None,
            security=None,
            artifacts={
                "project": project,
                "index": index_report.model_dump(),
                "task_graph": graph,
            },
            duration_ms=(time.perf_counter() - started) * 1000,
        )

