#!/usr/bin/env python
"""Main agent v2: TaskGraph-first bridge for normal agent workflows."""

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
from src.phase20.taskgraph_runtime import TaskGraphAgentRuntime, TaskGraphRuntimeRequest
from src.phase25.experience_retrieval import ExperienceRetriever


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class MainAgentV2Request(StrictModel):
    prompt: str = Field(min_length=1)
    workspace: str
    run_verifier: bool = True
    run_semgrep: bool = False
    run_sandbox: bool = False
    retrieve_experience: bool = True
    use_model_critic: bool = False
    max_agent_nodes: int | None = Field(default=None, ge=1, le=12)


class MainAgentV2Report(StrictModel):
    run_id: str
    status: str
    prompt: str
    workspace: str
    experience_context: dict[str, Any]
    taskgraph_report: dict[str, Any]
    final_answer: str
    duration_ms: float
    created_at_unix: float


def build_backend_from_env() -> ModelBackend:
    return build_backend(
        os.environ.get("PHASE24_BACKEND", os.environ.get("PHASE11_BACKEND", "mock")),
        endpoint=os.environ.get("PHASE24_MODEL_ENDPOINT", os.environ.get("PHASE11_MODEL_ENDPOINT", "http://127.0.0.1:8016/v1")),
        model_name=os.environ.get("PHASE24_MODEL_NAME", os.environ.get("PHASE11_MODEL_NAME", "Qwen/Qwen2.5-Coder-0.5B-Instruct")),
        api_key=os.environ.get("PHASE24_API_KEY", os.environ.get("PHASE11_API_KEY")),
    )


class MainAgentV2:
    def __init__(self, backend: ModelBackend) -> None:
        self.backend = backend

    async def run(self, request: MainAgentV2Request) -> MainAgentV2Report:
        started = time.time()
        experience = {}
        enriched_prompt = request.prompt
        if request.retrieve_experience:
            retrieval = ExperienceRetriever().retrieve(request.prompt, limit=6)
            experience = retrieval.model_dump()
            if retrieval.records:
                enriched_prompt = (
                    f"{request.prompt}\n\n"
                    "Relevant past failure/fix/outcome memory:\n"
                    f"{retrieval.context_block}"
                )
        runtime = TaskGraphAgentRuntime(self.backend)
        report = await runtime.run(
            TaskGraphRuntimeRequest(
                prompt=enriched_prompt,
                workspace=request.workspace,
                run_verifier=request.run_verifier,
                run_semgrep=request.run_semgrep,
                run_sandbox=request.run_sandbox,
                use_model_critic=request.use_model_critic,
                max_agent_nodes=request.max_agent_nodes,
            )
        )
        return MainAgentV2Report(
            run_id=f"phase24-{report.run_id}",
            status=report.status,
            prompt=request.prompt,
            workspace=request.workspace,
            experience_context=experience,
            taskgraph_report=report.model_dump(),
            final_answer=report.final_answer,
            duration_ms=(time.time() - started) * 1000,
            created_at_unix=started,
        )


def write_report(report: MainAgentV2Report, output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / f"{report.run_id}.json"
    latest_path = output_dir / "main-agent-v2-latest.json"
    md_path = output_dir / f"{report.run_id}.md"
    json_path.write_text(json.dumps(report.model_dump(), indent=2, ensure_ascii=False), encoding="utf-8")
    latest_path.write_text(json.dumps(report.model_dump(), indent=2, ensure_ascii=False), encoding="utf-8")
    md_path.write_text(
        "\n".join(
            [
                "# Phase 24 Main Agent V2",
                "",
                f"- Run ID: `{report.run_id}`",
                f"- Status: `{report.status}`",
                f"- Duration ms: `{report.duration_ms:.1f}`",
                "",
                "## Final Answer",
                "",
                report.final_answer,
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return json_path, md_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Phase 24 main agent v2.")
    parser.add_argument("--prompt", default="Improve this AI architecture safely.")
    parser.add_argument("--workspace", default=str(ROOT))
    parser.add_argument("--output-dir", type=Path, default=ROOT / "artifacts" / "phase24")
    parser.add_argument("--run-semgrep", action="store_true")
    parser.add_argument("--run-sandbox", action="store_true")
    parser.add_argument("--no-verifier", action="store_true")
    parser.add_argument("--no-experience", action="store_true")
    parser.add_argument("--model-critic", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


async def async_main() -> int:
    args = parse_args()
    backend = build_backend_from_env()
    try:
        report = await MainAgentV2(backend).run(
            MainAgentV2Request(
                prompt=args.prompt,
                workspace=args.workspace,
                run_verifier=not args.no_verifier,
                run_semgrep=args.run_semgrep,
                run_sandbox=args.run_sandbox,
                retrieve_experience=not args.no_experience,
                use_model_critic=args.model_critic,
            )
        )
    finally:
        await backend.aclose()
    json_path, md_path = write_report(report, args.output_dir)
    print(json.dumps({"run_id": report.run_id, "status": report.status, "json": str(json_path), "markdown": str(md_path)}, indent=2))
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(async_main()))


if __name__ == "__main__":
    main()
