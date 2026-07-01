"""Consolidated Mythos runtime.

The implementation delegates to Phase 40's integrated path while presenting a
stable non-phase API for the product.
"""

from __future__ import annotations

import time
from typing import Any

from src.phase40.integrated_agent import IntegratedAgentRequest, IntegratedAgentRuntime
from src.mythos.model_ops.backends import build_active_backend
from src.mythos.planning.engine import IntentPlanningEngine
from src.mythos.shared.schemas import MythosRunReport, MythosRunRequest


class MythosRuntime:
    def __init__(self) -> None:
        self.planner = IntentPlanningEngine()

    async def run(self, request: MythosRunRequest) -> MythosRunReport:
        started = time.perf_counter()
        plan = self.planner.plan(request.prompt)
        backend = build_active_backend()
        try:
            phase40_report = await IntegratedAgentRuntime(backend).run(
                IntegratedAgentRequest(
                    prompt=request.prompt,
                    workspace=request.workspace,
                    policy_mode=request.policy_mode,
                    agent_backend_mode=request.agent_backend_mode,
                    run_verifier=request.run_verifier,
                    run_security=request.run_security,
                    max_agent_nodes=request.max_agent_nodes,
                    metadata={**request.metadata, "mythos_plan_id": plan.run_id},
                )
            )
        finally:
            await backend.aclose()
        return MythosRunReport(
            run_id=phase40_report.run_id,
            status=phase40_report.status,
            summary=phase40_report.summary,
            confidence=phase40_report.confidence,
            prompt=request.prompt,
            workspace=phase40_report.workspace,
            final_answer=phase40_report.final_answer,
            route=phase40_report.route,
            plan=plan.model_dump(),
            memory=phase40_report.hierarchical_memory or phase40_report.vector_memory,
            verification=phase40_report.verifier,
            security=phase40_report.multilang_security,
            artifacts={
                "agent": phase40_report.agent,
                "critic": phase40_report.critic,
                "reasoning": phase40_report.reasoning,
                "strict_stability": phase40_report.strict_stability,
            },
            duration_ms=(time.perf_counter() - started) * 1000,
        )

