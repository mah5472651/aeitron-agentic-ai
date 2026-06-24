#!/usr/bin/env python
"""Native asyncio Swarm Intelligence Orchestration Engine.

This module implements a dependency-light agent orchestration system without
CrewAI, AutoGen, LangChain, or similar frameworks. It uses:

- Python asyncio for scheduling and event loop control
- Pydantic v2 for strict JSON packet validation
- a structured data bus for micro-agent context exchange
- a model-driven task graph planner
- adversarial peer review hooks for code artifacts
- automatic correction cycles when reviewer confidence < 0.85

Run a fully executable mock workflow:

    python src/phase4/swarm_orchestrator.py --mock --prompt "Build a secure sandbox and quota backend"
"""

from __future__ import annotations

import argparse
import asyncio
import contextvars
import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from abc import ABC, abstractmethod
from collections import defaultdict
from enum import Enum
from pathlib import Path
from typing import Any, Awaitable, Callable, Iterable, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator


DEFAULT_ENDPOINT = "http://localhost:8000/v1"
DEFAULT_MODEL = "high-reasoning-agent-model"
REVIEW_THRESHOLD = 0.85
MAX_CORRECTION_CYCLES = 2

trace_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("trace_id", default="unknown")
agent_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("agent_id", default="master")


def validate_http_endpoint(endpoint: str) -> str:
    parsed = urllib.parse.urlparse(endpoint.rstrip("/"))
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("LLM endpoint must be an absolute http:// or https:// URL")
    return endpoint.rstrip("/")


class AgentRole(str, Enum):
    MASTER_PLANNER = "master_planner"
    CODE_ARCHITECT = "code_architect"
    DEEP_EXPLOIT_FUZZER = "deep_exploit_fuzzer"
    DATABASE_OPTIMIZER = "database_optimizer"
    SECURITY_REVIEWER = "security_reviewer"
    INFRA_ENGINEER = "infra_engineer"
    TEST_ENGINEER = "test_engineer"
    RESEARCH_SCIENTIST = "research_scientist"
    CORRECTION_ENGINEER = "correction_engineer"


class PacketType(str, Enum):
    TASK = "task"
    CONTEXT = "context"
    ARTIFACT = "artifact"
    REVIEW = "review"
    CORRECTION_REQUEST = "correction_request"
    STATUS = "status"


class ArtifactType(str, Enum):
    CODE = "code"
    PLAN = "plan"
    TESTS = "tests"
    REPORT = "report"
    SCHEMA = "schema"


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    BLOCKED = "blocked"
    COMPLETE = "complete"
    FAILED = "failed"
    NEEDS_CORRECTION = "needs_correction"


def stable_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def stable_id(*parts: object) -> str:
    return hashlib.sha256("\x1f".join(str(part) for part in parts).encode("utf-8")).hexdigest()[:24]


def now_ms() -> int:
    return int(time.time() * 1000)


def extract_json_object(raw: str) -> dict[str, Any]:
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("model output did not contain a JSON object")
        return json.loads(text[start : end + 1])


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class TaskNode(StrictModel):
    task_id: str
    role: AgentRole
    title: str = Field(min_length=1)
    objective: str = Field(min_length=1)
    priority: int = Field(ge=1)
    depends_on: list[str] = Field(default_factory=list)
    input_context: dict[str, Any] = Field(default_factory=dict)
    expected_artifacts: list[ArtifactType] = Field(default_factory=list)
    max_iterations: int = Field(default=MAX_CORRECTION_CYCLES, ge=0, le=5)

    @field_validator("depends_on")
    @classmethod
    def unique_dependencies(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("depends_on contains duplicate task IDs")
        return value


class TaskGraph(StrictModel):
    graph_id: str
    problem_prompt: str
    tasks: list[TaskNode]

    @field_validator("tasks")
    @classmethod
    def validate_task_graph(cls, tasks: list[TaskNode]) -> list[TaskNode]:
        ids = [task.task_id for task in tasks]
        if len(ids) != len(set(ids)):
            raise ValueError("task graph contains duplicate task_id values")
        id_set = set(ids)
        for task in tasks:
            missing = [dep for dep in task.depends_on if dep not in id_set]
            if missing:
                raise ValueError(f"task {task.task_id} depends on unknown tasks: {missing}")
        detect_cycles(tasks)
        return tasks


class DataPacket(StrictModel):
    packet_id: str
    trace_id: str
    packet_type: PacketType
    sender: str
    recipient: str | None = None
    topic: str
    payload: dict[str, Any]
    created_at_ms: int = Field(default_factory=now_ms)
    correlation_id: str | None = None


class ArtifactPayload(StrictModel):
    task_id: str
    artifact_id: str
    artifact_type: ArtifactType
    title: str
    content: str
    language: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentResultPayload(StrictModel):
    task_id: str
    role: AgentRole
    status: TaskStatus
    summary: str
    findings: list[str] = Field(default_factory=list)
    artifacts: list[ArtifactPayload] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    requested_context: list[str] = Field(default_factory=list)


class ReviewPayload(StrictModel):
    review_id: str
    task_id: str
    artifact_id: str
    reviewer_role: AgentRole
    score: float = Field(ge=0.0, le=1.0)
    approved: bool
    concrete_feedback: list[str]
    security_findings: list[str] = Field(default_factory=list)
    required_changes: list[str] = Field(default_factory=list)


class CorrectionRequestPayload(StrictModel):
    task_id: str
    artifact_id: str
    original_artifact: ArtifactPayload
    review: ReviewPayload
    correction_cycle: int = Field(ge=1, le=MAX_CORRECTION_CYCLES)


class OrchestrationReport(StrictModel):
    trace_id: str
    graph: TaskGraph
    accepted_artifacts: list[ArtifactPayload]
    rejected_artifacts: list[dict[str, Any]]
    reviews: list[ReviewPayload]
    task_results: list[AgentResultPayload]
    duration_ms: int


def detect_cycles(tasks: list[TaskNode]) -> None:
    graph = {task.task_id: set(task.depends_on) for task in tasks}
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str) -> None:
        if node in visited:
            return
        if node in visiting:
            raise ValueError(f"cycle detected at task {node}")
        visiting.add(node)
        for dependency in graph[node]:
            visit(dependency)
        visiting.remove(node)
        visited.add(node)

    for task_id in graph:
        visit(task_id)


class ModelBackend(Protocol):
    async def complete_json(self, *, system: str, user: str) -> dict[str, Any]:
        """Return a validated JSON object from an LLM backend."""


class OpenAICompatibleBackend:
    """Async stdlib HTTP client for OpenAI-compatible chat completion APIs."""

    def __init__(
        self,
        endpoint: str,
        model: str,
        api_key: str | None,
        temperature: float = 0.2,
        max_tokens: int = 4096,
        timeout_seconds: int = 180,
    ) -> None:
        self.endpoint = validate_http_endpoint(endpoint)
        self.model = model
        self.api_key = api_key
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout_seconds = timeout_seconds

    async def complete_json(self, *, system: str, user: str) -> dict[str, Any]:
        raw = await asyncio.to_thread(self._complete, system, user)
        return extract_json_object(raw)

    def _complete(self, system: str, user: str) -> str:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "response_format": {"type": "json_object"},
        }
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        request = urllib.request.Request(
            f"{self.endpoint}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:  # nosec B310
                body = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            details = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"LLM endpoint HTTP {exc.code}: {details}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"LLM endpoint error: {exc}") from exc
        return body["choices"][0]["message"]["content"]


class MockReasoningBackend:
    """Fully executable deterministic backend for local orchestration tests."""

    async def complete_json(self, *, system: str, user: str) -> dict[str, Any]:
        await asyncio.sleep(0)
        payload = extract_json_object(user)
        if "planner_contract" in payload:
            return self._mock_plan(payload["problem_prompt"])
        if "review_contract" in payload:
            return self._mock_review(payload)
        if "correction_contract" in payload:
            return self._mock_correction(payload)
        return self._mock_agent_result(payload)

    def _mock_plan(self, prompt: str) -> dict[str, Any]:
        roles = [AgentRole.CODE_ARCHITECT, AgentRole.DEEP_EXPLOIT_FUZZER, AgentRole.TEST_ENGINEER]
        lower = prompt.lower()
        if "database" in lower or "postgres" in lower or "redis" in lower:
            roles.append(AgentRole.DATABASE_OPTIMIZER)
        if "docker" in lower or "sandbox" in lower or "deploy" in lower:
            roles.append(AgentRole.INFRA_ENGINEER)
        tasks = []
        previous: str | None = None
        for index, role in enumerate(roles, start=1):
            task_id = stable_id(prompt, role.value, index)
            tasks.append(
                {
                    "task_id": task_id,
                    "role": role.value,
                    "title": role.value.replace("_", " ").title(),
                    "objective": f"Produce {role.value} deliverables for: {prompt}",
                    "priority": index,
                    "depends_on": [previous] if previous and role == AgentRole.TEST_ENGINEER else [],
                    "input_context": {"problem_prompt": prompt},
                    "expected_artifacts": ["code"] if role == AgentRole.CODE_ARCHITECT else ["report"],
                    "max_iterations": MAX_CORRECTION_CYCLES,
                }
            )
            previous = task_id
        return {"graph_id": stable_id(prompt, "graph"), "problem_prompt": prompt, "tasks": tasks}

    def _mock_agent_result(self, payload: dict[str, Any]) -> dict[str, Any]:
        task = payload["task"]
        role = task["role"]
        artifact_type = "code" if role == AgentRole.CODE_ARCHITECT.value else "report"
        if artifact_type == "code":
            content = "def insecure_eval(user_input):\n    return eval(user_input)\n"
            language = "python"
        else:
            content = f"{role} findings for {task['title']}"
            language = None
        return {
            "task_id": task["task_id"],
            "role": role,
            "status": "complete",
            "summary": f"{role} completed task {task['title']}",
            "findings": ["Generated primary artifact", "Published structured context packet"],
            "artifacts": [
                {
                    "task_id": task["task_id"],
                    "artifact_id": stable_id(task["task_id"], artifact_type),
                    "artifact_type": artifact_type,
                    "title": task["title"],
                    "content": content,
                    "language": language,
                    "metadata": {"mock": True},
                }
            ],
            "confidence": 0.88,
            "requested_context": [],
        }

    def _mock_review(self, payload: dict[str, Any]) -> dict[str, Any]:
        artifact = payload["artifact"]
        content = artifact["content"]
        insecure = "eval(" in content or "subprocess" in content
        score = 0.41 if insecure else 0.93
        return {
            "review_id": stable_id(artifact["artifact_id"], "review"),
            "task_id": artifact["task_id"],
            "artifact_id": artifact["artifact_id"],
            "reviewer_role": AgentRole.SECURITY_REVIEWER.value,
            "score": score,
            "approved": score >= REVIEW_THRESHOLD,
            "concrete_feedback": [
                "Remove dynamic code execution primitives."
            ]
            if insecure
            else ["Artifact satisfies security review threshold."],
            "security_findings": ["Unsafe eval usage"] if insecure else [],
            "required_changes": ["Replace eval with explicit safe parsing"] if insecure else [],
        }

    def _mock_correction(self, payload: dict[str, Any]) -> dict[str, Any]:
        request = payload["correction_request"]
        artifact = request["original_artifact"]
        return {
            "task_id": request["task_id"],
            "role": AgentRole.CORRECTION_ENGINEER.value,
            "status": "complete",
            "summary": "Corrected code artifact after adversarial review.",
            "findings": ["Removed unsafe dynamic execution path."],
            "artifacts": [
                {
                    "task_id": request["task_id"],
                    "artifact_id": stable_id(artifact["artifact_id"], "corrected", request["correction_cycle"]),
                    "artifact_type": artifact["artifact_type"],
                    "title": artifact["title"] + " corrected",
                    "content": "def safe_parse(user_input):\n    return str(user_input)\n",
                    "language": artifact.get("language"),
                    "metadata": {"correction_cycle": request["correction_cycle"]},
                }
            ],
            "confidence": 0.91,
            "requested_context": [],
        }


class DataBus:
    """Asynchronous structured JSON packet bus with validation hooks."""

    def __init__(self) -> None:
        self._subscribers: dict[str, list[asyncio.Queue[DataPacket]]] = defaultdict(list)
        self._history: list[DataPacket] = []
        self._hooks: list[Callable[[DataPacket], Awaitable[None]]] = []
        self._lock = asyncio.Lock()

    def add_hook(self, hook: Callable[[DataPacket], Awaitable[None]]) -> None:
        self._hooks.append(hook)

    async def subscribe(self, topic: str) -> asyncio.Queue[DataPacket]:
        queue: asyncio.Queue[DataPacket] = asyncio.Queue()
        async with self._lock:
            self._subscribers[topic].append(queue)
        return queue

    async def publish(self, packet: DataPacket) -> None:
        packet = DataPacket.model_validate(packet.model_dump())
        async with self._lock:
            self._history.append(packet)
            targets = list(self._subscribers.get(packet.topic, []))
            targets.extend(self._subscribers.get("*", []))
        for queue in targets:
            await queue.put(packet)
        for hook in self._hooks:
            await hook(packet)

    def history(self, packet_type: PacketType | None = None) -> list[DataPacket]:
        if packet_type is None:
            return list(self._history)
        return [packet for packet in self._history if packet.packet_type == packet_type]


class BaseMicroAgent(ABC):
    def __init__(
        self,
        agent_id: str,
        role: AgentRole,
        backend: ModelBackend,
        bus: DataBus,
    ) -> None:
        self.agent_id = agent_id
        self.role = role
        self.backend = backend
        self.bus = bus

    async def run(self, task: TaskNode, context_packets: list[DataPacket]) -> AgentResultPayload:
        token = agent_id_var.set(self.agent_id)
        try:
            result = await self._run_impl(task, context_packets)
            result = AgentResultPayload.model_validate(result.model_dump())
            await self._publish_result(result)
            return result
        finally:
            agent_id_var.reset(token)

    async def _publish_result(self, result: AgentResultPayload) -> None:
        await self.bus.publish(
            DataPacket(
                packet_id=stable_id(self.agent_id, result.task_id, "status", time.time_ns()),
                trace_id=trace_id_var.get(),
                packet_type=PacketType.STATUS,
                sender=self.agent_id,
                topic=f"task.{result.task_id}.status",
                payload=result.model_dump(mode="json"),
                correlation_id=result.task_id,
            )
        )
        for artifact in result.artifacts:
            await self.bus.publish(
                DataPacket(
                    packet_id=stable_id(self.agent_id, artifact.artifact_id, time.time_ns()),
                    trace_id=trace_id_var.get(),
                    packet_type=PacketType.ARTIFACT,
                    sender=self.agent_id,
                    topic=f"artifact.{artifact.artifact_type.value}",
                    payload=artifact.model_dump(mode="json"),
                    correlation_id=result.task_id,
                )
            )

    @abstractmethod
    async def _run_impl(self, task: TaskNode, context_packets: list[DataPacket]) -> AgentResultPayload:
        raise NotImplementedError


class LlmMicroAgent(BaseMicroAgent):
    async def _run_impl(self, task: TaskNode, context_packets: list[DataPacket]) -> AgentResultPayload:
        system = (
            f"You are {self.role.value}. Return only JSON matching AgentResultPayload: "
            "task_id, role, status, summary, findings, artifacts, confidence, requested_context."
        )
        user = stable_json(
            {
                "task": task.model_dump(mode="json"),
                "context_packets": [packet.model_dump(mode="json") for packet in context_packets[-20:]],
                "result_contract": AgentResultPayload.model_json_schema(),
            }
        )
        payload = await self.backend.complete_json(system=system, user=user)
        return AgentResultPayload.model_validate(payload)


class CorrectionAgent(LlmMicroAgent):
    async def correct(self, request: CorrectionRequestPayload) -> AgentResultPayload:
        system = (
            "You are a correction engineer. Return JSON matching AgentResultPayload. "
            "Fix the artifact according to the adversarial review."
        )
        user = stable_json(
            {
                "correction_contract": AgentResultPayload.model_json_schema(),
                "correction_request": request.model_dump(mode="json"),
            }
        )
        payload = await self.backend.complete_json(system=system, user=user)
        return AgentResultPayload.model_validate(payload)


class SecurityReviewerAgent:
    def __init__(self, backend: ModelBackend, bus: DataBus) -> None:
        self.agent_id = "security_reviewer"
        self.role = AgentRole.SECURITY_REVIEWER
        self.backend = backend
        self.bus = bus

    async def review(self, artifact: ArtifactPayload) -> ReviewPayload:
        token = agent_id_var.set(self.agent_id)
        try:
            system = (
                "You are an adversarial security reviewer. Return only JSON matching "
                "ReviewPayload: review_id, task_id, artifact_id, reviewer_role, score, "
                "approved, concrete_feedback, security_findings, required_changes."
            )
            user = stable_json(
                {
                    "review_contract": ReviewPayload.model_json_schema(),
                    "artifact": artifact.model_dump(mode="json"),
                    "threshold": REVIEW_THRESHOLD,
                }
            )
            payload = await self.backend.complete_json(system=system, user=user)
            review = ReviewPayload.model_validate(payload)
            await self.bus.publish(
                DataPacket(
                    packet_id=stable_id(review.review_id, time.time_ns()),
                    trace_id=trace_id_var.get(),
                    packet_type=PacketType.REVIEW,
                    sender=self.agent_id,
                    topic=f"review.{artifact.artifact_id}",
                    payload=review.model_dump(mode="json"),
                    correlation_id=artifact.task_id,
                )
            )
            return review
        finally:
            agent_id_var.reset(token)


class AgentFactory:
    def __init__(self, backend: ModelBackend, bus: DataBus) -> None:
        self.backend = backend
        self.bus = bus

    def create(self, role: AgentRole, task_id: str) -> BaseMicroAgent:
        if role == AgentRole.CORRECTION_ENGINEER:
            return CorrectionAgent(f"{role.value}:{task_id}", role, self.backend, self.bus)
        return LlmMicroAgent(f"{role.value}:{task_id}", role, self.backend, self.bus)


class GraphPlanner:
    def __init__(self, backend: ModelBackend) -> None:
        self.backend = backend

    async def plan(self, prompt: str) -> TaskGraph:
        system = (
            "You are the Master Planner. Split the problem into a prioritized DAG of subtasks. "
            "Return only JSON matching TaskGraph."
        )
        user = stable_json(
            {
                "problem_prompt": prompt,
                "planner_contract": TaskGraph.model_json_schema(),
                "available_roles": [role.value for role in AgentRole if role != AgentRole.MASTER_PLANNER],
                "artifact_types": [artifact.value for artifact in ArtifactType],
            }
        )
        try:
            payload = await self.backend.complete_json(system=system, user=user)
            return TaskGraph.model_validate(payload)
        except (ValidationError, ValueError):
            return self._heuristic_plan(prompt)

    @staticmethod
    def _heuristic_plan(prompt: str) -> TaskGraph:
        roles = [AgentRole.CODE_ARCHITECT, AgentRole.DEEP_EXPLOIT_FUZZER, AgentRole.TEST_ENGINEER]
        lower = prompt.lower()
        if any(word in lower for word in ["database", "postgres", "qdrant", "redis"]):
            roles.append(AgentRole.DATABASE_OPTIMIZER)
        if any(word in lower for word in ["docker", "sandbox", "infra", "deploy"]):
            roles.append(AgentRole.INFRA_ENGINEER)
        tasks: list[TaskNode] = []
        for index, role in enumerate(roles, start=1):
            expected = [ArtifactType.CODE] if role == AgentRole.CODE_ARCHITECT else [ArtifactType.REPORT]
            tasks.append(
                TaskNode(
                    task_id=stable_id(prompt, role.value, index),
                    role=role,
                    title=role.value.replace("_", " ").title(),
                    objective=f"Handle {role.value} analysis for: {prompt}",
                    priority=index,
                    depends_on=[],
                    input_context={"problem_prompt": prompt},
                    expected_artifacts=expected,
                )
            )
        return TaskGraph(graph_id=stable_id(prompt, "heuristic_graph"), problem_prompt=prompt, tasks=tasks)


class MasterOrchestrator:
    def __init__(
        self,
        backend: ModelBackend,
        review_threshold: float = REVIEW_THRESHOLD,
        max_concurrency: int = 8,
    ) -> None:
        self.backend = backend
        self.review_threshold = review_threshold
        self.max_concurrency = max_concurrency
        self.bus = DataBus()
        self.planner = GraphPlanner(backend)
        self.factory = AgentFactory(backend, self.bus)
        self.reviewer = SecurityReviewerAgent(backend, self.bus)
        self.accepted_artifacts: list[ArtifactPayload] = []
        self.rejected_artifacts: list[dict[str, Any]] = []
        self.reviews: list[ReviewPayload] = []
        self.task_results: list[AgentResultPayload] = []
        self._reviewed_artifacts: set[str] = set()
        self._correction_cycles: dict[str, int] = defaultdict(int)
        self._correction_tasks: list[asyncio.Task[None]] = []
        self.bus.add_hook(self._artifact_validation_hook)

    async def orchestrate(self, prompt: str) -> OrchestrationReport:
        started = now_ms()
        trace_id = stable_id(prompt, started)
        token = trace_id_var.set(trace_id)
        try:
            install_event_loop_hooks()
            graph = await self.planner.plan(prompt)
            await self._publish_graph(graph)
            results = await self._execute_graph(graph)
            self.task_results.extend(results)
            if self._correction_tasks:
                await asyncio.gather(*self._correction_tasks)
            duration = now_ms() - started
            return OrchestrationReport(
                trace_id=trace_id,
                graph=graph,
                accepted_artifacts=self.accepted_artifacts,
                rejected_artifacts=self.rejected_artifacts,
                reviews=self.reviews,
                task_results=self.task_results,
                duration_ms=duration,
            )
        finally:
            trace_id_var.reset(token)

    async def _publish_graph(self, graph: TaskGraph) -> None:
        await self.bus.publish(
            DataPacket(
                packet_id=stable_id(graph.graph_id, "graph"),
                trace_id=trace_id_var.get(),
                packet_type=PacketType.CONTEXT,
                sender="master",
                topic="graph",
                payload=graph.model_dump(mode="json"),
                correlation_id=graph.graph_id,
            )
        )

    async def _execute_graph(self, graph: TaskGraph) -> list[AgentResultPayload]:
        pending = {task.task_id: task for task in graph.tasks}
        completed: set[str] = set()
        failed: set[str] = set()
        results: list[AgentResultPayload] = []
        semaphore = asyncio.Semaphore(self.max_concurrency)

        while pending:
            ready = [
                task
                for task in sorted(pending.values(), key=lambda item: item.priority)
                if all(dep in completed for dep in task.depends_on)
            ]
            if not ready:
                blocked = list(pending)
                raise RuntimeError(f"task graph is blocked; pending={blocked}, failed={list(failed)}")

            async def run_task(task: TaskNode) -> tuple[TaskNode, AgentResultPayload | None, Exception | None]:
                async with semaphore:
                    try:
                        context = self._context_for_task(task)
                        agent = self.factory.create(task.role, task.task_id)
                        result = await agent.run(task, context)
                        return task, result, None
                    except Exception as exc:
                        return task, None, exc

            task_outputs = await asyncio.gather(*(run_task(task) for task in ready))
            for task, result, exc in task_outputs:
                pending.pop(task.task_id, None)
                if exc is not None or result is None:
                    failed.add(task.task_id)
                    self.rejected_artifacts.append(
                        {
                            "task_id": task.task_id,
                            "role": task.role.value,
                            "reason": f"{type(exc).__name__}: {exc}" if exc else "unknown failure",
                        }
                    )
                else:
                    completed.add(task.task_id)
                    results.append(result)
        return results

    def _context_for_task(self, task: TaskNode) -> list[DataPacket]:
        packets = self.bus.history()
        return [
            packet
            for packet in packets
            if packet.packet_type in {PacketType.CONTEXT, PacketType.ARTIFACT, PacketType.REVIEW, PacketType.STATUS}
            and (packet.correlation_id in task.depends_on or packet.topic == "graph")
        ]

    async def _artifact_validation_hook(self, packet: DataPacket) -> None:
        if packet.packet_type != PacketType.ARTIFACT:
            return
        artifact = ArtifactPayload.model_validate(packet.payload)
        if artifact.artifact_type != ArtifactType.CODE:
            self.accepted_artifacts.append(artifact)
            return
        if artifact.artifact_id in self._reviewed_artifacts:
            return
        self._reviewed_artifacts.add(artifact.artifact_id)
        review = await self.reviewer.review(artifact)
        self.reviews.append(review)
        if review.score >= self.review_threshold and review.approved:
            self.accepted_artifacts.append(artifact)
            return
        self.rejected_artifacts.append(
            {
                "artifact": artifact.model_dump(mode="json"),
                "review": review.model_dump(mode="json"),
                "reason": f"review score {review.score:.3f} below threshold {self.review_threshold:.3f}",
            }
        )
        await self._schedule_correction(artifact, review)

    async def _schedule_correction(self, artifact: ArtifactPayload, review: ReviewPayload) -> None:
        self._correction_cycles[artifact.artifact_id] += 1
        cycle = self._correction_cycles[artifact.artifact_id]
        if cycle > MAX_CORRECTION_CYCLES:
            return
        request = CorrectionRequestPayload(
            task_id=artifact.task_id,
            artifact_id=artifact.artifact_id,
            original_artifact=artifact,
            review=review,
            correction_cycle=cycle,
        )
        await self.bus.publish(
            DataPacket(
                packet_id=stable_id(artifact.artifact_id, "correction", cycle),
                trace_id=trace_id_var.get(),
                packet_type=PacketType.CORRECTION_REQUEST,
                sender="master",
                recipient=AgentRole.CORRECTION_ENGINEER.value,
                topic="correction.request",
                payload=request.model_dump(mode="json"),
                correlation_id=artifact.task_id,
            )
        )
        task = asyncio.create_task(self._run_correction(request), name=f"correction-{artifact.artifact_id}-{cycle}")
        self._correction_tasks.append(task)

    async def _run_correction(self, request: CorrectionRequestPayload) -> None:
        agent = CorrectionAgent(
            agent_id=f"{AgentRole.CORRECTION_ENGINEER.value}:{request.task_id}:{request.correction_cycle}",
            role=AgentRole.CORRECTION_ENGINEER,
            backend=self.backend,
            bus=self.bus,
        )
        result = await agent.correct(request)
        self.task_results.append(result)
        await agent._publish_result(result)


def install_event_loop_hooks() -> None:
    loop = asyncio.get_running_loop()
    loop.set_debug(False)
    previous_handler = loop.get_exception_handler()

    def handler(loop_obj: asyncio.AbstractEventLoop, context: dict[str, Any]) -> None:
        context = {
            **context,
            "trace_id": trace_id_var.get(),
            "agent_id": agent_id_var.get(),
        }
        if previous_handler:
            previous_handler(loop_obj, context)
        else:
            loop_obj.default_exception_handler(context)

    loop.set_exception_handler(handler)


def report_to_jsonable(report: OrchestrationReport) -> dict[str, Any]:
    return report.model_dump(mode="json")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Native asyncio Pydantic swarm orchestrator.")
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--mock", action="store_true")
    parser.add_argument("--endpoint", default=os.environ.get("SWARM_MODEL_ENDPOINT", DEFAULT_ENDPOINT))
    parser.add_argument("--model", default=os.environ.get("SWARM_MODEL_NAME", DEFAULT_MODEL))
    parser.add_argument("--api-key-env", default="SWARM_MODEL_API_KEY")
    parser.add_argument("--temperature", type=float, default=0.2)
    parser.add_argument("--max-tokens", type=int, default=4096)
    parser.add_argument("--max-concurrency", type=int, default=8)
    parser.add_argument("--out", type=Path)
    return parser.parse_args()


async def run_mock_workflow(prompt: str, max_concurrency: int) -> OrchestrationReport:
    backend = MockReasoningBackend()
    orchestrator = MasterOrchestrator(backend=backend, max_concurrency=max_concurrency)
    return await orchestrator.orchestrate(prompt)


async def async_main() -> None:
    args = parse_args()
    if args.mock:
        report = await run_mock_workflow(args.prompt, args.max_concurrency)
    else:
        backend = OpenAICompatibleBackend(
            endpoint=args.endpoint,
            model=args.model,
            api_key=os.environ.get(args.api_key_env) if args.api_key_env else None,
            temperature=args.temperature,
            max_tokens=args.max_tokens,
        )
        orchestrator = MasterOrchestrator(backend=backend, max_concurrency=args.max_concurrency)
        report = await orchestrator.orchestrate(args.prompt)
    rendered = json.dumps(report_to_jsonable(report), indent=2, ensure_ascii=False)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


def main() -> None:
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
