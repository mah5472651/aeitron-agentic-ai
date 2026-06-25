#!/usr/bin/env python
"""Phase 40 integrated default agent execution path.

This is the "real" default architecture path:

vector memory -> MainAgentV2/TaskGraph -> critic -> verifier policy ->
multi-language security -> failure promotion.
"""

from __future__ import annotations

import argparse
import asyncio
import inspect
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Awaitable, Callable

from pydantic import BaseModel, ConfigDict, Field

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.phase11.memory_engine import safe_workspace
from src.phase11.model_backends import ModelBackend, build_backend
from src.phase16.experience_memory import ExperienceMemoryStore, ExperienceRecord, record_id
from src.phase22.critic_service import review_artifact
from src.phase24.main_agent_v2 import MainAgentV2, MainAgentV2Request, write_report as write_phase24_report
from src.phase27.verifier_policy_engine import load_profile, run_policy, write_default_policy
from src.phase37.vector_memory import VectorExperienceMemory, write_report as write_phase37_report
from src.phase38.multilang_security import MultiLanguageSecurityEngine, write_report as write_phase38_report
from src.phase43.meta_planner import create_meta_plan, write_report as write_phase43_report
from src.phase46.hierarchical_memory import HierarchicalMemory, write_report as write_phase46_report
from src.phase47.reasoning_engine import ReasoningEngine, write_report as write_phase47_report
from src.phase50.moe_router import MoERouter, write_report as write_phase50_report
from src.phase51.high_stability_reasoning_memory import (
    MemoryLayer as StrictMemoryLayer,
    MemoryKind as StrictMemoryKind,
    ReasoningEngine as StrictReasoningEngine,
    UnifiedMemoryManager,
)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class IntentRoute(StrictModel):
    intent: str
    verifier_profile: str
    run_multilang_security: bool
    run_semgrep: bool
    run_sandbox: bool
    use_model_critic: bool
    reason: str


class IntegratedAgentRequest(StrictModel):
    prompt: str = Field(min_length=1)
    workspace: str = str(ROOT)
    meta_planning: bool = True
    hierarchical_memory: bool = True
    reasoning_review: bool = True
    strict_stability: bool = True
    policy_mode: str = Field(default="strict", pattern="^(strict|development)$")
    moe_routing: bool = True
    vector_memory: bool = True
    rebuild_vector_memory: bool = False
    run_verifier: bool = True
    run_security: bool = True
    verifier_profile: str | None = None
    use_model_critic: bool = False
    agent_backend_mode: str = Field(default="auto", pattern="^(auto|active|mock)$")
    max_agent_nodes: int | None = Field(default=None, ge=1, le=12)
    max_security_files: int = Field(default=500, ge=1, le=20000)
    metadata: dict[str, Any] = Field(default_factory=dict)


class IntegratedAgentReport(StrictModel):
    run_id: str
    status: str
    summary: str
    confidence: float = Field(ge=0.0, le=1.0)
    prompt: str
    workspace: str
    route: dict[str, Any]
    enforcement: dict[str, Any]
    moe_route: dict[str, Any] | None
    meta_plan: dict[str, Any] | None
    hierarchical_memory: dict[str, Any] | None
    vector_memory: dict[str, Any] | None
    agent: dict[str, Any]
    critic: dict[str, Any]
    reasoning: dict[str, Any] | None
    strict_stability: dict[str, Any] | None
    verifier: dict[str, Any] | None
    multilang_security: dict[str, Any] | None
    failure_event: dict[str, Any] | None
    final_answer: str
    duration_ms: float
    created_at_unix: float


def build_backend_from_env() -> ModelBackend:
    return build_backend(
        os.environ.get("PHASE40_BACKEND", os.environ.get("PHASE24_BACKEND", os.environ.get("PHASE11_BACKEND", "mock"))),
        endpoint=os.environ.get(
            "PHASE40_MODEL_ENDPOINT",
            os.environ.get("PHASE24_MODEL_ENDPOINT", os.environ.get("PHASE11_MODEL_ENDPOINT", "http://127.0.0.1:8016/v1")),
        ),
        model_name=os.environ.get(
            "PHASE40_MODEL_NAME",
            os.environ.get("PHASE24_MODEL_NAME", os.environ.get("PHASE11_MODEL_NAME", "Qwen/Qwen2.5-Coder-0.5B-Instruct")),
        ),
        api_key=os.environ.get("PHASE40_API_KEY", os.environ.get("PHASE24_API_KEY", os.environ.get("PHASE11_API_KEY"))),
        checkpoint=os.environ.get("PHASE40_CHECKPOINT", os.environ.get("PHASE11_CHECKPOINT")),
        tokenizer_path=os.environ.get("PHASE40_TOKENIZER", os.environ.get("PHASE11_TOKENIZER")),
        device=os.environ.get("PHASE40_DEVICE", os.environ.get("PHASE11_DEVICE", "cpu")),
    )


def classify_intent(prompt: str, *, requested_profile: str | None, use_model_critic: bool) -> IntentRoute:
    lower = prompt.lower()
    security_markers = ["security", "vulnerability", "cve", "cwe", "exploit", "xss", "sql injection", "auth", "crypto", "solidity"]
    debug_markers = ["debug", "fix", "error", "traceback", "failing", "bug", "crash"]
    code_markers = ["build", "implement", "code", "api", "backend", "frontend", "test", "repo"]
    release_markers = ["deploy", "production", "release", "ship", "kubernetes"]
    if any(marker in lower for marker in security_markers):
        intent = "security"
        profile = requested_profile or "security"
        reason = "security markers detected"
    elif any(marker in lower for marker in release_markers):
        intent = "release"
        profile = requested_profile or "release"
        reason = "release/deployment markers detected"
    elif any(marker in lower for marker in debug_markers):
        intent = "debugging"
        profile = requested_profile or "fast"
        reason = "debugging markers detected"
    elif any(marker in lower for marker in code_markers):
        intent = "coding"
        profile = requested_profile or "fast"
        reason = "coding markers detected"
    else:
        intent = "general"
        profile = requested_profile or "fast"
        reason = "default general route"
    return IntentRoute(
        intent=intent,
        verifier_profile=profile,
        run_multilang_security=intent in {"security", "coding", "debugging", "release"},
        run_semgrep=profile in {"security", "release"},
        run_sandbox=profile == "release",
        use_model_critic=use_model_critic,
        reason=reason,
    )


class IntegratedAgentRuntime:
    def __init__(self, backend: ModelBackend) -> None:
        self.backend = backend

    async def run(
        self,
        request: IntegratedAgentRequest,
        *,
        event_sink: Callable[[str, dict[str, Any]], Awaitable[None] | None] | None = None,
    ) -> IntegratedAgentReport:
        started = time.time()
        run_id = f"phase40-{time.time_ns()}"
        workspace = safe_workspace(request.workspace)
        route = classify_intent(request.prompt, requested_profile=request.verifier_profile, use_model_critic=request.use_model_critic)
        serious_intent = route.intent in {"coding", "debugging", "security", "release"}
        strict_required = request.policy_mode == "strict" and serious_intent
        strict_stability_enabled = request.strict_stability or strict_required
        verifier_enabled = request.run_verifier or strict_required
        security_enabled = request.run_security or (strict_required and route.run_multilang_security)
        enforcement = {
            "policy_mode": request.policy_mode,
            "serious_intent": serious_intent,
            "strict_stability_required": strict_required,
            "strict_stability_enabled": strict_stability_enabled,
            "verifier_enabled": verifier_enabled,
            "security_enabled": security_enabled,
        }
        await self._emit(
            event_sink,
            "route",
            {"status": "complete", "intent": route.intent, "verifier_profile": route.verifier_profile, "enforcement": enforcement},
        )
        moe_payload: dict[str, Any] | None = None
        meta_plan_payload: dict[str, Any] | None = None
        hierarchical_payload: dict[str, Any] | None = None
        vector_payload: dict[str, Any] | None = None
        reasoning_payload: dict[str, Any] | None = None
        strict_stability_payload: dict[str, Any] | None = None
        enriched_prompt = request.prompt

        if request.moe_routing:
            moe_report = MoERouter().route(request.prompt, run_id=f"{run_id}-moe", top_k=4)
            write_phase50_report(moe_report, ROOT / "artifacts" / "phase50")
            moe_payload = moe_report.model_dump()
            enriched_prompt = (
                f"{enriched_prompt}\n\n"
                "Mixture-of-experts route:\n"
                f"- primary={moe_report.primary_expert}\n"
                f"- execution_hint={moe_report.execution_hint}\n"
                + "\n".join(f"- {expert.expert}: {expert.reason} score={expert.score}" for expert in moe_report.routes)
            )
            await self._emit(event_sink, "expert_routing", {"status": "complete", "primary_expert": moe_report.primary_expert, "routes": [item.expert for item in moe_report.routes]})

        if request.meta_planning:
            meta_plan = create_meta_plan(request.prompt, run_id=f"{run_id}-metaplan")
            write_phase43_report(meta_plan, ROOT / "artifacts" / "phase43")
            meta_plan_payload = meta_plan.model_dump()
            enriched_prompt = (
                f"{request.prompt}\n\n"
                "Meta-planner brief:\n"
                f"{meta_plan.taskgraph_brief}\n\n"
                "Architecture components:\n"
                + "\n".join(
                    f"- {component.name}: {component.responsibility}"
                    for component in meta_plan.architecture
                )
                + "\n\n"
                "Execution lanes:\n"
                + "\n".join(
                    f"- {lane.lane} ({lane.owner_role}): {', '.join(lane.tasks)}"
                    for lane in meta_plan.execution_lanes
                )
            )
            await self._emit(event_sink, "planning", {"status": "complete", "goal": meta_plan.goal, "lanes": [lane.lane for lane in meta_plan.execution_lanes]})

        if request.hierarchical_memory:
            memory_report = HierarchicalMemory(ROOT / "artifacts" / "phase46").run(
                enriched_prompt,
                run_id=f"{run_id}-hierarchical-memory",
                seed=True,
                limit=8,
            )
            write_phase46_report(memory_report, ROOT / "artifacts" / "phase46")
            hierarchical_payload = memory_report.model_dump()
            if memory_report.hits:
                enriched_prompt = (
                    f"{enriched_prompt}\n\n"
                    "Hierarchical memory context:\n"
                    f"{memory_report.context_block}\n\n"
                    "Prefer decisions that align with durable project memory and known failure outcomes."
                )
            await self._emit(event_sink, "hierarchical_memory", {"status": "complete", "hits": len(memory_report.hits), "layers": memory_report.layers})

        if strict_stability_enabled:
            strict_memory = UnifiedMemoryManager(
                ROOT / "artifacts" / "phase51" / "memory",
                session_id=run_id,
                project_id=workspace.name,
            )
            strict_memory.set_working_memory(project=workspace.name, current_feature=request.prompt[:160])
            strict_memory.save_project_memory(
                module_name="Phase 40 Integrated Agent",
                path="src/phase40/integrated_agent.py",
                tech_stack="Python + Pydantic + FastAPI + TaskGraph",
            )
            if meta_plan_payload:
                strict_memory.save(
                    layer=StrictMemoryLayer.PROJECT,
                    kind=StrictMemoryKind.SUCCESSFUL_PLAN,
                    payload={
                        "goal": meta_plan_payload.get("goal"),
                        "requirements": meta_plan_payload.get("requirements", []),
                        "success_criteria": meta_plan_payload.get("expansion", {}).get("acceptance_tests", []),
                    },
                    text=str(meta_plan_payload.get("taskgraph_brief") or request.prompt),
                    relevance=float(meta_plan_payload.get("confidence") or 0.75),
                    success_rate=1.0,
                )
            strict_memory.add_knowledge_relation("Strict Reasoning", "guards", "Phase 40 Integrated Agent", weight=0.88)
            retrieval = strict_memory.retrieve(enriched_prompt, limit=5)
            strict_stability_payload = {
                "memory_retrieval": retrieval.model_dump(),
                "anti_pollution_policy": "verified_fix|passed_benchmark|security_finding|successful_plan only",
            }
            if retrieval.hits:
                enriched_prompt = (
                    f"{enriched_prompt}\n\n"
                    "Strict stability memory context (Phase 51):\n"
                    + "\n".join(
                        f"- score={hit.final_score:.3f} layer={hit.entry.layer.value} kind={hit.entry.kind.value}: "
                        f"{' '.join(hit.entry.text.split())[:360]}"
                        for hit in retrieval.hits
                    )
                    + "\n\n"
                    f"Ranking formula: {retrieval.formula}"
                )
            await self._emit(event_sink, "strict_memory", {"status": "complete", "hits": len(retrieval.hits), "project_id": strict_memory.project_id, "embedding_backend": strict_memory.embedding_backend})

        if request.vector_memory:
            vector_memory = VectorExperienceMemory(workspace="mythos")
            vector_report = await vector_memory.run(
                enriched_prompt,
                run_id=f"{run_id}-vector",
                limit=8,
                rebuild=request.rebuild_vector_memory,
            )
            write_phase37_report(vector_report, ROOT / "artifacts" / "phase37")
            vector_payload = vector_report.model_dump()
            if vector_report.hits:
                enriched_prompt = (
                    f"{enriched_prompt}\n\n"
                    "Vector-ranked past failure/fix/outcome memory:\n"
                    f"{vector_report.context_block}\n\n"
                    "Use this memory to avoid repeated planning, security, and verification mistakes."
                )
            await self._emit(event_sink, "vector_memory", {"status": "complete", "hits": len(vector_report.hits), "backend": vector_report.embedding_backend})

        await self._emit(event_sink, "agent_execution", {"status": "running"})
        agent_backend = self.agent_backend_for_request(request)
        agent_report = await MainAgentV2(agent_backend).run(
            MainAgentV2Request(
                prompt=enriched_prompt,
                workspace=str(workspace),
                run_verifier=False,
                run_semgrep=route.run_semgrep,
                run_sandbox=route.run_sandbox,
                retrieve_experience=True,
                use_model_critic=route.use_model_critic,
                max_agent_nodes=request.max_agent_nodes or self.default_agent_node_cap(),
            )
        )
        write_phase24_report(agent_report, ROOT / "artifacts" / "phase24")
        taskgraph_artifacts = agent_report.taskgraph_report.get("artifacts") if isinstance(agent_report.taskgraph_report, dict) else []
        await self._emit(
            event_sink,
            "agent_execution",
            {
                "status": "complete",
                "agent_status": agent_report.status,
                "artifacts": len(taskgraph_artifacts) if isinstance(taskgraph_artifacts, list) else 0,
            },
        )

        await self._emit(event_sink, "critic", {"status": "running"})
        critic = await review_artifact(
            prompt=request.prompt,
            artifact=agent_report.final_answer,
            context="\n\n".join(
                part
                for part in [
                    (hierarchical_payload or {}).get("context_block", ""),
                    (vector_payload or {}).get("context_block", ""),
                    json.dumps(moe_payload or {}, ensure_ascii=False)[:2000],
                ]
                if part
            ),
            mode="model" if route.use_model_critic else "heuristic",
            backend=self.backend if route.use_model_critic else None,
        )
        await self._emit(event_sink, "critic", {"status": "complete", "accepted": critic.ok, "confidence": critic.confidence, "issues": len(critic.issues)})

        if request.reasoning_review:
            reasoning_report = ReasoningEngine().run(
                f"{request.prompt}\n\nCandidate answer:\n{agent_report.final_answer[:3000]}",
                run_id=f"{run_id}-reasoning",
            )
            write_phase47_report(reasoning_report, ROOT / "artifacts" / "phase47")
            reasoning_payload = reasoning_report.model_dump()
            await self._emit(event_sink, "reasoning_review", {"status": "complete", "accepted": reasoning_report.accepted, "confidence": reasoning_report.confidence})

        if strict_stability_enabled:
            strict_trace = StrictReasoningEngine().run(
                f"{request.prompt}\n\nCandidate answer:\n{agent_report.final_answer[:3000]}",
                run_id=f"{run_id}-strict-stability",
            )
            strict_stability_payload = {
                **(strict_stability_payload or {}),
                "reasoning_trace": strict_trace.model_dump(),
                "role_contracts": {
                    "planner": "task graph only; no executable code",
                    "executor": "follows graph only; no plan edits",
                    "critic": "flaws and confidence only; no solution",
                    "verifier": "schema and criteria checks only",
                },
            }
            self._write_strict_stability_report(run_id, strict_stability_payload)
            await self._emit(event_sink, "strict_stability", {"status": "complete", "accepted": strict_trace.accepted, "confidence": strict_trace.confidence, "reflections": len(strict_trace.reflections)})

        verifier_payload: dict[str, Any] | None = None
        if verifier_enabled:
            await self._emit(event_sink, "verifier", {"status": "running", "profile": route.verifier_profile})
            policy_file = ROOT / "config" / "verifier_policy.json"
            if not policy_file.exists():
                write_default_policy(policy_file)
            profile = load_profile(policy_file, route.verifier_profile)
            verifier_report = await run_policy(
                workspace=str(workspace),
                profile=profile,
                run_id=f"{run_id}-verifier",
                output_dir=ROOT / "artifacts" / "phase27",
            )
            verifier_payload = verifier_report.model_dump()
            await self._emit(event_sink, "verifier", {"status": "complete", "result": verifier_report.status, "score": verifier_report.score, "findings": len(verifier_report.findings)})

        security_payload: dict[str, Any] | None = None
        if security_enabled and route.run_multilang_security:
            await self._emit(event_sink, "security", {"status": "running"})
            security_report = await asyncio.to_thread(
                MultiLanguageSecurityEngine().analyze_workspace,
                workspace,
                max_files=request.max_security_files,
                include_fixtures=False,
            )
            security_report = security_report.model_copy(update={"run_id": f"{run_id}-security"})
            write_phase38_report(security_report, ROOT / "artifacts" / "phase38")
            security_payload = security_report.model_dump()
            await self._emit(event_sink, "security", {"status": "complete", "result": security_report.status, "score": security_report.score, "findings": len(security_report.findings)})

        status, failure_event = self._status_and_failure(
            run_id=run_id,
            request=request,
            route=route,
            agent=agent_report.model_dump(),
            critic=critic.model_dump(),
            reasoning=reasoning_payload,
            strict_stability=strict_stability_payload,
            verifier=verifier_payload,
            security=security_payload,
        )
        if failure_event:
            self._write_failure_event(failure_event)
            self._promote_failure(failure_event)

        await self._emit(event_sink, "complete", {"status": status, "run_id": run_id})

        return IntegratedAgentReport(
            run_id=run_id,
            status=status,
            summary=self._summary(status, route, critic.model_dump(), reasoning_payload, strict_stability_payload, verifier_payload, security_payload),
            confidence=self._confidence(critic.model_dump(), reasoning_payload, strict_stability_payload, verifier_payload, security_payload),
            prompt=request.prompt,
            workspace=str(workspace),
            route=route.model_dump(),
            enforcement=enforcement,
            moe_route=moe_payload,
            meta_plan=meta_plan_payload,
            hierarchical_memory=hierarchical_payload,
            vector_memory=vector_payload,
            agent=agent_report.model_dump(),
            critic=critic.model_dump(),
            reasoning=reasoning_payload,
            strict_stability=strict_stability_payload,
            verifier=verifier_payload,
            multilang_security=security_payload,
            failure_event=failure_event,
            final_answer=agent_report.final_answer,
            duration_ms=(time.time() - started) * 1000,
            created_at_unix=started,
        )

    async def _emit(
        self,
        event_sink: Callable[[str, dict[str, Any]], Awaitable[None] | None] | None,
        stage: str,
        payload: dict[str, Any],
    ) -> None:
        if event_sink is None:
            return
        result = event_sink(stage, {"stage": stage, "timestamp_unix": time.time(), **payload})
        if inspect.isawaitable(result):
            await result

    def _status_and_failure(
        self,
        *,
        run_id: str,
        request: IntegratedAgentRequest,
        route: IntentRoute,
        agent: dict[str, Any],
        critic: dict[str, Any],
        reasoning: dict[str, Any] | None,
        strict_stability: dict[str, Any] | None,
        verifier: dict[str, Any] | None,
        security: dict[str, Any] | None,
    ) -> tuple[str, dict[str, Any] | None]:
        reasons: list[str] = []
        if agent.get("status") not in {"complete", "ok"}:
            reasons.append(f"agent_status={agent.get('status')}")
        if not critic.get("ok", False):
            reasons.append(f"critic_confidence={critic.get('confidence')}")
        if reasoning and not reasoning.get("accepted", False):
            reasons.append(f"reasoning_confidence={reasoning.get('confidence')}")
        strict_trace = strict_stability.get("reasoning_trace") if strict_stability else None
        if isinstance(strict_trace, dict) and not strict_trace.get("accepted", False):
            reasons.append(f"strict_stability_confidence={strict_trace.get('confidence')}")
        if verifier and verifier.get("status") == "fail":
            reasons.append("verifier_failed")
        if security and security.get("status") == "needs_patch":
            reasons.append("multilang_security_findings")
        if not reasons:
            return "complete", None
        event = {
            "schema": "phase40.failure_event.v1",
            "run_id": run_id,
            "prompt": request.prompt,
            "workspace": request.workspace,
            "route": route.model_dump(),
            "reasons": reasons,
            "recommendation": "Inspect Phase 40 report, add a regression task, and route confirmed cases through Phase 36/29 before training.",
            "created_at_unix": time.time(),
        }
        return "needs_attention", event

    def _confidence(
        self,
        critic: dict[str, Any],
        reasoning: dict[str, Any] | None,
        strict_stability: dict[str, Any] | None,
        verifier: dict[str, Any] | None,
        security: dict[str, Any] | None,
    ) -> float:
        values = [float(critic.get("confidence") or 0.0)]
        if reasoning:
            values.append(float(reasoning.get("confidence") or 0.0))
        strict_trace = strict_stability.get("reasoning_trace") if strict_stability else None
        if isinstance(strict_trace, dict):
            values.append(float(strict_trace.get("confidence") or 0.0))
        if verifier:
            values.append(float(verifier.get("score") or 0.0) / 100.0)
        if security:
            values.append(float(security.get("score") or 0.0))
        return max(0.0, min(1.0, min(values) if values else 0.0))

    def _summary(
        self,
        status: str,
        route: IntentRoute,
        critic: dict[str, Any],
        reasoning: dict[str, Any] | None,
        strict_stability: dict[str, Any] | None,
        verifier: dict[str, Any] | None,
        security: dict[str, Any] | None,
    ) -> str:
        parts = [
            f"Integrated agent status={status}",
            f"intent={route.intent}",
            f"verifier_profile={route.verifier_profile}",
            f"critic={critic.get('summary')}",
        ]
        if reasoning:
            parts.append(f"reasoning={'accepted' if reasoning.get('accepted') else 'needs_review'} confidence={reasoning.get('confidence')}")
        strict_trace = strict_stability.get("reasoning_trace") if strict_stability else None
        if isinstance(strict_trace, dict):
            parts.append(
                f"strict_stability={'accepted' if strict_trace.get('accepted') else 'needs_review'} "
                f"confidence={strict_trace.get('confidence')}"
            )
        if verifier:
            parts.append(f"verifier={verifier.get('status')} score={verifier.get('score')}")
        if security:
            parts.append(f"multilang_security={security.get('status')} findings={len(security.get('findings') or [])}")
        return "; ".join(parts)

    def _write_failure_event(self, event: dict[str, Any]) -> None:
        path = ROOT / "artifacts" / "phase40" / "failure-events.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")

    def _write_strict_stability_report(self, run_id: str, payload: dict[str, Any]) -> None:
        output_dir = ROOT / "artifacts" / "phase51"
        output_dir.mkdir(parents=True, exist_ok=True)
        wrapped = {"run_id": f"{run_id}-strict-stability", "schema": "phase51.integrated_strict_stability.v1", **payload}
        (output_dir / f"{run_id}-strict-stability.json").write_text(json.dumps(wrapped, indent=2, ensure_ascii=False), encoding="utf-8")
        (output_dir / "integrated-strict-stability-latest.json").write_text(
            json.dumps(wrapped, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def _promote_failure(self, event: dict[str, Any]) -> None:
        store = ExperienceMemoryStore(ROOT / "artifacts" / "phase21" / "experience_memory.jsonl")
        rid = record_id(event["run_id"], "phase40", ",".join(event["reasons"]))
        record = ExperienceRecord(
            record_id=rid,
            source_run_id=event["run_id"],
            task_id=event["run_id"],
            category="integrated_agent",
            failure="; ".join(event["reasons"]),
            fix=event["recommendation"],
            outcome="phase40_integrated_agent_needs_attention",
            confidence=0.75,
            tags=["phase40", event["route"]["intent"], event["route"]["verifier_profile"]],
            metadata=event,
        )
        store.append(record)

    def default_agent_node_cap(self) -> int | None:
        active_profile = os.environ.get("MYTHOS_ACTIVE_PROFILE", "")
        model_name = getattr(self.backend, "model_name", "")
        if active_profile in {"qwen-cpu-smoke", "mock-local"}:
            return 3
        if "0.5B" in model_name or "0.5b" in model_name.lower():
            return 3
        return None

    def agent_backend_for_request(self, request: IntegratedAgentRequest) -> ModelBackend:
        mode = os.environ.get("PHASE40_AGENT_BACKEND_MODE", request.agent_backend_mode)
        if mode == "mock":
            return build_backend("mock")
        if mode == "active":
            return self.backend
        active_profile = os.environ.get("MYTHOS_ACTIVE_PROFILE", "")
        model_name = getattr(self.backend, "model_name", "")
        if active_profile in {"qwen-cpu-smoke", "mock-local"} or "0.5B" in model_name or "0.5b" in model_name.lower():
            return build_backend("mock")
        return self.backend


def write_report(report: IntegratedAgentReport, output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / f"{report.run_id}.json"
    latest_path = output_dir / "integrated-agent-latest.json"
    md_path = output_dir / f"{report.run_id}.md"
    json_path.write_text(json.dumps(report.model_dump(), indent=2, ensure_ascii=False), encoding="utf-8")
    latest_path.write_text(json.dumps(report.model_dump(), indent=2, ensure_ascii=False), encoding="utf-8")
    md_path.write_text(
        "\n".join(
            [
                "# Phase 40 Integrated Agent",
                "",
                f"- Run ID: `{report.run_id}`",
                f"- Status: `{report.status}`",
                f"- Route: `{report.route.get('intent')}` / `{report.route.get('verifier_profile')}`",
                f"- Duration ms: `{report.duration_ms:.1f}`",
                "",
                "## Final Answer",
                "",
                report.final_answer,
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return json_path, md_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Phase 40 integrated default agent.")
    parser.add_argument("--prompt", default="debug this architecture and recommend the safest next patch")
    parser.add_argument("--workspace", default=str(ROOT))
    parser.add_argument("--output-dir", type=Path, default=ROOT / "artifacts" / "phase40")
    parser.add_argument("--verifier-profile")
    parser.add_argument("--agent-backend-mode", choices=["auto", "active", "mock"], default="auto")
    parser.add_argument("--model-critic", action="store_true")
    parser.add_argument("--no-meta-planner", action="store_true")
    parser.add_argument("--no-hierarchical-memory", action="store_true")
    parser.add_argument("--no-reasoning-review", action="store_true")
    parser.add_argument("--no-strict-stability", action="store_true")
    parser.add_argument("--policy-mode", choices=["strict", "development"], default="development")
    parser.add_argument("--no-moe-routing", action="store_true")
    parser.add_argument("--no-vector-memory", action="store_true")
    parser.add_argument("--rebuild-vector-memory", action="store_true")
    parser.add_argument("--no-verifier", action="store_true")
    parser.add_argument("--no-security", action="store_true")
    parser.add_argument("--max-security-files", type=int, default=500)
    parser.add_argument("--strict", action="store_true")
    return parser.parse_args()


async def async_main() -> int:
    args = parse_args()
    backend = build_backend_from_env()
    try:
        report = await IntegratedAgentRuntime(backend).run(
            IntegratedAgentRequest(
                prompt=args.prompt,
                workspace=args.workspace,
                meta_planning=not args.no_meta_planner,
                hierarchical_memory=not args.no_hierarchical_memory,
                reasoning_review=not args.no_reasoning_review,
                strict_stability=not args.no_strict_stability,
                policy_mode=args.policy_mode,
                moe_routing=not args.no_moe_routing,
                vector_memory=not args.no_vector_memory,
                rebuild_vector_memory=args.rebuild_vector_memory,
                run_verifier=not args.no_verifier,
                run_security=not args.no_security,
                verifier_profile=args.verifier_profile,
                use_model_critic=args.model_critic,
                agent_backend_mode=args.agent_backend_mode,
                max_security_files=args.max_security_files,
            )
        )
    finally:
        await backend.aclose()
    json_path, md_path = write_report(report, args.output_dir)
    print(json.dumps({"run_id": report.run_id, "status": report.status, "json": str(json_path), "markdown": str(md_path)}, indent=2))
    return 1 if args.strict and report.status != "complete" else 0


def main() -> None:
    raise SystemExit(asyncio.run(async_main()))


if __name__ == "__main__":
    main()
