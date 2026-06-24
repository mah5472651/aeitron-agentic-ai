#!/usr/bin/env python
"""Configurable critic backend for coding/security artifacts."""

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
from src.phase16.critic_verifier import CriticReport, HeuristicCriticBackend, ModelCriticBackend


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class CriticServiceReport(StrictModel):
    run_id: str
    mode: str
    backend: str | None = None
    model: str | None = None
    review: dict[str, Any]
    status: str
    duration_ms: float
    created_at_unix: float = Field(default_factory=time.time)


def build_model_backend_from_env() -> ModelBackend:
    return build_backend(
        os.environ.get("PHASE22_BACKEND", "openai_compatible"),
        endpoint=os.environ.get("PHASE22_MODEL_ENDPOINT", os.environ.get("PHASE11_MODEL_ENDPOINT", "http://127.0.0.1:8016/v1")),
        model_name=os.environ.get("PHASE22_MODEL_NAME", os.environ.get("PHASE11_MODEL_NAME", "Qwen/Qwen2.5-Coder-0.5B-Instruct")),
        api_key=os.environ.get("PHASE22_API_KEY", os.environ.get("PHASE11_API_KEY")),
    )


async def review_artifact(
    *,
    prompt: str,
    artifact: str,
    context: str = "",
    mode: str = "heuristic",
    backend: ModelBackend | None = None,
) -> CriticReport:
    if mode == "model":
        owned_backend = backend is None
        active = backend or build_model_backend_from_env()
        try:
            return await ModelCriticBackend(active).review(prompt=prompt, artifact=artifact, context=context)
        finally:
            if owned_backend:
                await active.aclose()
    return await HeuristicCriticBackend().review(prompt=prompt, artifact=artifact, context=context)


def write_report(report: CriticServiceReport, output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / f"{report.run_id}.json"
    latest_path = output_dir / "critic-latest.json"
    md_path = output_dir / f"{report.run_id}.md"
    json_path.write_text(json.dumps(report.model_dump(), indent=2, ensure_ascii=False), encoding="utf-8")
    latest_path.write_text(json.dumps(report.model_dump(), indent=2, ensure_ascii=False), encoding="utf-8")
    review = report.review
    lines = [
        "# Phase 22 Critic Service",
        "",
        f"- Run ID: `{report.run_id}`",
        f"- Mode: `{report.mode}`",
        f"- Status: `{report.status}`",
        f"- Confidence: `{review.get('confidence')}`",
        f"- Summary: {str(review.get('summary', ''))[:1000]}",
    ]
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, md_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Phase 22 critic backend.")
    parser.add_argument("--run-id", default=f"phase22-{int(time.time())}")
    parser.add_argument("--mode", choices=["heuristic", "model"], default="heuristic")
    parser.add_argument("--prompt", default="Review this coding/security artifact.")
    parser.add_argument(
        "--artifact",
        default=(
            "Plan: inspect target files, implement minimal code changes, and document risks. "
            "Verification: run unit tests, sandbox smoke checks, and static security review. "
            "Security: validate inputs, avoid shell execution, keep secrets out of code, and add regression tests."
        ),
    )
    parser.add_argument("--context", default="")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "artifacts" / "phase22")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--strict", action="store_true")
    return parser.parse_args()


async def async_main() -> int:
    args = parse_args()
    started = time.time()
    review = await review_artifact(prompt=args.prompt, artifact=args.artifact, context=args.context, mode=args.mode)
    report = CriticServiceReport(
        run_id=args.run_id,
        mode=args.mode,
        backend=review.metadata.get("backend"),
        model=review.metadata.get("model"),
        review=review.model_dump(),
        status="complete" if review.ok else "needs_revision",
        duration_ms=(time.time() - started) * 1000,
    )
    json_path, md_path = write_report(report, args.output_dir)
    print(json.dumps({"run_id": report.run_id, "status": report.status, "confidence": review.confidence, "json": str(json_path), "markdown": str(md_path)}, indent=2))
    return 1 if args.strict and not review.ok else 0


def main() -> None:
    raise SystemExit(asyncio.run(async_main()))


if __name__ == "__main__":
    main()
