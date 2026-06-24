#!/usr/bin/env python
"""Durable task graph planner for agentic coding workflows."""

from __future__ import annotations

import hashlib
import json
import time
from collections import deque
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


def stable_id(*parts: object, length: int = 18) -> str:
    raw = "\x1f".join(str(part) for part in parts).encode("utf-8", errors="replace")
    return hashlib.sha256(raw).hexdigest()[:length]


def now_ms() -> int:
    return int(time.time() * 1000)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class TaskStatus(str, Enum):
    PENDING = "pending"
    READY = "ready"
    RUNNING = "running"
    BLOCKED = "blocked"
    FAILED = "failed"
    COMPLETE = "complete"


class TaskNode(StrictModel):
    task_id: str
    title: str
    description: str
    role: str
    dependencies: list[str] = Field(default_factory=list)
    status: TaskStatus = TaskStatus.PENDING
    priority: int = Field(default=50, ge=0, le=100)
    inputs: dict[str, Any] = Field(default_factory=dict)
    artifacts: list[str] = Field(default_factory=list)
    retries: int = Field(default=0, ge=0)

    @field_validator("dependencies")
    @classmethod
    def unique_dependencies(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(value))


class TaskGraph(StrictModel):
    graph_id: str
    objective: str
    nodes: dict[str, TaskNode]
    created_at_ms: int = Field(default_factory=now_ms)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("nodes")
    @classmethod
    def require_nodes(cls, value: dict[str, TaskNode]) -> dict[str, TaskNode]:
        if not value:
            raise ValueError("task graph must contain at least one node")
        return value

    def validate_acyclic(self) -> None:
        known = set(self.nodes)
        for node in self.nodes.values():
            missing = [dependency for dependency in node.dependencies if dependency not in known]
            if missing:
                raise ValueError(f"{node.task_id} has missing dependencies: {missing}")
        self.topological_layers()

    def ready_nodes(self) -> list[TaskNode]:
        complete = {task_id for task_id, node in self.nodes.items() if node.status == TaskStatus.COMPLETE}
        ready: list[TaskNode] = []
        for node in self.nodes.values():
            if node.status not in {TaskStatus.PENDING, TaskStatus.READY}:
                continue
            if all(dependency in complete for dependency in node.dependencies):
                node.status = TaskStatus.READY
                ready.append(node)
        return sorted(ready, key=lambda item: (-item.priority, item.task_id))

    def mark_running(self, task_id: str) -> None:
        self.nodes[task_id].status = TaskStatus.RUNNING

    def mark_complete(self, task_id: str, artifact_ids: list[str] | None = None) -> None:
        node = self.nodes[task_id]
        node.status = TaskStatus.COMPLETE
        if artifact_ids:
            node.artifacts = list(dict.fromkeys([*node.artifacts, *artifact_ids]))

    def mark_failed(self, task_id: str, *, retryable: bool = True) -> None:
        node = self.nodes[task_id]
        node.retries += 1
        node.status = TaskStatus.BLOCKED if retryable else TaskStatus.FAILED

    def complete(self) -> bool:
        return all(node.status == TaskStatus.COMPLETE for node in self.nodes.values())

    def topological_layers(self) -> list[list[TaskNode]]:
        indegree = {task_id: 0 for task_id in self.nodes}
        children = {task_id: [] for task_id in self.nodes}
        for node in self.nodes.values():
            for dependency in node.dependencies:
                indegree[node.task_id] += 1
                children[dependency].append(node.task_id)
        queue = deque(sorted(task_id for task_id, degree in indegree.items() if degree == 0))
        layers: list[list[TaskNode]] = []
        visited = 0
        while queue:
            current_layer_ids = list(queue)
            queue.clear()
            layers.append([self.nodes[task_id] for task_id in current_layer_ids])
            for task_id in current_layer_ids:
                visited += 1
                for child in sorted(children[task_id]):
                    indegree[child] -= 1
                    if indegree[child] == 0:
                        queue.append(child)
        if visited != len(self.nodes):
            raise ValueError("task graph contains a dependency cycle")
        return layers

    def to_json(self) -> str:
        return self.model_dump_json(indent=2)

    @classmethod
    def from_json(cls, text: str) -> "TaskGraph":
        return cls.model_validate_json(text)


class TaskGraphPlanner:
    """Deterministic planner that turns short prompts into durable DAGs.

    This is intentionally model-independent. A future high-reasoning planner can
    replace only this class while preserving the persisted graph schema.
    """

    def plan(self, prompt: str, *, workspace_summary: str = "") -> TaskGraph:
        lower = prompt.lower()
        graph_id = f"tg-{stable_id(prompt, workspace_summary, int(time.time() // 60))}"
        nodes: dict[str, TaskNode] = {}

        def add(
            suffix: str,
            title: str,
            description: str,
            role: str,
            dependencies: list[str] | None = None,
            priority: int = 50,
        ) -> str:
            task_id = f"{graph_id}-{suffix}"
            nodes[task_id] = TaskNode(
                task_id=task_id,
                title=title,
                description=description,
                role=role,
                dependencies=dependencies or [],
                priority=priority,
                inputs={"prompt": prompt, "workspace_summary": workspace_summary},
            )
            return task_id

        intent = add(
            "intent",
            "Expand Intent",
            "Normalize the user's short prompt into explicit engineering goals, constraints, and success checks.",
            "architect",
            priority=98,
        )
        research = None
        if any(token in lower for token in ("research", "docs", "current", "latest", "api", "library")):
            research = add(
                "research",
                "Gather Technical Context",
                "Collect relevant documentation or repository context without executing offensive actions.",
                "researcher",
                dependencies=[intent],
                priority=88,
            )
        architecture = add(
            "architecture",
            "Design Implementation",
            "Choose files, data flow, edge cases, security constraints, and verification plan.",
            "architect",
            dependencies=[research or intent],
            priority=92,
        )
        code = add(
            "code",
            "Implement Patch",
            "Generate minimal production-ready code changes consistent with the existing repository.",
            "coder",
            dependencies=[architecture],
            priority=85,
        )
        test = add(
            "test",
            "Run Functional Verification",
            "Execute tests or sandbox checks and capture deterministic telemetry.",
            "tester",
            dependencies=[code],
            priority=82,
        )
        security = add(
            "security",
            "Defensive Security Review",
            "Detect vulnerabilities, compare patch risk, and recommend safe remediation.",
            "security_auditor",
            dependencies=[code],
            priority=84,
        )
        if any(token in lower for token in ("bug", "debug", "fix", "error", "traceback", "crash", "failing")):
            debug = add(
                "debug",
                "Repair Failing Path",
                "Use test/crash telemetry to correct logic and prevent regression.",
                "debugger",
                dependencies=[test, security],
                priority=86,
            )
            review_dependencies = [debug]
        else:
            review_dependencies = [test, security]
        add(
            "review",
            "Critic Review And Final Answer",
            "Peer-review artifacts, verify confidence threshold, and produce final implementation summary.",
            "reviewer",
            dependencies=review_dependencies,
            priority=80,
        )

        graph = TaskGraph(graph_id=graph_id, objective=prompt, nodes=nodes, metadata={"planner": "phase16-deterministic-v1"})
        graph.validate_acyclic()
        return graph


class TaskGraphStore:
    def __init__(self, root: str | Path = "artifacts/phase16/task_graphs") -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def save(self, graph: TaskGraph) -> Path:
        graph.validate_acyclic()
        path = self.root / f"{graph.graph_id}.json"
        path.write_text(graph.to_json(), encoding="utf-8")
        return path

    def load(self, graph_id: str) -> TaskGraph:
        path = self.root / f"{graph_id}.json"
        return TaskGraph.from_json(path.read_text(encoding="utf-8"))

    def latest(self) -> TaskGraph | None:
        candidates = sorted(self.root.glob("*.json"), key=lambda path: path.stat().st_mtime, reverse=True)
        if not candidates:
            return None
        return TaskGraph.from_json(candidates[0].read_text(encoding="utf-8"))

