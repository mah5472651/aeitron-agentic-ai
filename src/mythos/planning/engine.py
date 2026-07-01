"""Consolidated Intent & Planning Engine.

Merges Phase 43 Meta Planner and Phase 44 Intent Expansion behind one API.
"""

from __future__ import annotations

import time
from typing import Any

from pydantic import Field

from src.phase43.meta_planner import create_meta_plan
from src.phase44.intent_expansion import expand_intent
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
        expansion = expand_intent(prompt, run_id=f"{rid}-intent")
        meta = create_meta_plan(prompt, run_id=f"{rid}-metaplan")
        success_criteria = list(expansion.acceptance_tests)
        return PlanningResult(
            run_id=rid,
            prompt=prompt,
            expansion=expansion.model_dump(),
            task_graph_brief=meta.taskgraph_brief,
            goal=meta.goal,
            requirements=list(meta.requirements),
            risks=list(meta.risks),
            success_criteria=success_criteria,
            confidence=min(expansion.confidence, meta.confidence),
        )

