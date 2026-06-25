#!/usr/bin/env python
"""End-to-end contract test for Phase 43 through Phase 50.

This test imports the modules directly and verifies the hard-part architecture
flow:

MoE route -> intent expansion -> meta plan -> parallel agents ->
hierarchical memory -> reasoning -> knowledge graph -> multimodal contract ->
Phase 40 integrated path.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel, ConfigDict, Field

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.phase11.model_backends import build_backend
from src.phase40.integrated_agent import IntegratedAgentRequest, IntegratedAgentRuntime
from src.phase43.meta_planner import create_meta_plan
from src.phase44.intent_expansion import expand_intent
from src.phase45.parallel_agent_runtime import ParallelAgentRuntime
from src.phase46.hierarchical_memory import HierarchicalMemory
from src.phase47.reasoning_engine import ReasoningEngine
from src.phase48.knowledge_graph import KnowledgeGraph
from src.phase49.multimodal_expert import MultimodalExpert
from src.phase50.moe_router import MoERouter


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class E2EStepResult(StrictModel):
    name: str
    status: str
    detail: str
    duration_ms: float
    data: dict[str, Any] = Field(default_factory=dict)


class Phase43To50E2EReport(StrictModel):
    run_id: str
    prompt: str
    passed: bool
    steps: list[E2EStepResult]
    recommendation: str
    created_at_unix: float = Field(default_factory=time.time)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


async def timed_step(name: str, fn: Callable[[], Any]) -> E2EStepResult:
    started = time.time()
    try:
        value = fn()
        if hasattr(value, "__await__"):
            value = await value
        return E2EStepResult(
            name=name,
            status="pass",
            detail="contract satisfied",
            duration_ms=(time.time() - started) * 1000,
            data=value if isinstance(value, dict) else {},
        )
    except Exception as exc:
        return E2EStepResult(
            name=name,
            status="fail",
            detail=f"{type(exc).__name__}: {exc}",
            duration_ms=(time.time() - started) * 1000,
        )


async def run_e2e(prompt: str, *, run_id: str, include_phase40: bool) -> Phase43To50E2EReport:
    steps: list[E2EStepResult] = []
    state: dict[str, Any] = {}

    def phase50_route() -> dict[str, Any]:
        report = MoERouter().route(prompt, run_id=f"{run_id}-phase50", top_k=4)
        experts = [route.expert for route in report.routes]
        require("security_expert" in experts, "secure login prompt must route to security expert")
        require("planning_expert" in experts, "build/system prompt must route to planning expert")
        state["route"] = report
        return {"primary": report.primary_expert, "experts": experts}

    def phase44_expand() -> dict[str, Any]:
        report = expand_intent(prompt, run_id=f"{run_id}-phase44")
        require(report.domain == "login", f"expected login domain, got {report.domain}")
        require("password hashing" in report.security_requirements, "login expansion must include password hashing")
        require(len(report.acceptance_tests) >= 4, "acceptance tests must be populated")
        state["expansion"] = report
        return {"domain": report.domain, "requirements": len(report.requirements), "security": len(report.security_requirements)}

    def phase43_plan() -> dict[str, Any]:
        report = create_meta_plan(prompt, run_id=f"{run_id}-phase43")
        lane_names = [lane.lane for lane in report.execution_lanes]
        require("requirements" in lane_names, "meta plan missing requirements lane")
        require("security" in lane_names, "meta plan missing security lane")
        require(report.taskgraph_brief.count("\n- ") >= 5, "taskgraph brief is too thin")
        state["meta_plan"] = report
        return {"lanes": lane_names, "components": len(report.architecture), "confidence": report.confidence}

    async def phase45_parallel() -> dict[str, Any]:
        backend = build_backend("mock")
        try:
            report = await ParallelAgentRuntime(backend, max_parallel=5).run(prompt, run_id=f"{run_id}-phase45")
        finally:
            await backend.aclose()
        require(report.role_count >= 4, "parallel runtime must execute multiple specialist roles")
        require(report.confidence >= 0.5, "parallel runtime confidence too low")
        state["parallel"] = report
        return {"roles": report.role_count, "groups": report.parallel_groups, "confidence": report.confidence}

    def phase46_memory(tmp: Path) -> dict[str, Any]:
        memory = HierarchicalMemory(tmp / "memory")
        report = memory.run("secure login planner verifier", run_id=f"{run_id}-phase46", seed=True, limit=5)
        require(report.layers["knowledge"] >= 1, "knowledge layer not seeded")
        require(len(report.hits) >= 2, "hierarchical memory should retrieve seeded hits")
        state["memory"] = report
        return {"layers": report.layers, "hits": len(report.hits)}

    def phase47_reasoning() -> dict[str, Any]:
        report = ReasoningEngine().run(prompt, run_id=f"{run_id}-phase47")
        require(report.accepted, "reasoning engine should accept the secure login planning prompt")
        require(len(report.stages) == 3, "reasoning engine must produce thinker/critic/verifier stages")
        state["reasoning"] = report
        return {"accepted": report.accepted, "confidence": report.confidence, "stages": [stage.name for stage in report.stages]}

    def phase48_graph(tmp: Path) -> dict[str, Any]:
        graph = KnowledgeGraph(tmp / "knowledge-graph.json")
        report = graph.run("meta planner memory reasoning", run_id=f"{run_id}-phase48", seed=True)
        require(report.nodes >= 8, "knowledge graph seed should contain phase nodes")
        require(report.edges >= 8, "knowledge graph seed should contain relationships")
        require(report.matches, "knowledge graph query should return matches")
        state["graph"] = report
        return {"nodes": report.nodes, "edges": report.edges, "matches": len(report.matches)}

    def phase49_multimodal(tmp: Path) -> dict[str, Any]:
        image_path = tmp / "screenshot.png"
        image_path.write_bytes(b"\x89PNG\r\n\x1a\n")
        repo_dir = tmp / "repo"
        repo_dir.mkdir()
        (repo_dir / "app.py").write_text("print('hello')\n", encoding="utf-8")
        report = MultimodalExpert().analyze("analyze this screenshot and repository", [image_path, repo_dir], run_id=f"{run_id}-phase49", max_files=10)
        signals = [signal for artifact in report.artifacts for signal in artifact.signals]
        require("vision_candidate" in signals, "image path should be marked as vision candidate")
        require(any(signal.startswith("sampled_files=") for signal in signals), "folder path should include sampled file signal")
        state["multimodal"] = report
        return {"artifacts": len(report.artifacts), "signals": signals}

    async def phase40_integrated() -> dict[str, Any]:
        backend = build_backend("mock")
        try:
            report = await IntegratedAgentRuntime(backend).run(
                IntegratedAgentRequest(
                    prompt=prompt,
                    workspace=str(ROOT),
                    policy_mode="development",
                    meta_planning=True,
                    vector_memory=False,
                    run_verifier=False,
                    run_security=False,
                    agent_backend_mode="mock",
                    max_agent_nodes=3,
                )
            )
        finally:
            await backend.aclose()
        require(report.status == "complete", f"Phase 40 expected complete, got {report.status}")
        require(report.meta_plan is not None, "Phase 40 must include Phase 43 meta-plan payload")
        return {"status": report.status, "route": report.route, "meta_plan": bool(report.meta_plan)}

    with tempfile.TemporaryDirectory(prefix="phase43_50_e2e_") as temp_dir:
        tmp = Path(temp_dir)
        checks: list[tuple[str, Callable[[], Any]]] = [
            ("phase50_moe_router", phase50_route),
            ("phase44_intent_expansion", phase44_expand),
            ("phase43_meta_planner", phase43_plan),
            ("phase45_parallel_agent_runtime", phase45_parallel),
            ("phase46_hierarchical_memory", lambda: phase46_memory(tmp)),
            ("phase47_reasoning_engine", phase47_reasoning),
            ("phase48_knowledge_graph", lambda: phase48_graph(tmp)),
            ("phase49_multimodal_expert", lambda: phase49_multimodal(tmp)),
        ]
        if include_phase40:
            checks.append(("phase40_integrated_path", phase40_integrated))
        for name, fn in checks:
            steps.append(await timed_step(name, fn))

    passed = all(step.status == "pass" for step in steps)
    recommendation = (
        "Phase 43-50 contracts are wired and ready for broader regression/profile testing."
        if passed
        else "Fix failing step details before treating the final eight phases as production-ready."
    )
    return Phase43To50E2EReport(run_id=run_id, prompt=prompt, passed=passed, steps=steps, recommendation=recommendation)


def write_report(report: Phase43To50E2EReport, output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / f"{report.run_id}.json"
    latest_path = output_dir / "phase43-to-50-e2e-latest.json"
    json_path.write_text(json.dumps(report.model_dump(), indent=2, ensure_ascii=False), encoding="utf-8")
    latest_path.write_text(json.dumps(report.model_dump(), indent=2, ensure_ascii=False), encoding="utf-8")
    return json_path, latest_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run end-to-end tests for Phase 43 through Phase 50.")
    parser.add_argument("--prompt", default="build secure login system")
    parser.add_argument("--run-id", default=f"phase43-to-50-e2e-{int(time.time())}")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "artifacts" / "phase50")
    parser.add_argument("--skip-phase40", action="store_true")
    return parser.parse_args()


async def async_main() -> int:
    args = parse_args()
    report = await run_e2e(args.prompt, run_id=args.run_id, include_phase40=not args.skip_phase40)
    json_path, _ = write_report(report, args.output_dir)
    print(
        json.dumps(
            {
                "run_id": report.run_id,
                "passed": report.passed,
                "steps": {step.name: step.status for step in report.steps},
                "json": str(json_path),
            },
            indent=2,
        )
    )
    return 0 if report.passed else 1


def main() -> None:
    raise SystemExit(asyncio.run(async_main()))


if __name__ == "__main__":
    main()
