#!/usr/bin/env python
"""Strict schemas shared by the Phase 11 AI core."""

from __future__ import annotations

import time
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


def now_ms() -> int:
    return int(time.time() * 1000)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class ChatRole(str, Enum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class BackendKind(str, Enum):
    MOCK = "mock"
    PYTORCH = "pytorch"
    OPENAI_COMPATIBLE = "openai_compatible"


class ChatMessage(StrictModel):
    role: ChatRole
    content: str = Field(min_length=1)
    created_at_ms: int = Field(default_factory=now_ms)
    metadata: dict[str, Any] = Field(default_factory=dict)


class GenerationConfig(StrictModel):
    max_new_tokens: int = Field(default=768, ge=1, le=8192)
    temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    top_p: float = Field(default=0.95, ge=0.0, le=1.0)
    stop: list[str] = Field(default_factory=list)
    stream: bool = False


class GenerationRequest(StrictModel):
    messages: list[ChatMessage]
    config: GenerationConfig = Field(default_factory=GenerationConfig)
    session_id: str | None = None
    workspace: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("messages")
    @classmethod
    def require_user_message(cls, messages: list[ChatMessage]) -> list[ChatMessage]:
        if not any(message.role == ChatRole.USER for message in messages):
            raise ValueError("at least one user message is required")
        return messages


class GenerationResponse(StrictModel):
    text: str
    backend: str
    model: str
    latency_ms: float
    prompt_tokens_estimate: int
    completion_tokens_estimate: int
    metadata: dict[str, Any] = Field(default_factory=dict)


class ContextItem(StrictModel):
    source: str
    title: str
    content: str
    score: float = Field(ge=0.0)
    kind: str = "text"
    metadata: dict[str, Any] = Field(default_factory=dict)


class ContextPack(StrictModel):
    query: str
    expanded_intent: str
    items: list[ContextItem]
    token_budget: int
    estimated_tokens: int
    workspace_root: str | None = None


class FilePatch(StrictModel):
    path: str = Field(min_length=1)
    content: str
    rationale: str = ""

    @field_validator("path")
    @classmethod
    def reject_unsafe_path(cls, value: str) -> str:
        normalized = value.replace("\\", "/")
        if normalized.startswith("/") or "/../" in f"/{normalized}/" or normalized in {".", ".."}:
            raise ValueError(f"unsafe patch path: {value}")
        return normalized


class ToolResult(StrictModel):
    tool: str
    ok: bool
    summary: str
    stdout: str = ""
    stderr: str = ""
    data: dict[str, Any] = Field(default_factory=dict)


class AgentStep(StrictModel):
    step_id: str
    role: str
    action: str
    status: str
    summary: str
    created_at_ms: int = Field(default_factory=now_ms)
    tool_results: list[ToolResult] = Field(default_factory=list)


class AgentRunRequest(StrictModel):
    prompt: str = Field(min_length=1)
    workspace: str
    allow_writes: bool = False
    allow_sandbox: bool = True
    max_iterations: int = Field(default=5, ge=1, le=20)
    context_token_budget: int = Field(default=12000, ge=512, le=200000)
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentRunReport(StrictModel):
    run_id: str
    prompt: str
    expanded_intent: str
    status: str
    summary: str
    confidence: float = Field(ge=0.0, le=1.0)
    context: ContextPack
    steps: list[AgentStep]
    proposed_patches: list[FilePatch] = Field(default_factory=list)
    final_answer: str
    created_at_ms: int = Field(default_factory=now_ms)


class SecurityFinding(StrictModel):
    finding_id: str
    title: str
    severity: str
    cwe: str | None = None
    file_path: str | None = None
    line: int | None = None
    evidence: str
    recommendation: str
    confidence: float = Field(ge=0.0, le=1.0)


class SecurityReview(StrictModel):
    target: str
    findings: list[SecurityFinding]
    score: float = Field(ge=0.0, le=1.0)
    summary: str

