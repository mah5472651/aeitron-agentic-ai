#!/usr/bin/env python
"""Role-specific async micro-agents for the durable task graph."""

from __future__ import annotations

import asyncio
import hashlib
import os
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from src.phase11.model_backends import ModelBackend
from src.phase11.schemas import ChatMessage, ChatRole, GenerationConfig, GenerationRequest
from src.phase16.task_graph import TaskGraph, TaskNode, TaskStatus


def artifact_id(*parts: object) -> str:
    raw = "\x1f".join(str(part) for part in parts).encode("utf-8", errors="replace")
    return f"art-{hashlib.sha256(raw).hexdigest()[:20]}"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class AgentRole(str, Enum):
    ARCHITECT = "architect"
    CODER = "coder"
    TESTER = "tester"
    DEBUGGER = "debugger"
    SECURITY_AUDITOR = "security_auditor"
    REVIEWER = "reviewer"
    RESEARCHER = "researcher"


class AgentArtifact(StrictModel):
    artifact_id: str
    task_id: str
    role: AgentRole
    content: str
    confidence: float = Field(ge=0.0, le=1.0)
    created_at_ms: int
    metadata: dict[str, Any] = Field(default_factory=dict)


@dataclass
class AgentContext:
    objective: str
    workspace_summary: str = ""
    shared_artifacts: list[AgentArtifact] = field(default_factory=list)

    def render(self, limit: int = 8) -> str:
        if not self.shared_artifacts:
            return "No prior artifacts yet."
        parts = []
        for artifact in self.shared_artifacts[-limit:]:
            parts.append(
                f"### {artifact.role.value}:{artifact.task_id} confidence={artifact.confidence:.2f}\n"
                f"{artifact.content[:1800]}"
            )
        return "\n\n".join(parts)


ROLE_SYSTEM_PROMPTS = {
    AgentRole.ARCHITECT: "You are a principal software architect. Produce concise plans, dependencies, risks, and verification steps.",
    AgentRole.CODER: "You are a senior coding agent. Produce implementation-ready patch strategy and exact files to change.",
    AgentRole.TESTER: "You are a test engineer. Define deterministic tests, sandbox checks, and pass/fail criteria.",
    AgentRole.DEBUGGER: "You are a debugger. Use telemetry, isolate root cause, and propose the smallest fix.",
    AgentRole.SECURITY_AUDITOR: "You are a defensive security auditor. Find CWE-class risks and safe patch guidance only.",
    AgentRole.REVIEWER: "You are an adversarial reviewer. Score correctness, security, maintainability, and missing tests.",
    AgentRole.RESEARCHER: "You are a research agent. Summarize relevant APIs/docs from provided context without hallucinated claims.",
}


class RoleAgent:
    def __init__(self, role: AgentRole, backend: ModelBackend) -> None:
        self.role = role
        self.backend = backend

    async def run(self, task: TaskNode, context: AgentContext) -> AgentArtifact:
        prompt = (
            f"Objective:\n{context.objective}\n\n"
            f"Workspace summary:\n{context.workspace_summary or 'No workspace summary supplied.'}\n\n"
            f"Current task:\n{task.title}\n{task.description}\n\n"
            f"Prior artifacts:\n{context.render()}\n\n"
            "Return structured, concrete output with: Findings, Actions, Risks, Verification."
        )
        try:
            response = await self.backend.generate(
                GenerationRequest(
                    messages=[
                        ChatMessage(role=ChatRole.SYSTEM, content=ROLE_SYSTEM_PROMPTS[self.role]),
                        ChatMessage(role=ChatRole.USER, content=prompt),
                    ],
                    config=GenerationConfig(max_new_tokens=int(os.environ.get("PHASE16_AGENT_MAX_NEW_TOKENS", "360")), temperature=0.2),
                    metadata={"task_id": task.task_id, "role": self.role.value},
                )
            )
            content = response.text.strip()
            confidence = self._confidence(content)
            metadata = {
                "backend": response.backend,
                "model": response.model,
                "latency_ms": response.latency_ms,
                "task_title": task.title,
            }
        except Exception as exc:
            content = (
                "Findings: role-agent backend call failed.\n"
                f"Actions: retry with a healthier model endpoint or use local mock orchestration for plumbing tests.\n"
                f"Risks: {type(exc).__name__}: {exc}\n"
                "Verification: rerun Phase 40 after backend recovery."
            )
            confidence = 0.2
            metadata = {"backend_error": f"{type(exc).__name__}: {exc}", "task_title": task.title}
        return AgentArtifact(
            artifact_id=artifact_id(task.task_id, self.role.value, content[:500], time.time_ns()),
            task_id=task.task_id,
            role=self.role,
            content=content,
            confidence=confidence,
            created_at_ms=int(time.time() * 1000),
            metadata=metadata,
        )

    def _confidence(self, text: str) -> float:
        lower = text.lower()
        score = 0.45
        score += 0.12 if "verification" in lower or "test" in lower else 0.0
        score += 0.10 if "risk" in lower or "security" in lower else 0.0
        score += 0.10 if "file" in lower or "patch" in lower or "action" in lower else 0.0
        score += 0.10 if len(text) >= 250 else min(0.10, len(text) / 2500)
        score += 0.08 if any(marker in lower for marker in ("plan", "findings", "actions")) else 0.0
        return max(0.0, min(1.0, score))


class RoleAgentOrchestrator:
    def __init__(self, backend: ModelBackend, *, max_parallel: int = 4) -> None:
        self.backend = backend
        self.max_parallel = max(1, max_parallel)

    def build_agent(self, role: str) -> RoleAgent:
        try:
            parsed = AgentRole(role)
        except ValueError:
            parsed = AgentRole.REVIEWER
        return RoleAgent(parsed, self.backend)

    async def execute(self, graph: TaskGraph, *, workspace_summary: str = "") -> list[AgentArtifact]:
        graph.validate_acyclic()
        context = AgentContext(objective=graph.objective, workspace_summary=workspace_summary)
        semaphore = asyncio.Semaphore(self.max_parallel)
        artifacts: list[AgentArtifact] = []

        async def run_node(node: TaskNode) -> AgentArtifact:
            async with semaphore:
                graph.mark_running(node.task_id)
                agent = self.build_agent(node.role)
                return await agent.run(node, context)

        for layer in graph.topological_layers():
            runnable = [node for node in layer if node.status in {TaskStatus.PENDING, TaskStatus.READY}]
            layer_artifacts = await asyncio.gather(*(run_node(node) for node in runnable))
            for node, artifact in zip(runnable, layer_artifacts):
                graph.mark_complete(node.task_id, [artifact.artifact_id])
                context.shared_artifacts.append(artifact)
                artifacts.append(artifact)
        return artifacts
