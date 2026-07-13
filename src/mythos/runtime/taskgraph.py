"""Durable MVP TaskGraph runtime."""

from __future__ import annotations

import time
import uuid
from typing import Any

from pydantic import Field

from src.mythos.db import LocalStore
from src.mythos.shared.schemas import StrictModel


TASK_NODE_ORDER = [
    ("understand", "Understand request and repository target"),
    ("planner", "Build durable implementation plan"),
    ("retrieve_context", "Retrieve ranked repository context"),
    ("edit", "Generate patch proposal"),
    ("test", "Run targeted tests"),
    ("critic_review", "Review correctness and reasoning risks"),
    ("security_review", "Review defensive security constraints"),
    ("performance_review", "Review performance and maintainability"),
    ("verify", "Verify patch and security constraints"),
    ("summarize", "Return final answer and evidence"),
]


class TaskNode(StrictModel):
    node_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    kind: str
    title: str
    instructions: str
    status: str = "queued"
    depends_on: list[str] = Field(default_factory=list)
    inputs: dict[str, Any] = Field(default_factory=dict)
    outputs: dict[str, Any] = Field(default_factory=dict)
    attempt: int = 0
    max_attempts: int = 2
    started_at: float | None = None
    finished_at: float | None = None


class TaskGraph(StrictModel):
    task_graph_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    project_id: str
    run_id: str
    goal: str
    status: str = "queued"
    nodes: list[TaskNode]
    edges: list[dict[str, str]]
    success_criteria: list[str]
    created_at_unix: float = Field(default_factory=time.time)
    updated_at_unix: float = Field(default_factory=time.time)


class AgentRunCreateRequest(StrictModel):
    project_id: str
    session_id: str | None = None
    prompt: str = Field(min_length=1)
    mode: str = Field(default="code_edit", pattern="^(code_edit|debug|explain|security_review)$")
    max_steps: int = Field(default=12, ge=1, le=50)
    apply_patch: bool = False
    model_profile: str = "mock"


class AgentRunCreateResponse(StrictModel):
    run_id: str
    project_id: str
    session_id: str | None
    status: str
    task_graph_id: str


class TaskAdvanceResponse(StrictModel):
    task_graph_id: str
    status: str
    active_task: dict[str, Any] | None = None
    ready_task_count: int
    completed_task_count: int
    failed_task_count: int


class TaskCompleteRequest(StrictModel):
    outputs: dict[str, Any] = Field(default_factory=dict)


class TaskFailRequest(StrictModel):
    error: str = Field(min_length=1)
    outputs: dict[str, Any] = Field(default_factory=dict)


class TaskGraphRuntime:
    def __init__(self, store: LocalStore | None = None) -> None:
        self.store = store or LocalStore()

    def create_agent_run(self, request: AgentRunCreateRequest) -> AgentRunCreateResponse:
        run = self.store.create_run(
            project_id=request.project_id,
            session_id=request.session_id,
            prompt=request.prompt,
            mode=request.mode,
            model_profile=request.model_profile,
            status="queued",
        )
        graph = self.build_graph(
            project_id=request.project_id,
            run_id=run["id"],
            prompt=request.prompt,
            mode=request.mode,
            max_steps=request.max_steps,
            apply_patch=request.apply_patch,
        )
        self.store.create_task_graph(
            project_id=request.project_id,
            run_id=run["id"],
            goal=graph.goal,
            status=graph.status,
            graph=graph.model_dump(),
        )
        return AgentRunCreateResponse(
            run_id=run["id"],
            project_id=request.project_id,
            session_id=request.session_id,
            status=run["status"],
            task_graph_id=graph.task_graph_id,
        )

    def build_graph(
        self,
        *,
        project_id: str,
        run_id: str,
        prompt: str,
        mode: str,
        max_steps: int,
        apply_patch: bool,
    ) -> TaskGraph:
        nodes: list[TaskNode] = []
        previous_id: str | None = None
        for kind, title in TASK_NODE_ORDER:
            node = TaskNode(
                kind=kind,
                title=title,
                instructions=self.instructions_for(kind, prompt=prompt, mode=mode, apply_patch=apply_patch),
                depends_on=[previous_id] if previous_id else [],
                inputs={"prompt": prompt, "mode": mode, "max_steps": max_steps} if kind == "understand" else {},
            )
            nodes.append(node)
            previous_id = node.node_id
        edges = [
            {"from": nodes[index].node_id, "to": nodes[index + 1].node_id, "condition": "success"}
            for index in range(len(nodes) - 1)
        ]
        return TaskGraph(
            project_id=project_id,
            run_id=run_id,
            goal=f"Complete Mythos agent request: {prompt}",
            nodes=nodes,
            edges=edges,
            success_criteria=[
                "context is relevant to the prompt",
                "patch is previewed before apply",
                "tests or verification commands complete",
                "failed patches are rejected or rolled back",
            ],
        )

    def instructions_for(self, kind: str, *, prompt: str, mode: str, apply_patch: bool) -> str:
        instructions = {
            "understand": "Classify intent, target files, risks, and expected verification evidence.",
            "planner": "Create a durable dependency-aware task plan. Do not write executable code in this stage.",
            "retrieve_context": "Build a ranked context pack from indexed repository chunks.",
            "edit": "Generate minimal file edits as structured patch operations.",
            "test": "Run targeted tests and capture stdout, stderr, exit code, and duration.",
            "critic_review": "Review executor outputs for logic gaps, missing requirements, and incorrect assumptions. Do not produce the final solution.",
            "security_review": "Review patch and evidence for defensive security risks, secret exposure, unsafe execution, and vulnerability regressions.",
            "performance_review": "Review patch for unnecessary complexity, slow paths, resource usage, and maintainability risk.",
            "verify": "Accept only if patch applies cleanly and verification passes.",
            "summarize": "Return concise final answer with changed files and verification evidence.",
        }
        base = instructions[kind]
        return f"{base} mode={mode}; apply_patch={apply_patch}; prompt={prompt}"

    def advance(self, task_graph_id: str) -> TaskAdvanceResponse:
        graph = self.store.get_task_graph(task_graph_id)
        if graph is None:
            raise KeyError(f"unknown task graph: {task_graph_id}")
        tasks = self.store.list_tasks(task_graph_id)
        failed = [task for task in tasks if task["status"] == "failed"]
        if failed:
            self.store.update_task_graph_status(task_graph_id, "failed")
            return self.advance_report(task_graph_id, "failed", None, tasks)
        running = [task for task in tasks if task["status"] == "running"]
        if running:
            return self.advance_report(task_graph_id, "running", running[0], tasks)
        ready = self.ready_tasks(tasks)
        if ready:
            task = ready[0]
            self.store.update_task_state(task["id"], status="running", started=True)
            self.store.update_task_graph_status(task_graph_id, "running")
            started = self.store.get_task(task["id"])
            return self.advance_report(task_graph_id, "running", started, self.store.list_tasks(task_graph_id))
        if tasks and all(task["status"] == "completed" for task in tasks):
            self.store.update_task_graph_status(task_graph_id, "completed")
            return self.advance_report(task_graph_id, "completed", None, tasks)
        return self.advance_report(task_graph_id, str(graph.get("status") or "queued"), None, tasks)

    def complete_task(self, task_id: str, request: TaskCompleteRequest | None = None) -> TaskAdvanceResponse:
        task = self.store.get_task(task_id)
        if task is None:
            raise KeyError(f"unknown task: {task_id}")
        self.store.update_task_state(
            task_id,
            status="completed",
            outputs=(request.outputs if request else {}),
            error=None,
            finished=True,
        )
        return self.advance(task["task_graph_id"])

    def fail_task(self, task_id: str, request: TaskFailRequest) -> TaskAdvanceResponse:
        task = self.store.get_task(task_id)
        if task is None:
            raise KeyError(f"unknown task: {task_id}")
        self.store.update_task_state(
            task_id,
            status="failed",
            outputs=request.outputs,
            error=request.error,
            finished=True,
        )
        self.store.update_task_graph_status(task["task_graph_id"], "failed")
        return self.advance(task["task_graph_id"])

    def ready_tasks(self, tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
        completed = {task["id"] for task in tasks if task["status"] == "completed"}
        return [
            task
            for task in tasks
            if task["status"] == "queued" and all(dependency in completed for dependency in task["depends_on"])
        ]

    def advance_report(
        self,
        task_graph_id: str,
        status: str,
        active_task: dict[str, Any] | None,
        tasks: list[dict[str, Any]],
    ) -> TaskAdvanceResponse:
        ready = self.ready_tasks(tasks)
        return TaskAdvanceResponse(
            task_graph_id=task_graph_id,
            status=status,
            active_task=active_task,
            ready_task_count=len(ready),
            completed_task_count=sum(1 for task in tasks if task["status"] == "completed"),
            failed_task_count=sum(1 for task in tasks if task["status"] == "failed"),
        )
