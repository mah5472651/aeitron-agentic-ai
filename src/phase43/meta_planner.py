#!/usr/bin/env python
"""Phase 43 meta planner.

Adds a deliberate planning layer before TaskGraph creation:

Goal -> Requirements -> Architecture -> Execution Plan -> TaskGraph brief.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.phase44.intent_expansion import IntentExpansionReport, expand_intent, write_report as write_expansion_report


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class ArchitectureComponent(StrictModel):
    name: str
    responsibility: str
    inputs: list[str]
    outputs: list[str]
    risks: list[str] = Field(default_factory=list)


class ExecutionLane(StrictModel):
    lane: str
    owner_role: str
    tasks: list[str]
    dependencies: list[str] = Field(default_factory=list)
    verification: list[str] = Field(default_factory=list)


class MetaPlanReport(StrictModel):
    run_id: str
    prompt: str
    expansion: dict[str, Any]
    goal: str
    requirements: list[str]
    architecture: list[ArchitectureComponent]
    execution_lanes: list[ExecutionLane]
    risks: list[str]
    taskgraph_brief: str
    confidence: float = Field(ge=0.0, le=1.0)
    created_at_unix: float = Field(default_factory=time.time)


def unique(items: list[str]) -> list[str]:
    return list(dict.fromkeys(item for item in items if item))


def build_architecture(expansion: IntentExpansionReport) -> list[ArchitectureComponent]:
    components = [
        ArchitectureComponent(
            name="Interface Layer",
            responsibility="Expose the user-facing/API surface and validate inputs.",
            inputs=["user requests", "auth context"],
            outputs=["validated commands", "response payloads"],
            risks=["ambiguous prompt interpretation", "unsafe input handling"],
        ),
        ArchitectureComponent(
            name="Domain Service Layer",
            responsibility="Implement core business rules and workflow transitions.",
            inputs=expansion.data_entities,
            outputs=["domain events", "state changes"],
            risks=["incomplete edge cases", "authorization bypass"],
        ),
        ArchitectureComponent(
            name="Persistence Layer",
            responsibility="Store domain entities, audit logs, and durable state.",
            inputs=["validated domain objects"],
            outputs=["queryable records", "audit trail"],
            risks=["migration drift", "PII leakage"],
        ),
        ArchitectureComponent(
            name="Security And Verification Layer",
            responsibility="Enforce auth, rate limits, validation, and regression tests.",
            inputs=["code changes", "security requirements"],
            outputs=["test results", "security findings", "patch guidance"],
            risks=["missing negative tests", "weak secrets handling"],
        ),
    ]
    if expansion.domain in {"streaming", "rideshare", "commerce"}:
        components.append(
            ArchitectureComponent(
                name="Async Workflow Layer",
                responsibility="Handle background jobs, external webhooks, and retryable side effects.",
                inputs=["events", "webhooks", "queues"],
                outputs=["processed jobs", "notifications"],
                risks=["duplicate processing", "retry storms"],
            )
        )
    if expansion.domain in {"streaming", "rideshare"}:
        components.append(
            ArchitectureComponent(
                name="Realtime Coordination Layer",
                responsibility="Coordinate time-sensitive state such as playback, locations, matching, and notifications.",
                inputs=["events", "client updates", "background jobs"],
                outputs=["state transitions", "notifications", "retryable work"],
                risks=["stale state", "duplicate events", "privacy leakage"],
            )
        )
    return components


def build_lanes(expansion: IntentExpansionReport) -> list[ExecutionLane]:
    return [
        ExecutionLane(
            lane="requirements",
            owner_role="architect",
            tasks=["confirm scope", "map user roles", "define acceptance criteria"],
            verification=["requirements cover prompt and security constraints"],
        ),
        ExecutionLane(
            lane="implementation",
            owner_role="coder",
            tasks=["inspect repo conventions", "create minimal modules", "wire interfaces", "add tests"],
            dependencies=["requirements"],
            verification=["unit tests and import checks pass"],
        ),
        ExecutionLane(
            lane="security",
            owner_role="security_auditor",
            tasks=expansion.security_requirements[:6],
            dependencies=["implementation"],
            verification=["Phase 19/27/38 checks pass"],
        ),
        ExecutionLane(
            lane="quality",
            owner_role="tester",
            tasks=["run regression tests", "verify edge cases", "capture telemetry"],
            dependencies=["implementation", "security"],
            verification=expansion.acceptance_tests,
        ),
        ExecutionLane(
            lane="review",
            owner_role="reviewer",
            tasks=["critic review", "summarize patch", "list residual risks"],
            dependencies=["quality"],
            verification=["critic confidence >= threshold", "no blocker findings"],
        ),
    ]


def create_meta_plan(prompt: str, *, run_id: str | None = None) -> MetaPlanReport:
    prompt = prompt.strip()
    if not prompt:
        raise ValueError("prompt must not be empty")
    expansion = expand_intent(prompt, run_id=f"{run_id or 'phase43'}-intent")
    architecture = build_architecture(expansion)
    lanes = build_lanes(expansion)
    risks = unique([risk for component in architecture for risk in component.risks] + ["scope creep", "missing production telemetry"])
    brief = "\n".join(
        [
            f"Goal: {prompt}",
            f"Domain: {expansion.domain}",
            "Expanded requirements:",
            *[f"- {item}" for item in expansion.requirements],
            "Execution lanes:",
            *[f"- {lane.lane}: {', '.join(lane.tasks)}" for lane in lanes],
        ]
    )
    return MetaPlanReport(
        run_id=run_id or f"phase43-{time.time_ns()}",
        prompt=prompt,
        expansion=expansion.model_dump(),
        goal=f"Deliver a safe, tested, maintainable implementation for: {prompt}",
        requirements=expansion.requirements,
        architecture=architecture,
        execution_lanes=lanes,
        risks=risks,
        taskgraph_brief=brief,
        confidence=min(0.95, expansion.confidence + 0.06),
    )


def write_report(report: MetaPlanReport, output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / f"{report.run_id}.json"
    latest_path = output_dir / "meta-planner-latest.json"
    json_path.write_text(json.dumps(report.model_dump(), indent=2, ensure_ascii=False), encoding="utf-8")
    latest_path.write_text(json.dumps(report.model_dump(), indent=2, ensure_ascii=False), encoding="utf-8")
    return json_path, latest_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Phase 43 meta planner.")
    parser.add_argument("--prompt", default="build netflix")
    parser.add_argument("--run-id", default=f"phase43-{int(time.time())}")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "artifacts" / "phase43")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    report = create_meta_plan(args.prompt, run_id=args.run_id)
    json_path, _ = write_report(report, args.output_dir)
    write_expansion_report(IntentExpansionReport.model_validate(report.expansion), ROOT / "artifacts" / "phase44")
    print(json.dumps({"run_id": report.run_id, "requirements": len(report.requirements), "lanes": len(report.execution_lanes), "json": str(json_path)}, indent=2))


if __name__ == "__main__":
    main()
