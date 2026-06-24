#!/usr/bin/env python
"""Agentic coding runtime v2.

This module keeps the agent architecture explicit: planning, editing, testing,
debugging, reviewing, self-healing, and final response generation are separate
blocks with strict schema output.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.phase11.memory_engine import WorkspaceMemoryEngine
from src.phase11.model_backends import ModelBackend
from src.phase11.schemas import (
    AgentRunReport,
    AgentRunRequest,
    AgentStep,
    ChatMessage,
    ChatRole,
    ContextPack,
    FilePatch,
    GenerationConfig,
    GenerationRequest,
    SecurityReview,
    ToolResult,
)
from src.phase11.security_engine import SecurityReasoningEngine
from src.phase11.tool_runtime import ToolRegistry, resolve_inside


def stable_id(*parts: object) -> str:
    return hashlib.sha256("\x1f".join(str(part) for part in parts).encode("utf-8")).hexdigest()[:24]


@dataclass(frozen=True)
class RuntimePlan:
    objective: str
    stages: list[str]
    priority_files: list[str]
    verification: list[str]
    task_graph: dict[str, Any] | None = None


class Planner:
    def create_plan(self, request: AgentRunRequest, context: ContextPack, security: SecurityReview) -> RuntimePlan:
        priority_files = [item.source for item in context.items[:8] if item.kind.startswith("source")]
        stages = [
            "expand_intent",
            "pack_long_context",
            "static_security_review",
            "generate_solution",
            "extract_and_review_patches",
            "sandbox_or_test_verification",
            "debug_or_self_heal_on_failure",
            "final_patch_and_answer",
        ]
        verification = ["static security comparison", "path safety validation"]
        if request.allow_sandbox:
            verification.append("Phase 2 Docker sandbox execution")
        if security.findings:
            verification.append("confirm no new vulnerability class is introduced")
        task_graph_payload: dict[str, Any] | None = None
        try:
            from src.phase16.task_graph import TaskGraphPlanner, TaskGraphStore

            graph = TaskGraphPlanner().plan(request.prompt, workspace_summary=context.expanded_intent)
            graph_path = TaskGraphStore().save(graph)
            task_graph_payload = {"graph": graph.model_dump(), "path": str(graph_path)}
        except Exception as exc:
            task_graph_payload = {"error": f"{type(exc).__name__}: {exc}"}
        return RuntimePlan(
            objective=context.expanded_intent,
            stages=stages,
            priority_files=priority_files,
            verification=verification,
            task_graph=task_graph_payload,
        )


class CodeEditor:
    def __init__(self, security: SecurityReasoningEngine) -> None:
        self.security = security

    def extract_patches(self, text: str) -> list[FilePatch]:
        marker = "<phase11_patches_json>"
        if marker not in text:
            return []
        start = text.find(marker) + len(marker)
        end = text.find("</phase11_patches_json>", start)
        if end < 0:
            return []
        try:
            payload = json.loads(text[start:end].strip())
        except json.JSONDecodeError:
            return []
        patches: list[FilePatch] = []
        for item in payload.get("patches", []):
            try:
                patch = FilePatch.model_validate(item)
            except Exception:
                patch = None
            if patch is not None:
                patches.append(patch)
        return patches

    def write_patches(self, workspace: Path, patches: list[FilePatch]) -> list[ToolResult]:
        results: list[ToolResult] = []
        for patch in patches:
            try:
                target = resolve_inside(workspace, patch.path)
                before = target.read_text(encoding="utf-8", errors="replace") if target.exists() else ""
                review = self.security.compare_patch_security(before, patch.content, target=patch.path)
                if review.score < 0.65:
                    results.append(
                        ToolResult(
                            tool="code_editor.write_patch",
                            ok=False,
                            summary=f"Rejected {patch.path}: {review.summary}",
                            data={"security_review": review.model_dump()},
                        )
                    )
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(patch.content, encoding="utf-8")
                results.append(
                    ToolResult(
                        tool="code_editor.write_patch",
                        ok=True,
                        summary=f"Wrote {patch.path}",
                        data={"patch": patch.model_dump(), "security_review": review.model_dump()},
                    )
                )
            except Exception as exc:
                results.append(ToolResult(tool="code_editor.write_patch", ok=False, summary=f"{type(exc).__name__}: {exc}"))
        return results


class TestRunner:
    def __init__(self, registry: ToolRegistry) -> None:
        self.registry = registry

    async def run(self, request: AgentRunRequest, patches: list[FilePatch]) -> ToolResult:
        if not request.allow_sandbox:
            return ToolResult(tool="test_runner", ok=True, summary="sandbox disabled by request")
        explicit_code = request.metadata.get("sandbox_python") if request.metadata else None
        if explicit_code:
            return await self.registry.call("sandbox_python", {"code": explicit_code})
        python_patch = next((patch for patch in patches if patch.path.endswith(".py")), None)
        if python_patch:
            return await self.registry.call("sandbox_python", {"code": python_patch.content})
        return await self.registry.call("sandbox_python", {"code": "print('phase11-agent-sandbox-ok')"})


class Debugger:
    def diagnose(self, result: ToolResult) -> str:
        if result.ok:
            return "No runtime failure was reported."
        stderr = (result.stderr or result.summary)[:3000]
        stdout = result.stdout[:1000]
        return (
            "Sandbox/test failure intercepted.\n"
            f"Summary: {result.summary}\n"
            f"STDERR:\n{stderr}\n"
            f"STDOUT:\n{stdout}\n"
            "Next action: inspect the failing command, missing dependency, path issue, or runtime exception and produce a smaller verified patch."
        )


class Reviewer:
    def review(self, security: SecurityReview, sandbox: ToolResult | None, patches: list[FilePatch]) -> ToolResult:
        sandbox_ok = True if sandbox is None else sandbox.ok
        patch_bonus = 0.05 if patches else 0.0
        score = min(1.0, security.score * 0.75 + (0.2 if sandbox_ok else 0.0) + patch_bonus)
        ok = score >= 0.85 and sandbox_ok
        return ToolResult(
            tool="adversarial_reviewer",
            ok=ok,
            summary=f"review_score={score:.2f}; security={security.score:.2f}; sandbox_ok={sandbox_ok}; patches={len(patches)}",
            data={"score": score, "security_score": security.score, "sandbox_ok": sandbox_ok, "patch_count": len(patches)},
        )


class SelfHealer:
    def __init__(self, backend: ModelBackend) -> None:
        self.backend = backend

    async def repair_hint(
        self,
        *,
        workspace: str,
        original_prompt: str,
        failed_output: str,
        diagnosis: str,
    ) -> str:
        generation = await self.backend.generate(
            GenerationRequest(
                workspace=workspace,
                config=GenerationConfig(max_new_tokens=900, temperature=0.15),
                messages=[
                    ChatMessage(
                        role=ChatRole.SYSTEM,
                        content=(
                            "You are a debugging repair agent. Use the runtime telemetry to produce "
                            "a corrected patch strategy. Do not claim the fix passed unless telemetry proves it."
                        ),
                    ),
                    ChatMessage(
                        role=ChatRole.USER,
                        content=(
                            f"Original intent:\n{original_prompt}\n\n"
                            f"Previous output:\n{failed_output[:4000]}\n\n"
                            f"Runtime exception trace:\n{diagnosis}\n\n"
                            "Return a concise corrected reasoning route and patch direction."
                        ),
                    ),
                ],
            )
        )
        return generation.text


class FinalPatchGenerator:
    def build(self, *, generation_text: str, reviewer: ToolResult, diagnosis: str | None = None) -> str:
        parts = [generation_text.strip(), "", f"Reviewer: {reviewer.summary}"]
        if diagnosis:
            parts.extend(["", "Runtime diagnosis:", diagnosis])
        return "\n".join(part for part in parts if part)


class AgenticCodingRuntime:
    def __init__(self, backend: ModelBackend, *, default_workspace: str | Path | None = None) -> None:
        self.backend = backend
        self.default_workspace = Path(default_workspace).resolve() if default_workspace else None
        self.security = SecurityReasoningEngine()
        self.planner = Planner()
        self.editor = CodeEditor(self.security)
        self.debugger = Debugger()
        self.reviewer = Reviewer()
        self.self_healer = SelfHealer(backend)
        self.finalizer = FinalPatchGenerator()

    async def run(self, request: AgentRunRequest) -> AgentRunReport:
        started = time.time_ns()
        run_id = stable_id(request.prompt, request.workspace, started)
        workspace = Path(request.workspace).resolve()
        memory = WorkspaceMemoryEngine(workspace)
        context = memory.retrieve(request.prompt, token_budget=request.context_token_budget)
        registry = ToolRegistry(workspace, security=self.security)
        test_runner = TestRunner(registry)
        steps: list[AgentStep] = []

        security_review = await asyncio.to_thread(self.security.analyze_workspace, workspace)
        plan = self.planner.create_plan(request, context, security_review)
        steps.append(
            AgentStep(
                step_id=stable_id(run_id, "planner"),
                role="planner",
                action="create_prioritized_task_graph",
                status="complete",
                summary=f"Planned {len(plan.stages)} stages over {len(plan.priority_files)} priority files.",
                tool_results=[
                    ToolResult(
                        tool="planner",
                        ok=True,
                        summary="runtime plan ready",
                        data={
                            "objective": plan.objective,
                            "stages": plan.stages,
                            "priority_files": plan.priority_files,
                            "verification": plan.verification,
                        },
                    )
                ],
            )
        )
        steps.append(
            AgentStep(
                step_id=stable_id(run_id, "security"),
                role="security_reviewer",
                action="static_workspace_review",
                status="complete",
                summary=security_review.summary,
                tool_results=[
                    ToolResult(
                        tool="security_engine",
                        ok=security_review.score >= 0.5,
                        summary=security_review.summary,
                        data={
                            "score": security_review.score,
                            "findings": [item.model_dump() for item in security_review.findings[:20]],
                        },
                    )
                ],
            )
        )

        generation = await self._generate_solution(request, workspace, context, security_review, plan)
        steps.append(
            AgentStep(
                step_id=stable_id(run_id, "reasoning"),
                role="code_architect",
                action="generate_solution",
                status="complete",
                summary="Generated architecture-aware solution.",
                tool_results=[
                    ToolResult(
                        tool="model_backend",
                        ok=True,
                        summary=f"{generation.backend}:{generation.model} completed in {generation.latency_ms:.1f} ms",
                        stdout=generation.text[:4000],
                        data=generation.metadata,
                    )
                ],
            )
        )

        patches = self.editor.extract_patches(generation.text)
        if patches and request.allow_writes:
            write_results = await asyncio.to_thread(self.editor.write_patches, workspace, patches)
            steps.append(
                AgentStep(
                    step_id=stable_id(run_id, "write"),
                    role="code_editor",
                    action="write_patches",
                    status="complete" if all(result.ok for result in write_results) else "failed",
                    summary=f"Applied {sum(1 for result in write_results if result.ok)}/{len(write_results)} proposed patches.",
                    tool_results=write_results,
                )
            )
        elif patches:
            steps.append(
                AgentStep(
                    step_id=stable_id(run_id, "dry-run"),
                    role="code_editor",
                    action="dry_run_patches",
                    status="complete",
                    summary=f"Generated {len(patches)} patch(es); writes disabled.",
                )
            )

        sandbox_result = await test_runner.run(request, patches)
        steps.append(
            AgentStep(
                step_id=stable_id(run_id, "test"),
                role="test_runner",
                action="sandbox_or_test_verification",
                status="complete" if sandbox_result.ok else "failed",
                summary=sandbox_result.summary,
                tool_results=[sandbox_result],
            )
        )

        diagnosis: str | None = None
        if not sandbox_result.ok:
            diagnosis = self.debugger.diagnose(sandbox_result)
            steps.append(
                AgentStep(
                    step_id=stable_id(run_id, "debugger"),
                    role="debugger",
                    action="runtime_failure_diagnosis",
                    status="complete",
                    summary="Captured sandbox/test failure telemetry.",
                    tool_results=[ToolResult(tool="debugger", ok=True, summary="diagnosis ready", stdout=diagnosis)],
                )
            )
            if request.max_iterations > 1:
                repair_text = await self.self_healer.repair_hint(
                    workspace=str(workspace),
                    original_prompt=request.prompt,
                    failed_output=generation.text,
                    diagnosis=diagnosis,
                )
                steps.append(
                    AgentStep(
                        step_id=stable_id(run_id, "self-heal"),
                        role="self_healer",
                        action="repair_cycle_hint",
                        status="complete",
                        summary="Generated corrected repair direction from telemetry.",
                        tool_results=[ToolResult(tool="self_healer", ok=True, summary="repair hint generated", stdout=repair_text[:4000])],
                    )
                )

        reviewer_result = self.reviewer.review(security_review, sandbox_result, patches)
        steps.append(
            AgentStep(
                step_id=stable_id(run_id, "review"),
                role="security_reviewer",
                action="adversarial_peer_review",
                status="complete" if reviewer_result.ok else "needs_iteration",
                summary=reviewer_result.summary,
                tool_results=[reviewer_result],
            )
        )

        confidence = float(reviewer_result.data.get("score", 0.55))
        final_answer = self.finalizer.build(generation_text=generation.text, reviewer=reviewer_result, diagnosis=diagnosis)
        status = "complete" if reviewer_result.ok or not request.allow_sandbox else "needs_attention"
        return AgentRunReport(
            run_id=run_id,
            prompt=request.prompt,
            expanded_intent=context.expanded_intent,
            status=status,
            summary="Agentic coding runtime completed plan, memory, security, generation, sandbox, review, and repair routing.",
            confidence=max(0.0, min(1.0, confidence)),
            context=context,
            steps=steps,
            proposed_patches=patches,
            final_answer=final_answer,
        )

    async def _generate_solution(
        self,
        request: AgentRunRequest,
        workspace: Path,
        context: ContextPack,
        security_review: SecurityReview,
        plan: RuntimePlan,
    ):
        system = ChatMessage(
            role=ChatRole.SYSTEM,
            content=(
                "You are a senior agentic coding system. Expand short prompts, inspect context, "
                "design minimal correct changes, consider security, and state verification steps. "
                "When you propose direct file replacements, wrap JSON in <phase11_patches_json> tags "
                "with shape {\"patches\":[{\"path\":\"...\",\"content\":\"...\",\"rationale\":\"...\"}]}."
            ),
        )
        context_text = self._render_context(context)
        user = ChatMessage(
            role=ChatRole.USER,
            content=(
                f"Expanded intent:\n{context.expanded_intent}\n\n"
                f"Runtime plan:\n{json.dumps(plan.__dict__, indent=2)}\n\n"
                f"Workspace context:\n{context_text}\n\n"
                f"Security review:\n{security_review.summary}\n\n"
                "Produce an implementation plan, likely files, verification checklist, and proposed complete-file patches only when confident."
            ),
        )
        return await self.backend.generate(
            GenerationRequest(
                messages=[system, user],
                config=GenerationConfig(max_new_tokens=1200, temperature=0.2),
                workspace=str(workspace),
                metadata={"run_id": stable_id(request.prompt, workspace, "generation")},
            )
        )

    def _render_context(self, context: ContextPack) -> str:
        parts = []
        for item in context.items[:12]:
            parts.append(f"### {item.source} score={item.score}\n{item.content[:5000]}")
        return "\n\n".join(parts) if parts else "No matching files found; rely on prompt and workspace summary."
