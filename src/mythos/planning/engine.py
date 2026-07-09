"""Native Intent & Planning Engine for the final Mythos architecture."""

from __future__ import annotations

import time
from typing import Any

from pydantic import Field

from src.mythos.shared.schemas import StrictModel


class PlanningResult(StrictModel):
    run_id: str
    prompt: str
    expansion: dict[str, Any]
    task_graph_brief: str
    goal: str
    requirements: list[str]
    risks: list[str]
    success_criteria: list[str]
    confidence: float = Field(ge=0.0, le=1.0)
    created_at_unix: float = Field(default_factory=time.time)


class IntentPlanningEngine:
    def plan(self, prompt: str, *, run_id: str | None = None) -> PlanningResult:
        rid = run_id or f"mythos-plan-{time.time_ns()}"
        lowered = prompt.lower()
        intent = "security_review" if any(term in lowered for term in ["security", "vulnerability", "cve", "exploit"]) else "debug" if any(term in lowered for term in ["bug", "fix", "error", "fail"]) else "code_edit"
        requirements = [
            "understand the repository context",
            "retrieve relevant files and symbols",
            "propose a minimal patch",
            "run verification commands",
            "return evidence with the final answer",
        ]
        if intent == "security_review":
            requirements.append("run defensive security checks before accepting changes")
        risks = [
            "insufficient repository context",
            "patch may fail tests",
            "security regression if verification is skipped",
        ]
        success_criteria = [
            "context pack includes relevant source and tests",
            "patch preview is generated before apply",
            "configured tests pass",
            "verifier returns accept",
        ]
        expansion = {"intent": intent, "acceptance_tests": success_criteria, "source": "native-planner"}
        return PlanningResult(
            run_id=rid,
            prompt=prompt,
            expansion=expansion,
            task_graph_brief="understand -> planner -> retrieve_context -> edit -> test -> critic_review -> security_review -> performance_review -> verify -> summarize",
            goal=f"Complete Mythos request: {prompt}",
            requirements=requirements,
            risks=risks,
            success_criteria=success_criteria,
            confidence=0.82,
        )
