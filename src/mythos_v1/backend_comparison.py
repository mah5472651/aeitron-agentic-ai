#!/usr/bin/env python
"""Mock-control versus active real-backend quality comparison."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from argparse import Namespace
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.phase13.backend_quality_harness import run_comparison, write_reports


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class ActiveBackend(StrictModel):
    profile: str
    backend: str
    endpoint: str
    model: str
    requires_cuda: bool = False


class ProductComparisonReport(StrictModel):
    run_id: str
    status: str
    active_backend: ActiveBackend
    endpoint_available: bool
    candidate_ready: bool | None
    baseline_score: float | None
    candidate_score: float | None
    score_delta: float | None
    category_deltas: dict[str, float] = Field(default_factory=dict)
    recommendations: list[str] = Field(default_factory=list)
    phase13_report: str | None = None
    created_at_unix: float = Field(default_factory=time.time)


def load_active_backend(path: Path) -> ActiveBackend:
    payload = json.loads(path.read_text(encoding="utf-8"))
    profile = payload.get("profile") or {}
    env = payload.get("env") or {}
    return ActiveBackend(
        profile=str(profile.get("name") or "active-profile"),
        backend=str(profile.get("backend") or env.get("PHASE11_BACKEND") or "openai_compatible"),
        endpoint=str(profile.get("endpoint") or env.get("PHASE11_MODEL_ENDPOINT") or "http://127.0.0.1:8016/v1"),
        model=str(profile.get("model_name") or env.get("PHASE11_MODEL_NAME") or "security-coder"),
        requires_cuda=bool(profile.get("requires_cuda", False)),
    )


async def endpoint_available(backend: ActiveBackend) -> bool:
    if backend.backend != "openai_compatible":
        return True
    candidates = [f"{backend.endpoint.rstrip('/')}/models"]
    if backend.endpoint.rstrip("/").endswith("/v1"):
        candidates.append(f"{backend.endpoint.rstrip('/')[:-3]}/health")
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(4.0, connect=2.0)) as client:
            for url in candidates:
                try:
                    response = await client.get(url)
                    if response.status_code < 500:
                        return True
                except httpx.HTTPError:
                    continue
    except httpx.HTTPError:
        return False
    return False


async def compare(args: argparse.Namespace) -> ProductComparisonReport:
    active = load_active_backend(args.active_profile)
    available = await endpoint_available(active)
    if not available:
        return ProductComparisonReport(
            run_id=args.run_id,
            status="unavailable",
            active_backend=active,
            endpoint_available=False,
            candidate_ready=None,
            baseline_score=None,
            candidate_score=None,
            score_delta=None,
            recommendations=[
                "Start the active model endpoint, then rerun the identical mock-versus-real suite.",
                "Do not interpret endpoint unavailability as an architecture-quality failure.",
            ],
        )

    previous = {
        "PHASE13_CANDIDATE_ENDPOINT": os.environ.get("PHASE13_CANDIDATE_ENDPOINT"),
        "PHASE13_CANDIDATE_MODEL": os.environ.get("PHASE13_CANDIDATE_MODEL"),
    }
    os.environ["PHASE13_CANDIDATE_ENDPOINT"] = active.endpoint
    os.environ["PHASE13_CANDIDATE_MODEL"] = active.model
    phase13_args = Namespace(
        run_id=f"{args.run_id}-phase13",
        suite=args.suite,
        baseline_backend="mock",
        candidate_backend=active.backend,
        output_dir=args.output_dir / "phase13",
        export_tasks=ROOT / "data" / "phase13" / "backend_quality_tasks.jsonl",
        concurrency=args.concurrency,
        max_tasks=args.max_tasks,
        pass_score=args.pass_score,
        strict=False,
    )
    try:
        result = await run_comparison(phase13_args)
        phase13_json, _ = write_reports(result, phase13_args.output_dir)
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    return ProductComparisonReport(
        run_id=args.run_id,
        status="pass" if result.candidate_ready else "needs_improvement",
        active_backend=active,
        endpoint_available=True,
        candidate_ready=result.candidate_ready,
        baseline_score=result.baseline.overall_score,
        candidate_score=result.candidate.overall_score,
        score_delta=result.score_delta,
        category_deltas=result.category_deltas,
        recommendations=result.recommendations,
        phase13_report=str(phase13_json),
    )


def write_report(report: ProductComparisonReport, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "backend-comparison-latest.json"
    path.write_text(json.dumps(report.model_dump(), indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compare the architecture mock control with the active real backend.")
    parser.add_argument("--run-id", default=f"mythos-v1-backend-{int(time.time())}")
    parser.add_argument("--active-profile", type=Path, default=ROOT / "config" / "active_model_profile.json")
    parser.add_argument("--suite", choices=["quick", "full"], default="quick")
    parser.add_argument("--max-tasks", type=int, default=7)
    parser.add_argument("--concurrency", type=int, default=2)
    parser.add_argument("--pass-score", type=float, default=80.0)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "artifacts" / "mythos_v1")
    parser.add_argument("--require-available", action="store_true")
    parser.add_argument("--strict", action="store_true")
    return parser.parse_args()


async def async_main() -> int:
    args = parse_args()
    report = await compare(args)
    path = write_report(report, args.output_dir)
    print(json.dumps({**report.model_dump(), "report": str(path)}, indent=2, ensure_ascii=False))
    failed = args.require_available and not report.endpoint_available
    failed = failed or (args.strict and report.endpoint_available and not report.candidate_ready)
    return 1 if failed else 0


def main() -> None:
    raise SystemExit(asyncio.run(async_main()))


if __name__ == "__main__":
    main()

