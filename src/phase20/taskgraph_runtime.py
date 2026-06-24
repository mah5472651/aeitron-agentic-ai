#!/usr/bin/env python
"""TaskGraph-first runtime that composes planner, role agents, critic, and verifier."""

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

from src.phase11.memory_engine import WorkspaceMemoryEngine, safe_workspace
from src.phase11.model_backends import ModelBackend, build_backend
from src.phase16.critic_verifier import CriticReport, HeuristicCriticBackend, ModelCriticBackend
from src.phase16.role_agents import AgentArtifact, RoleAgentOrchestrator
from src.phase16.task_graph import TaskGraph, TaskGraphPlanner, TaskGraphStore
from src.phase19.verifier_registry import VerificationReport, VerifierPolicy, VerifierRegistry


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class TaskGraphRuntimeRequest(StrictModel):
    prompt: str = Field(min_length=1)
    workspace: str
    max_parallel_agents: int = Field(default=2, ge=1, le=8)
    run_verifier: bool = True
    run_semgrep: bool = False
    run_sandbox: bool = False
    use_model_critic: bool = False
    max_agent_nodes: int | None = Field(default=None, ge=1, le=12)
    metadata: dict[str, Any] = Field(default_factory=dict)


class TaskGraphRuntimeReport(StrictModel):
    run_id: str
    prompt: str
    workspace: str
    status: str
    graph: dict[str, Any]
    graph_path: str
    artifacts: list[dict[str, Any]]
    critic: dict[str, Any]
    verifier: dict[str, Any] | None = None
    final_answer: str
    duration_ms: float
    created_at_unix: float


class TaskGraphAgentRuntime:
    def __init__(self, backend: ModelBackend) -> None:
        self.backend = backend
        self.planner = TaskGraphPlanner()
        self.store = TaskGraphStore(ROOT / "artifacts" / "phase20" / "task_graphs")

    async def run(self, request: TaskGraphRuntimeRequest) -> TaskGraphRuntimeReport:
        started = time.time()
        workspace = safe_workspace(request.workspace)
        memory = WorkspaceMemoryEngine(workspace)
        context = memory.retrieve(request.prompt, token_budget=8000, max_items=12)
        workspace_summary = self._workspace_summary(context.items)
        graph = self.planner.plan(request.prompt, workspace_summary=workspace_summary)
        if request.max_agent_nodes:
            graph = self._prune_graph(graph, request.max_agent_nodes)
        graph_path = self.store.save(graph)

        orchestrator = RoleAgentOrchestrator(self.backend, max_parallel=request.max_parallel_agents)
        artifacts = await orchestrator.execute(graph, workspace_summary=workspace_summary)
        combined = "\n\n".join(f"## {artifact.role.value}\n{artifact.content}" for artifact in artifacts)
        critic = await self._critic(request, combined, workspace_summary)
        verifier_report: VerificationReport | None = None
        if request.run_verifier:
            policy = VerifierPolicy(
                run_semgrep=request.run_semgrep,
                run_sandbox=request.run_sandbox,
                run_codeql=False,
                fail_on_medium=False,
            )
            verifier_report = await VerifierRegistry(policy).run(workspace, run_id=f"phase20-verifier-{graph.graph_id}")

        ok = critic.ok and (verifier_report is None or verifier_report.status != "fail")
        final_answer = self._final_answer(graph, artifacts, critic, verifier_report)
        return TaskGraphRuntimeReport(
            run_id=f"phase20-{graph.graph_id}",
            prompt=request.prompt,
            workspace=str(workspace),
            status="complete" if ok else "needs_attention",
            graph=graph.model_dump(),
            graph_path=str(graph_path),
            artifacts=[artifact.model_dump() for artifact in artifacts],
            critic=critic.model_dump(),
            verifier=verifier_report.model_dump() if verifier_report else None,
            final_answer=final_answer,
            duration_ms=(time.time() - started) * 1000,
            created_at_unix=started,
        )

    async def _critic(self, request: TaskGraphRuntimeRequest, artifact: str, context: str) -> CriticReport:
        if request.use_model_critic:
            return await ModelCriticBackend(self.backend).review(prompt=request.prompt, artifact=artifact, context=context)
        return await HeuristicCriticBackend().review(prompt=request.prompt, artifact=artifact, context=context)

    def _workspace_summary(self, items: list[Any]) -> str:
        if not items:
            return "No indexed workspace context found."
        parts = []
        for item in items[:10]:
            parts.append(f"{item.source}: {item.title} score={item.score:.2f}\n{item.content[:900]}")
        return "\n\n".join(parts)

    def _prune_graph(self, graph: TaskGraph, max_nodes: int) -> TaskGraph:
        ordered = [node for layer in graph.topological_layers() for node in layer]
        selected_ids = {node.task_id for node in ordered[:max_nodes]}
        nodes = {}
        for node in ordered:
            if node.task_id not in selected_ids:
                continue
            updated = node.model_copy(update={"dependencies": [dep for dep in node.dependencies if dep in selected_ids]})
            nodes[node.task_id] = updated
        return TaskGraph(
            graph_id=graph.graph_id,
            objective=graph.objective,
            nodes=nodes,
            created_at_ms=graph.created_at_ms,
            metadata={**graph.metadata, "pruned_to_max_agent_nodes": max_nodes},
        )

    def _final_answer(
        self,
        graph: TaskGraph,
        artifacts: list[AgentArtifact],
        critic: CriticReport,
        verifier: VerificationReport | None,
    ) -> str:
        lines = [
            f"TaskGraph `{graph.graph_id}` completed with {len(graph.nodes)} nodes.",
            f"Critic: {critic.summary}",
        ]
        if verifier:
            lines.append(f"Verifier: {verifier.status} score={verifier.score:.2f}; {verifier.recommendation}")
        lines.append("")
        lines.append("Role outputs:")
        for artifact in artifacts:
            lines.append(f"- {artifact.role.value}: confidence={artifact.confidence:.2f}; {artifact.content[:260].replace(chr(10), ' ')}")
        return "\n".join(lines)


def build_backend_from_env() -> ModelBackend:
    return build_backend(
        os.environ.get("PHASE20_BACKEND", os.environ.get("PHASE11_BACKEND", "mock")),
        endpoint=os.environ.get("PHASE20_MODEL_ENDPOINT", os.environ.get("PHASE11_MODEL_ENDPOINT", "http://127.0.0.1:8016/v1")),
        model_name=os.environ.get("PHASE20_MODEL_NAME", os.environ.get("PHASE11_MODEL_NAME", "Qwen/Qwen2.5-Coder-0.5B-Instruct")),
        api_key=os.environ.get("PHASE20_API_KEY", os.environ.get("PHASE11_API_KEY")),
    )


def write_report(report: TaskGraphRuntimeReport, output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / f"{report.run_id}.json"
    latest_path = output_dir / "taskgraph-runtime-latest.json"
    md_path = output_dir / f"{report.run_id}.md"
    payload = report.model_dump()
    json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    latest_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    lines = [
        "# Phase 20 TaskGraph Runtime",
        "",
        f"- Run ID: `{report.run_id}`",
        f"- Status: `{report.status}`",
        f"- Graph path: `{report.graph_path}`",
        f"- Artifact count: `{len(report.artifacts)}`",
        "",
        "## Final Answer",
        "",
        report.final_answer,
    ]
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, md_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Phase 20 TaskGraph-first runtime.")
    parser.add_argument("--prompt", default="Build a safe coding plan and verification path for this repository.")
    parser.add_argument("--workspace", default=str(ROOT))
    parser.add_argument("--output-dir", type=Path, default=ROOT / "artifacts" / "phase20")
    parser.add_argument("--run-verifier", action="store_true")
    parser.add_argument("--run-semgrep", action="store_true")
    parser.add_argument("--run-sandbox", action="store_true")
    parser.add_argument("--model-critic", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--strict", action="store_true")
    return parser.parse_args()


async def async_main() -> int:
    args = parse_args()
    backend = build_backend_from_env()
    try:
        report = await TaskGraphAgentRuntime(backend).run(
            TaskGraphRuntimeRequest(
                prompt=args.prompt,
                workspace=args.workspace,
                run_verifier=args.run_verifier,
                run_semgrep=args.run_semgrep,
                run_sandbox=args.run_sandbox,
                use_model_critic=args.model_critic,
            )
        )
    finally:
        await backend.aclose()
    json_path, md_path = write_report(report, args.output_dir)
    if args.json:
        print(json.dumps({"run_id": report.run_id, "status": report.status, "json": str(json_path), "markdown": str(md_path)}, indent=2))
    else:
        print(f"{report.run_id}: {report.status} -> {json_path}")
    return 1 if args.strict and report.status != "complete" else 0


def main() -> None:
    raise SystemExit(asyncio.run(async_main()))


if __name__ == "__main__":
    main()
