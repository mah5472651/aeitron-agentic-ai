#!/usr/bin/env python
"""Target architecture blueprint for Mythos-class coding/security AI.

This file formalizes the destination architecture the user described:
Cursor + Claude Code + DeepSeek R1 style system, with a practical base-model
route first and a future 50B-100B / MoE path later.
"""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class TargetComponent:
    name: str
    target: str
    current_assets: list[str]
    status: str
    gap: str
    priority: str
    next_build: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ModelScalePlan:
    phase: str
    target_parameters: str
    active_parameters: str
    method: str
    hardware_class: str
    notes: list[str]


@dataclass(frozen=True)
class ArchitectureBlueprint:
    run_id: str
    created_at_unix: float
    target_summary: str
    model_strategy: list[ModelScalePlan]
    components: list[TargetComponent]
    top_ai_weights: dict[str, int]
    priority_pillars: list[str]
    immediate_build_order: list[str]
    risk_register: list[str]


def build_blueprint(run_id: str) -> ArchitectureBlueprint:
    components = [
        TargetComponent(
            name="Intent Engine",
            target="Expand vague user prompts into concrete engineering intent and constraints.",
            current_assets=["src/phase11/memory_engine.py", "src/phase11/agentic_runtime.py"],
            status="partial",
            gap="Current expansion is rule-based; needs learned intent classifier and ambiguity detector.",
            priority="high",
            next_build=[
                "Add IntentFrame schema",
                "Train/evaluate short-prompt classifier",
                "Route ambiguous prompts to assumption generator",
            ],
        ),
        TargetComponent(
            name="Planner + Task Graph",
            target="LLM + graph planner that decomposes large builds into dependency DAGs.",
            current_assets=["src/phase11/agentic_runtime.py", "src/phase4/swarm_orchestrator.py", "src/phase16/task_graph.py"],
            status="implemented_phase16_needs_runtime_default",
            gap="Durable TaskGraph exists; needs to become the default planner path in the main agent runtime.",
            priority="critical",
            next_build=[
                "Route main AgenticCodingRuntime through Phase 16 TaskGraph",
                "Add replay UI/reporting",
                "Use scorecard failures to tune planning decisions",
            ],
        ),
        TargetComponent(
            name="Agent Orchestrator",
            target="Architect, coder, tester, debugger, security auditor, reviewer, and researcher agents.",
            current_assets=["src/phase4/swarm_orchestrator.py", "src/phase11/agentic_runtime.py", "src/phase16/role_agents.py"],
            status="implemented_phase16_needs_deeper_specialization",
            gap="Role agents exist; need stronger role prompts, artifacts, and correction loops around real model outputs.",
            priority="critical",
            next_build=[
                "Add role-specific evaluation tasks",
                "Add artifact contracts to API responses",
                "Add peer review and retry policies per role",
            ],
        ),
        TargetComponent(
            name="Tool Layer",
            target="Browser, terminal, Docker, Git, compiler, database, IDE, file system, CodeQL, Semgrep, Nuclei, ZAP.",
            current_assets=["src/phase2/docker_sandbox_engine.py", "src/phase11/tool_runtime.py", "src/phase16/tool_adapters.py"],
            status="defensive_core_connected",
            gap="Git/Semgrep/CodeQL/browser adapters exist; need full scan pipelines, SARIF parsing, and severity gates.",
            priority="high",
            next_build=[
                "Add Semgrep JSON severity gates",
                "Add CodeQL database create/analyze flow",
                "Add tool permission policy",
                "Add tool trace viewer",
            ],
        ),
        TargetComponent(
            name="Memory Layer",
            target="Short-term, long-term, and experience memory over hierarchical active/working/archive context.",
            current_assets=["src/phase11/memory_engine.py", "src/phase11/persistent_memory.py", "src/phase16/experience_memory.py"],
            status="partial",
            gap="Local retrieval and JSONL experience memory exist; needs production Postgres/Qdrant promotion and planning-time recall.",
            priority="critical",
            next_build=[
                "Add active/working/archive context tiers",
                "Promote failure -> fix -> outcome records to Postgres/Qdrant",
                "Retrieve experience records during TaskGraph planning",
            ],
        ),
        TargetComponent(
            name="Critic Model",
            target="Separate critic that finds flaws in plans, patches, reasoning, and security posture.",
            current_assets=["src/phase11/agentic_runtime.py", "src/phase16/critic_verifier.py"],
            status="interface_ready_heuristic_active",
            gap="CriticBackend exists, but active critic is heuristic unless a dedicated critic model is connected.",
            priority="critical",
            next_build=[
                "Connect model-backed critic",
                "Build critic dataset",
                "Generate wrong-vs-correct reasoning pairs",
            ],
        ),
        TargetComponent(
            name="Verifier Layer",
            target="Math, code, security, and fact verifiers, separate from the generator.",
            current_assets=["src/phase2/docker_sandbox_engine.py", "src/phase9/evaluate.py", "src/phase14/scorecard_harness.py", "src/phase16/critic_verifier.py"],
            status="partial",
            gap="Composite verifier exists; needs larger benchmark coverage, Semgrep/CodeQL gates, and repo-scale regression suites.",
            priority="high",
            next_build=[
                "Add Semgrep verifier",
                "Add CodeQL verifier",
                "Add code/test/security/fact verifier registry",
                "Add verifier consensus policy",
            ],
        ),
        TargetComponent(
            name="Base Model Strategy",
            target="Start from Qwen/DeepSeek/Llama lineage; avoid foundation training at first.",
            current_assets=[
                "src/phase11/model_backends.py",
                "src/phase13/backend_quality_harness.py",
                "src/phase16/local_hf_openai_server.py",
                "src/phase17/gpu_readiness.py",
                "deploy/gpu/model_profiles.json",
            ],
            status="local_openai_compatible_connected_gpu_profiles_ready",
            gap="Pinned tiny HF CPU backend proves real OpenAI-compatible integration; Qwen/7B-32B profiles/configs are ready but require a compatible runtime or Linux CUDA to execute.",
            priority="critical",
            next_build=[
                "Run scorecard against connected Qwen baseline",
                "Execute prepared 7B profile on Linux CUDA",
                "Use failures to build SFT/GRPO data",
            ],
        ),
        TargetComponent(
            name="Future MoE 50B-100B+",
            target="Future 50B-100B dense or MoE route; long-term 500B total / 64B active / top-8 routing is research scale.",
            current_assets=["docs/mythos_complete_architecture_manual.md", "src/phase7/grpo_training_loop.py"],
            status="research_roadmap",
            gap="No distributed training stack yet; needs Linux CUDA cluster, DeepSpeed/Megatron/Ray/Kubernetes.",
            priority="later",
            next_build=[
                "Prototype small MoE router locally",
                "Define expert taxonomy",
                "Prepare distributed training config for Linux GPU cluster",
            ],
        ),
        TargetComponent(
            name="Self Improvement Loop",
            target="Experience -> evaluation -> synthetic data -> retraining -> new version, never live weight edits.",
            current_assets=["src/phase5/self_healing_runtime.py", "src/phase14/scorecard_harness.py", "src/phase16/sft_exporter.py"],
            status="partial",
            gap="Self-healing trace, scorecard, and failure exporter exist; needs human/data-quality review gate and versioned training queue.",
            priority="high",
            next_build=[
                "Add failure-to-SFT exporter",
                "Add nightly training candidate buffer",
                "Add versioned eval gate",
            ],
        ),
    ]

    model_strategy = [
        ModelScalePlan(
            phase="Now",
            target_parameters="7B-32B",
            active_parameters="7B-32B",
            method="Use practical open base model through vLLM/OpenAI-compatible API.",
            hardware_class="Single strong GPU or rented Linux CUDA box.",
            notes=[
                "Do not train a foundation model from scratch.",
                "Use Qwen/DeepSeek/Llama lineage.",
                "Measure with scorecard before tuning.",
            ],
        ),
        ModelScalePlan(
            phase="Next",
            target_parameters="50B-100B",
            active_parameters="50B-100B dense or smaller active MoE",
            method="Fine-tune/serve a stronger code model; add LoRA/QLoRA and distillation.",
            hardware_class="Multi-GPU Linux node.",
            notes=[
                "Focus on coding/security/tool-use data.",
                "Use vLLM/TensorRT-LLM for inference.",
                "Use DeepSpeed/Megatron-LM for training experiments.",
            ],
        ),
        ModelScalePlan(
            phase="Research",
            target_parameters="500B MoE",
            active_parameters="64B active, top-8 routing",
            method="Expert router with coding/security/math/research/planning experts.",
            hardware_class="Cluster scale: many GPUs, Ray/Kubernetes, distributed storage.",
            notes=[
                "This is not a first build step.",
                "Prototype routing on small experts before scaling.",
                "Keep critic/verifier separate from generator.",
            ],
        ),
    ]

    return ArchitectureBlueprint(
        run_id=run_id,
        created_at_unix=time.time(),
        target_summary="Cursor + Claude Code + DeepSeek R1 style coding/security AI with agentic tools, memory, critic, verifier, and future 50B-100B+ model path.",
        model_strategy=model_strategy,
        components=components,
        top_ai_weights={
            "Data Quality": 35,
            "Reasoning Training": 20,
            "Agent System": 15,
            "Evaluation": 10,
            "Memory": 10,
            "Tools": 5,
            "Architecture": 5,
        },
        priority_pillars=[
            "Planner",
            "Multi-Agent System",
            "Memory",
            "Critic",
            "Verification",
            "Security Experts",
            "High-quality coding/reasoning data",
        ],
        immediate_build_order=[
            "1. Run exact scorecard against the connected local Qwen backend.",
            "2. Convert Qwen scorecard failures into reviewed SFT/GRPO candidates.",
            "3. Add Semgrep and CodeQL verifier gates with JSON/SARIF parsing.",
            "4. Connect a model-backed CriticBackend.",
            "5. Promote ExperienceMemory into Postgres/Qdrant.",
            "6. Make Phase 16 TaskGraph the default main agent planner.",
            "7. Expand SWE-Bench/CyberSecEval-style evaluation coverage.",
            "8. Run the prepared 7B profile on Linux CUDA, then graduate to 14B/32B.",
        ],
        risk_register=[
            "50B-100B models require Linux CUDA infrastructure; Windows local machine is architecture/dev only.",
            "Foundation pretraining from scratch is not practical at this stage.",
            "Security tools must stay defensive; avoid autonomous exploit execution.",
            "10M-token context should be hierarchical memory, not naive full attention.",
            "Self-improvement must be offline retraining/versioning, not live weight edits.",
        ],
    )


def write_reports(blueprint: ArchitectureBlueprint, output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / f"{blueprint.run_id}.json"
    md_path = output_dir / f"{blueprint.run_id}.md"
    json_path.write_text(json.dumps(asdict(blueprint), indent=2, ensure_ascii=False), encoding="utf-8")

    lines = [
        "# Mythos Target Architecture Blueprint",
        "",
        f"- Run ID: `{blueprint.run_id}`",
        f"- Target: {blueprint.target_summary}",
        "",
        "## Model Strategy",
        "",
    ]
    for plan in blueprint.model_strategy:
        lines.extend(
            [
                f"### {plan.phase}",
                "",
                f"- Target parameters: `{plan.target_parameters}`",
                f"- Active parameters: `{plan.active_parameters}`",
                f"- Method: {plan.method}",
                f"- Hardware: {plan.hardware_class}",
            ]
        )
        lines.extend(f"- {note}" for note in plan.notes)
        lines.append("")
    lines.extend(["## What Actually Creates A Top AI", ""])
    for name, weight in blueprint.top_ai_weights.items():
        lines.append(f"- {name}: {weight}%")
    lines.extend(["", "## Seven Priority Pillars", ""])
    lines.extend(f"- {pillar}" for pillar in blueprint.priority_pillars)
    lines.append("")
    lines.extend(["## Components", "", "| Component | Status | Priority | Gap |", "| --- | --- | --- | --- |"])
    for component in blueprint.components:
        lines.append(f"| {component.name} | {component.status} | {component.priority} | {component.gap.replace('|', '/')} |")
    lines.extend(["", "## Immediate Build Order", ""])
    lines.extend(f"- {item}" for item in blueprint.immediate_build_order)
    lines.extend(["", "## Risk Register", ""])
    lines.extend(f"- {risk}" for risk in blueprint.risk_register)
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, md_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Write target architecture blueprint.")
    parser.add_argument("--run-id", default="mythos-target-architecture")
    parser.add_argument("--output-dir", type=Path, default=Path("artifacts/phase15"))
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    blueprint = build_blueprint(args.run_id)
    json_path, md_path = write_reports(blueprint, args.output_dir)
    print(
        json.dumps(
            {
                "run_id": blueprint.run_id,
                "components": len(blueprint.components),
                "json": str(json_path),
                "markdown": str(md_path),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
