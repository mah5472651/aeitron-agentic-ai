#!/usr/bin/env python
"""Phase 16 smoke test for core architecture upgrades."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.phase11.model_backends import MockReasoningBackend
from src.phase16.base_model_connector import probe_real_endpoint
from src.phase16.critic_verifier import CompositeVerifier, HeuristicCriticBackend
from src.phase16.experience_memory import ExperienceMemoryStore
from src.phase16.role_agents import RoleAgentOrchestrator
from src.phase16.sft_exporter import ScorecardFailureExporter
from src.phase16.task_graph import TaskGraphPlanner, TaskGraphStore
from src.phase16.tool_adapters import ToolAdapterRegistry


def latest_scorecard() -> Path | None:
    directory = ROOT / "artifacts" / "scorecard"
    if not directory.exists():
        return None
    candidates = sorted(directory.glob("*.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    return candidates[0] if candidates else None


async def run_smoke() -> dict[str, Any]:
    started = time.time()
    checks: dict[str, Any] = {}
    planner = TaskGraphPlanner()
    graph = planner.plan(
        "Build a secure login API, debug failing tests, and add verification.",
        workspace_summary="FastAPI backend with sandbox and memory modules.",
    )
    graph.validate_acyclic()
    graph_path = TaskGraphStore(ROOT / "artifacts" / "phase16" / "task_graphs").save(graph)
    checks["task_graph"] = {
        "ok": graph_path.exists(),
        "graph_id": graph.graph_id,
        "nodes": len(graph.nodes),
        "layers": len(graph.topological_layers()),
        "path": str(graph_path),
    }

    orchestrator = RoleAgentOrchestrator(MockReasoningBackend(), max_parallel=3)
    artifacts = await orchestrator.execute(graph, workspace_summary="Local architecture smoke test.")
    checks["role_agents"] = {
        "ok": graph.complete() and len(artifacts) == len(graph.nodes),
        "artifacts": len(artifacts),
        "roles": sorted({artifact.role.value for artifact in artifacts}),
        "average_confidence": round(sum(artifact.confidence for artifact in artifacts) / max(1, len(artifacts)), 4),
    }

    combined_artifact = "\n\n".join(artifact.content for artifact in artifacts)
    critic = HeuristicCriticBackend()
    critic_report = await critic.review(prompt=graph.objective, artifact=combined_artifact)
    verifier = CompositeVerifier()
    verification = await verifier.verify(
        artifact=combined_artifact,
        files={"main.py": "def add(a, b):\n    return a + b\n\nprint(add(2, 3))\n"},
    )
    checks["critic_verifier"] = {
        "ok": critic_report.confidence >= 0.70 and verification.score >= 0.70,
        "critic": critic_report.model_dump(),
        "verification": verification.model_dump(),
    }

    scorecard_path = latest_scorecard()
    memory = ExperienceMemoryStore(ROOT / "artifacts" / "phase16" / "experience_memory.jsonl")
    promoted = memory.promote_scorecard_failures(scorecard_path) if scorecard_path else []
    checks["experience_memory"] = {
        "ok": memory.path.exists(),
        "path": str(memory.path),
        "promoted": len(promoted),
        "search_hits": len(memory.search("model output security scorecard", limit=3)),
    }

    if scorecard_path:
        exporter = ScorecardFailureExporter(scorecard_path)
        export_summary = exporter.export(
            sft_path=ROOT / "artifacts" / "phase16" / "scorecard_failures_sft.jsonl",
            preference_path=ROOT / "artifacts" / "phase16" / "scorecard_failures_grpo.jsonl",
        )
    else:
        export_summary = {"sft_count": 0, "preference_count": 0, "reason": "no scorecard found"}
    checks["training_export"] = {
        "ok": bool(scorecard_path) and Path(export_summary["sft_path"]).exists() if export_summary.get("sft_path") else False,
        **export_summary,
    }

    tools = await ToolAdapterRegistry(ROOT).status()
    checks["tool_adapters"] = {
        "ok": True,
        "workspace": tools["workspace"],
        "probes": tools["probes"],
        "safety": tools["safety"],
    }

    model_probe = await probe_real_endpoint()
    checks["base_model_connector"] = {
        "ok": model_probe.configured and model_probe.reachable and model_probe.lineage_ok,
        "probe": model_probe.model_dump(),
        "note": "This check becomes green after a real Qwen/DeepSeek/Llama-compatible endpoint or checkpoint is configured.",
    }

    required_green = [
        "task_graph",
        "role_agents",
        "critic_verifier",
        "experience_memory",
        "training_export",
        "tool_adapters",
    ]
    passed = all(bool(checks[name]["ok"]) for name in required_green)
    report = {
        "run_id": f"phase16-{int(started)}",
        "passed": passed,
        "duration_ms": (time.time() - started) * 1000,
        "checks": checks,
        "required_green": required_green,
        "model_dependent": ["base_model_connector"],
    }
    output_dir = ROOT / "artifacts" / "phase16"
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "phase16-smoke.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    (output_dir / "phase16-smoke.md").write_text(render_markdown(report), encoding="utf-8")
    if checks["base_model_connector"]["ok"]:
        (output_dir / "phase16-smoke-real.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        (output_dir / "phase16-smoke-real.md").write_text(render_markdown(report), encoding="utf-8")
    return report


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Phase 16 Core Architecture Upgrades",
        "",
        f"- Passed: `{report['passed']}`",
        f"- Duration: `{report['duration_ms']:.1f} ms`",
        "",
        "| Check | OK | Detail |",
        "| --- | --- | --- |",
    ]
    for name, check in report["checks"].items():
        detail = ""
        if name == "task_graph":
            detail = f"{check['nodes']} nodes, {check['layers']} layers"
        elif name == "role_agents":
            detail = f"{check['artifacts']} artifacts, roles={check['roles']}"
        elif name == "critic_verifier":
            detail = check["critic"]["summary"]
        elif name == "experience_memory":
            detail = f"promoted={check['promoted']} search_hits={check['search_hits']}"
        elif name == "training_export":
            detail = f"sft={check.get('sft_count')} preference={check.get('preference_count')}"
        elif name == "base_model_connector":
            detail = check["probe"]["message"]
        elif name == "tool_adapters":
            detail = check["safety"]
        lines.append(f"| {name} | `{check.get('ok')}` | {str(detail).replace('|', '/')} |")
    lines.extend(
        [
            "",
            "## Safety",
            "",
            "- Tool adapters are defensive: static analysis, repository inspection, and documentation metadata fetches.",
            "- Autonomous exploit execution is not enabled.",
        ]
    )
    return "\n".join(lines) + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Phase 16 architecture smoke test.")
    parser.add_argument("--json", action="store_true", help="Print JSON summary.")
    return parser.parse_args()


async def async_main() -> int:
    args = parse_args()
    report = await run_smoke()
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print(f"passed={report['passed']} report=artifacts/phase16/phase16-smoke.md")
    return 0 if report["passed"] else 1


def main() -> None:
    raise SystemExit(asyncio.run(async_main()))


if __name__ == "__main__":
    main()
