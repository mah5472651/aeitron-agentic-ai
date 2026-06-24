#!/usr/bin/env python
"""Phase 45 parallel agent runtime.

Runs specialist agents concurrently from a Phase 43 meta-plan, then aggregates
their outputs into a single handoff report. This is a local-first runtime: mock
backends work out of the box, while active model endpoints can be supplied by
the existing Phase 11 backend contract.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.phase11.model_backends import ModelBackend, build_backend
from src.phase16.role_agents import AgentArtifact, AgentContext, AgentRole, RoleAgent
from src.phase16.task_graph import TaskNode
from src.phase43.meta_planner import MetaPlanReport, create_meta_plan, write_report as write_phase43_report


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class ParallelAgentReport(StrictModel):
    run_id: str
    prompt: str
    meta_plan: dict[str, Any]
    artifacts: list[dict[str, Any]]
    role_count: int
    parallel_groups: list[list[str]]
    conflicts: list[str]
    final_synthesis: str
    confidence: float = Field(ge=0.0, le=1.0)
    duration_ms: float
    created_at_unix: float = Field(default_factory=time.time)


def build_backend_from_env(mode: str) -> ModelBackend:
    if mode == "mock":
        return build_backend("mock")
    return build_backend(
        os.environ.get("PHASE45_BACKEND", os.environ.get("PHASE11_BACKEND", "mock")),
        endpoint=os.environ.get("PHASE45_MODEL_ENDPOINT", os.environ.get("PHASE11_MODEL_ENDPOINT", "http://127.0.0.1:8016/v1")),
        model_name=os.environ.get("PHASE45_MODEL_NAME", os.environ.get("PHASE11_MODEL_NAME", "Qwen/Qwen2.5-Coder-0.5B-Instruct")),
        api_key=os.environ.get("PHASE45_API_KEY", os.environ.get("PHASE11_API_KEY")),
    )


def lane_to_role(owner_role: str) -> AgentRole:
    aliases = {
        "architect": AgentRole.ARCHITECT,
        "coder": AgentRole.CODER,
        "tester": AgentRole.TESTER,
        "security": AgentRole.SECURITY_AUDITOR,
        "security_auditor": AgentRole.SECURITY_AUDITOR,
        "reviewer": AgentRole.REVIEWER,
        "researcher": AgentRole.RESEARCHER,
    }
    return aliases.get(owner_role, AgentRole.REVIEWER)


def task_from_lane(plan: MetaPlanReport, lane_index: int) -> TaskNode:
    lane = plan.execution_lanes[lane_index]
    return TaskNode(
        task_id=f"{plan.run_id}-lane-{lane_index}-{lane.lane}",
        title=f"{lane.lane.title()} Lane",
        description="\n".join([*lane.tasks, "Verification:", *lane.verification]),
        role=lane_to_role(lane.owner_role).value,
        dependencies=[],
        priority=max(10, 95 - lane_index * 5),
        inputs={"prompt": plan.prompt, "meta_plan": plan.model_dump()},
    )


def dependency_groups(plan: MetaPlanReport) -> list[list[int]]:
    lane_names = {lane.lane: index for index, lane in enumerate(plan.execution_lanes)}
    remaining = set(range(len(plan.execution_lanes)))
    completed: set[str] = set()
    groups: list[list[int]] = []
    while remaining:
        ready = [
            index
            for index in sorted(remaining)
            if all(dependency in completed or dependency not in lane_names for dependency in plan.execution_lanes[index].dependencies)
        ]
        if not ready:
            unresolved = {
                plan.execution_lanes[index].lane: [
                    dependency
                    for dependency in plan.execution_lanes[index].dependencies
                    if dependency in lane_names and dependency not in completed
                ]
                for index in sorted(remaining)
            }
            raise ValueError(f"execution lane dependency cycle or unresolved dependency: {unresolved}")
        groups.append(ready)
        for index in ready:
            remaining.remove(index)
            completed.add(plan.execution_lanes[index].lane)
    return groups


class ParallelAgentRuntime:
    def __init__(self, backend: ModelBackend, *, max_parallel: int = 5) -> None:
        self.backend = backend
        self.max_parallel = max(1, max_parallel)

    async def run(self, prompt: str, *, run_id: str | None = None) -> ParallelAgentReport:
        started = time.time()
        plan = create_meta_plan(prompt, run_id=run_id or f"phase45-{time.time_ns()}")
        write_phase43_report(plan, ROOT / "artifacts" / "phase43")
        context = AgentContext(objective=plan.goal, workspace_summary=plan.taskgraph_brief)
        semaphore = asyncio.Semaphore(self.max_parallel)
        artifacts: list[AgentArtifact] = []
        groups = dependency_groups(plan)

        async def run_lane(index: int) -> AgentArtifact:
            task = task_from_lane(plan, index)
            async with semaphore:
                return await RoleAgent(lane_to_role(plan.execution_lanes[index].owner_role), self.backend).run(task, context)

        for group in groups:
            group_artifacts = await asyncio.gather(*(run_lane(index) for index in group))
            context.shared_artifacts.extend(group_artifacts)
            artifacts.extend(group_artifacts)

        conflicts = self.detect_conflicts(artifacts)
        confidence = self.confidence(artifacts, conflicts)
        return ParallelAgentReport(
            run_id=plan.run_id,
            prompt=prompt,
            meta_plan=plan.model_dump(),
            artifacts=[artifact.model_dump() for artifact in artifacts],
            role_count=len({artifact.role for artifact in artifacts}),
            parallel_groups=[[plan.execution_lanes[index].lane for index in group] for group in groups],
            conflicts=conflicts,
            final_synthesis=self.synthesize(plan, artifacts, conflicts),
            confidence=confidence,
            duration_ms=(time.time() - started) * 1000,
        )

    def detect_conflicts(self, artifacts: list[AgentArtifact]) -> list[str]:
        text_by_role = {artifact.role.value: artifact.content.lower() for artifact in artifacts}
        conflicts: list[str] = []
        if "security_auditor" in text_by_role and "coder" in text_by_role:
            if "unsafe" in text_by_role["security_auditor"] and "no security" in text_by_role["coder"]:
                conflicts.append("coder/security disagreement about unsafe behavior")
        if "tester" in text_by_role and "coder" in text_by_role:
            if "no test" in text_by_role["coder"] and "test" in text_by_role["tester"]:
                conflicts.append("implementation needs explicit test follow-through")
        return conflicts

    def confidence(self, artifacts: list[AgentArtifact], conflicts: list[str]) -> float:
        if not artifacts:
            return 0.0
        base = sum(artifact.confidence for artifact in artifacts) / len(artifacts)
        return max(0.0, min(1.0, base - len(conflicts) * 0.08))

    def synthesize(self, plan: MetaPlanReport, artifacts: list[AgentArtifact], conflicts: list[str]) -> str:
        lines = [
            f"Parallel agent run for: {plan.prompt}",
            f"Roles completed: {', '.join(sorted({artifact.role.value for artifact in artifacts}))}",
            "Recommended next action: apply the coder output only after tester and security verification pass.",
        ]
        if conflicts:
            lines.append(f"Conflicts requiring review: {'; '.join(conflicts)}")
        lines.append("Artifact summaries:")
        for artifact in artifacts:
            compact = " ".join(artifact.content.split())[:360]
            lines.append(f"- {artifact.role.value}: {compact}")
        return "\n".join(lines)


def write_report(report: ParallelAgentReport, output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / f"{report.run_id}.json"
    latest_path = output_dir / "parallel-agent-latest.json"
    json_path.write_text(json.dumps(report.model_dump(), indent=2, ensure_ascii=False), encoding="utf-8")
    latest_path.write_text(json.dumps(report.model_dump(), indent=2, ensure_ascii=False), encoding="utf-8")
    return json_path, latest_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Phase 45 parallel specialist agents.")
    parser.add_argument("--prompt", default="build secure login system")
    parser.add_argument("--run-id")
    parser.add_argument("--backend-mode", choices=["mock", "env"], default="mock")
    parser.add_argument("--max-parallel", type=int, default=5)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "artifacts" / "phase45")
    return parser.parse_args()


async def async_main() -> int:
    args = parse_args()
    if args.max_parallel < 1:
        raise ValueError("--max-parallel must be >= 1")
    backend = build_backend_from_env(args.backend_mode)
    try:
        report = await ParallelAgentRuntime(backend, max_parallel=args.max_parallel).run(args.prompt, run_id=args.run_id)
    finally:
        await backend.aclose()
    json_path, _ = write_report(report, args.output_dir)
    print(json.dumps({"run_id": report.run_id, "roles": report.role_count, "confidence": report.confidence, "json": str(json_path)}, indent=2))
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(async_main()))


if __name__ == "__main__":
    main()
