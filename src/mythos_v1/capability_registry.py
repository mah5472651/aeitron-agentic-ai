#!/usr/bin/env python
"""Canonical capability registry for the consolidated Mythos V1 runtime."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class Capability(StrictModel):
    capability_id: str
    name: str
    owner: str
    modules: list[str]
    runtime_stage: str
    required: bool = True
    quality_signal: str


class CapabilityRegistry(StrictModel):
    version: str = "1.0.0"
    main_runtime: str = "src/phase40/integrated_agent.py"
    capabilities: list[Capability]

    @model_validator(mode="after")
    def unique_ownership(self) -> "CapabilityRegistry":
        identifiers = [item.capability_id for item in self.capabilities]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("capability_id values must be unique")
        return self


class RegistryReport(StrictModel):
    run_id: str
    valid: bool
    main_runtime: str
    capability_count: int
    missing_modules: list[str]
    ownership: dict[str, str]
    created_at_unix: float = Field(default_factory=time.time)


def default_registry() -> CapabilityRegistry:
    return CapabilityRegistry(
        capabilities=[
            Capability(
                capability_id="planning",
                name="Intent And Durable Planning",
                owner="Phase 40 integrated runtime",
                modules=["src/phase43/meta_planner.py", "src/phase44/intent_expansion.py", "src/phase20/taskgraph_runtime.py"],
                runtime_stage="pre_execution",
                quality_signal="task graph validity and short-prompt score",
            ),
            Capability(
                capability_id="multi_agent",
                name="Role-Specific Agent Execution",
                owner="Phase 40 integrated runtime",
                modules=["src/phase45/parallel_agent_runtime.py", "src/phase16/role_agents.py", "src/phase24/main_agent_v2.py"],
                runtime_stage="execution",
                quality_signal="workflow completion and role-contract compliance",
            ),
            Capability(
                capability_id="memory",
                name="Unified Context And Experience Memory",
                owner="Phase 51 stability layer",
                modules=["src/phase51/high_stability_reasoning_memory.py", "src/phase37/vector_memory.py", "src/phase31/long_context_packer.py"],
                runtime_stage="pre_execution",
                quality_signal="retrieval relevance, deduplication, isolation, and pollution rejection",
            ),
            Capability(
                capability_id="reasoning",
                name="Critic And Reflection Reasoning",
                owner="Phase 51 stability layer",
                modules=["src/phase51/high_stability_reasoning_memory.py", "src/phase47/reasoning_engine.py", "src/phase22/critic_service.py"],
                runtime_stage="post_execution",
                quality_signal="critic confidence, reflection trigger, and schema validity",
            ),
            Capability(
                capability_id="verification",
                name="Code And Policy Verification",
                owner="Phase 40 integrated runtime",
                modules=["src/phase19/verifier_registry.py", "src/phase27/verifier_policy_engine.py", "src/phase2/docker_sandbox_engine.py"],
                runtime_stage="verification",
                quality_signal="compile, test, sandbox, and policy pass rates",
            ),
            Capability(
                capability_id="security",
                name="Defensive Security Analysis",
                owner="Phase 40 integrated runtime",
                modules=["src/phase38/multilang_security.py", "src/phase28/security_expert_workflow.py", "src/phase16/tool_adapters.py"],
                runtime_stage="verification",
                quality_signal="security detection and secure-patch score",
            ),
            Capability(
                capability_id="evaluation",
                name="Golden Evaluation And Regression",
                owner="Mythos V1 release gate",
                modules=["src/phase14/scorecard_harness.py", "src/phase41/regression_pack.py", "src/mythos_v1/release_gate.py"],
                runtime_stage="release",
                quality_signal="scorecard, regression count, and release decision",
            ),
            Capability(
                capability_id="training_readiness",
                name="Reviewed Data And GPU Training Readiness",
                owner="Mythos V1 training preflight",
                modules=["src/mythos_v1/training_preflight.py", "src/phase29/dataset_review_gate.py", "src/phase39/checkpoint_rollback.py"],
                runtime_stage="training",
                quality_signal="schema-valid reviewed rows and checkpoint promotion safety",
            ),
        ]
    )


def validate_registry(registry: CapabilityRegistry, *, run_id: str) -> RegistryReport:
    required_paths = [registry.main_runtime, *[module for capability in registry.capabilities if capability.required for module in capability.modules]]
    missing = sorted({path for path in required_paths if not (ROOT / path).exists()})
    return RegistryReport(
        run_id=run_id,
        valid=not missing,
        main_runtime=registry.main_runtime,
        capability_count=len(registry.capabilities),
        missing_modules=missing,
        ownership={item.capability_id: item.owner for item in registry.capabilities},
    )


def write_outputs(registry: CapabilityRegistry, report: RegistryReport, output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = output_dir / "capability-registry.json"
    report_path = output_dir / "capability-registry-latest.json"
    manifest_path.write_text(json.dumps(registry.model_dump(), indent=2, ensure_ascii=False), encoding="utf-8")
    report_path.write_text(json.dumps(report.model_dump(), indent=2, ensure_ascii=False), encoding="utf-8")
    return manifest_path, report_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate the consolidated Mythos V1 capability registry.")
    parser.add_argument("--run-id", default=f"mythos-v1-capabilities-{int(time.time())}")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "artifacts" / "mythos_v1")
    parser.add_argument("--strict", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    registry = default_registry()
    report = validate_registry(registry, run_id=args.run_id)
    manifest, report_path = write_outputs(registry, report, args.output_dir)
    print(json.dumps({"valid": report.valid, "capabilities": report.capability_count, "manifest": str(manifest), "report": str(report_path)}, indent=2))
    raise SystemExit(1 if args.strict and not report.valid else 0)


if __name__ == "__main__":
    main()

