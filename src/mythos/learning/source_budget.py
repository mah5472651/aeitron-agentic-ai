"""Source budget allocation driven by reputation scores."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from pydantic import Field

from src.mythos.learning.source_registry import SourceRegistry
from src.mythos.shared.schemas import StrictModel


class SourceBudget(StrictModel):
    source: str
    category: str
    action: str
    reputation_score: float = Field(ge=0.0, le=1.0)
    target_docs: int
    max_depth: int
    delay_seconds: float
    reason: str


class SourceBudgetPlan(StrictModel):
    target_total_docs: int
    allocated_total_docs: int
    budgets: list[SourceBudget]
    created_at_unix: float = Field(default_factory=time.time)


def _load_reputation(path: str | Path | None) -> dict[str, dict[str, object]]:
    if not path:
        return {}
    source = Path(path)
    if not source.exists():
        return {}
    payload = json.loads(source.read_text(encoding="utf-8"))
    return {str(item["source"]): item for item in payload.get("sources", [])}


def build_source_budget_plan(
    *,
    sources_path: str | Path,
    reputation_report_path: str | Path | None,
    target_total_docs: int,
    min_docs_per_source: int = 10,
    max_docs_per_source: int = 5000,
) -> SourceBudgetPlan:
    registry = SourceRegistry.from_file(sources_path)
    reputation_by_source = _load_reputation(reputation_report_path)
    weights: dict[str, float] = {}
    source_meta: dict[str, tuple[str, str, float]] = {}
    for source in registry.to_sources():
        rep = reputation_by_source.get(source.name, {})
        score = float(rep.get("reputation_score", 0.62))
        action = str(rep.get("action", "watch"))
        category_multiplier = 1.25 if source.category in {"defensive_security", "vulnerability_database"} else 1.0
        action_multiplier = {"promote": 1.4, "watch": 1.0, "throttle": 0.35, "block": 0.0}.get(action, 0.8)
        weight = max(0.0, score * category_multiplier * action_multiplier)
        weights[source.name] = weight
        source_meta[source.name] = (source.category, action, score)

    total_weight = sum(weights.values()) or 1.0
    budgets: list[SourceBudget] = []
    allocated = 0
    for name, weight in sorted(weights.items(), key=lambda item: item[1], reverse=True):
        category, action, score = source_meta[name]
        if action == "block" or weight <= 0:
            docs = 0
            reason = "blocked_by_reputation"
        else:
            docs = int(round(target_total_docs * (weight / total_weight)))
            docs = max(min_docs_per_source, min(max_docs_per_source, docs))
            reason = "allocated_by_reputation_weight"
        allocated += docs
        budgets.append(
            SourceBudget(
                source=name,
                category=category,
                action=action,
                reputation_score=round(score, 6),
                target_docs=docs,
                max_depth=2 if score >= 0.70 else 1,
                delay_seconds=0.25 if score >= 0.78 else 0.75 if score >= 0.55 else 1.5,
                reason=reason,
            )
        )
    return SourceBudgetPlan(target_total_docs=target_total_docs, allocated_total_docs=allocated, budgets=budgets)


def write_source_budget_plan(output_path: str | Path, **kwargs: object) -> SourceBudgetPlan:
    plan = build_source_budget_plan(**kwargs)
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(plan.model_dump(), indent=2, sort_keys=True), encoding="utf-8")
    return plan


def main() -> None:
    parser = argparse.ArgumentParser(description="Allocate Mythos crawl budget from source reputation.")
    parser.add_argument("--sources", required=True)
    parser.add_argument("--reputation-report")
    parser.add_argument("--target-total-docs", type=int, required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    plan = write_source_budget_plan(
        args.output,
        sources_path=args.sources,
        reputation_report_path=args.reputation_report,
        target_total_docs=args.target_total_docs,
    )
    print(json.dumps(plan.model_dump(), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
