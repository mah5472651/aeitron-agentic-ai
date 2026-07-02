"""Native single-agent Mythos runtime."""

from __future__ import annotations

import time
from pathlib import Path

from src.mythos.db import LocalStore
from src.mythos.indexing import ContextBuilder, RepositoryIndexer
from src.mythos.model_ops.backends import build_active_backend
from src.mythos.planning.engine import IntentPlanningEngine
from src.mythos.runtime.taskgraph import AgentRunCreateRequest, TaskGraphRuntime
from src.mythos.shared.schemas import MythosRunReport, MythosRunRequest


class MythosRuntime:
    def __init__(self) -> None:
        self.planner = IntentPlanningEngine()

    async def run(self, request: MythosRunRequest) -> MythosRunReport:
        started = time.perf_counter()
        plan = self.planner.plan(request.prompt)
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
        backend = build_active_backend()
        try:
            answer = await backend.generate(
                f"{context.prompt_context}\n\nUser request:\n{request.prompt}\n\nReturn a concise implementation plan and patch guidance.",
                temperature=0.2,
                max_tokens=1024,
            )
        finally:
            await backend.aclose()
        return MythosRunReport(
            run_id=agent_run.run_id,
            status="complete",
            summary="Native Mythos runtime completed planning, indexing, context packing, and model response.",
            confidence=plan.confidence,
            prompt=request.prompt,
            workspace=workspace,
            final_answer=answer,
            route={"intent": plan.expansion.get("intent"), "runtime": "native-single-agent"},
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
