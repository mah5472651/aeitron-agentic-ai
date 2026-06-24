#!/usr/bin/env python
"""CLI entrypoint for Phase 9 automated model evaluation."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from dataclasses import asdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.phase9.benchmarks import (
    CustomSecurityRunner,
    StandardBenchmarkRunner,
    load_cyberseceval2,
    load_humaneval,
    load_mbpp,
)
from src.phase9.head_to_head import HeadToHeadRunner
from src.phase9.model_client import JsonlReplayClient, LLMJudge, OpenAICompatibleClient
from src.phase9.models import BenchmarkResult, EvalSample
from src.phase9.regression_tracker import RegressionTracker
from src.phase9.sandbox_adapter import SandboxBenchmarkRunner


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Phase 9 coding/security LLM evaluation harness.")
    parser.add_argument("--run-id", default=f"eval-{int(time.time())}")
    parser.add_argument("--endpoint", default="http://localhost:8080/v1")
    parser.add_argument("--model", default="security-coder")
    parser.add_argument("--api-key")
    parser.add_argument("--replay-jsonl", type=Path)
    parser.add_argument("--benchmarks", nargs="+", default=["humaneval", "mbpp", "cyberseceval2", "custom_security"])
    parser.add_argument("--humaneval-jsonl", type=Path)
    parser.add_argument("--mbpp-jsonl", type=Path)
    parser.add_argument("--cyberseceval2-jsonl", type=Path)
    parser.add_argument("--sandbox-image", default="python:3.12-slim")
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument("--postgres-dsn")
    parser.add_argument("--jsonl-results", type=Path, default=Path("artifacts/phase9/evaluation_runs.jsonl"))
    parser.add_argument("--report-out", type=Path, default=Path("artifacts/phase9/evaluation_report.md"))
    parser.add_argument("--alert-webhook")
    parser.add_argument("--regression-threshold", type=float, default=0.02)
    parser.add_argument("--init-db", action="store_true")

    parser.add_argument("--head-to-head", action="store_true")
    parser.add_argument("--model-a-endpoint")
    parser.add_argument("--model-a")
    parser.add_argument("--model-b-endpoint")
    parser.add_argument("--model-b")
    parser.add_argument("--judge-endpoint")
    parser.add_argument("--judge-model")
    parser.add_argument("--head-to-head-prompts", type=Path)
    parser.add_argument("--head-to-head-out", type=Path, default=Path("artifacts/phase9/head_to_head.jsonl"))
    return parser.parse_args()


def build_client(args: argparse.Namespace):
    if args.replay_jsonl:
        return JsonlReplayClient(args.replay_jsonl, model=args.model)
    return OpenAICompatibleClient(args.endpoint, args.model, api_key=args.api_key)


async def run_standard(args: argparse.Namespace) -> list[BenchmarkResult]:
    model_client = build_client(args)
    results: list[BenchmarkResult] = []
    try:
        sandboxed_benchmarks = {"humaneval", "mbpp"} & set(args.benchmarks)
        if sandboxed_benchmarks:
            async with SandboxBenchmarkRunner(image=args.sandbox_image, pool_size=args.concurrency) as sandbox:
                runner = StandardBenchmarkRunner(model_client, sandbox, args.run_id, concurrency=args.concurrency)
                if "humaneval" in args.benchmarks:
                    results.append(await runner.run_humaneval(load_humaneval(args.humaneval_jsonl), completions_per_prompt=10))
                if "mbpp" in args.benchmarks:
                    results.append(await runner.run_mbpp(load_mbpp(args.mbpp_jsonl)))
        if "cyberseceval2" in args.benchmarks:
            if not args.cyberseceval2_jsonl:
                raise SystemExit("--cyberseceval2-jsonl is required for CyberSecEval 2 in this offline-safe harness")
            runner_nosandbox = StandardBenchmarkRunner(model_client, None, args.run_id, concurrency=args.concurrency)
            results.append(await runner_nosandbox.run_cyberseceval2(load_cyberseceval2(args.cyberseceval2_jsonl)))
        if "custom_security" in args.benchmarks:
            results.append(await CustomSecurityRunner(model_client, args.run_id, concurrency=args.concurrency).run())
    finally:
        close = getattr(model_client, "aclose", None)
        if close:
            await close()
    return results


def load_prompts(path: Path) -> list[EvalSample]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return [
        EvalSample(
            sample_id=str(row.get("id") or row.get("sample_id") or index),
            prompt=str(row.get("prompt") or row.get("input") or row.get("instruction")),
            metadata=row,
        )
        for index, row in enumerate(rows)
    ]


async def run_head_to_head(args: argparse.Namespace) -> None:
    if not all([args.model_a_endpoint, args.model_a, args.model_b_endpoint, args.model_b, args.judge_endpoint, args.judge_model, args.head_to_head_prompts]):
        raise SystemExit("head-to-head requires model A/B endpoints, model names, judge endpoint/model, and prompts JSONL")
    model_a = OpenAICompatibleClient(args.model_a_endpoint, args.model_a, api_key=args.api_key)
    model_b = OpenAICompatibleClient(args.model_b_endpoint, args.model_b, api_key=args.api_key)
    judge_client = OpenAICompatibleClient(args.judge_endpoint, args.judge_model, api_key=args.api_key)
    try:
        runner = HeadToHeadRunner(model_a, model_b, LLMJudge(judge_client), concurrency=args.concurrency)
        results = await runner.compare(load_prompts(args.head_to_head_prompts))
        args.head_to_head_out.parent.mkdir(parents=True, exist_ok=True)
        with args.head_to_head_out.open("w", encoding="utf-8") as handle:
            for result in results:
                handle.write(json.dumps(asdict(result), ensure_ascii=False) + "\n")
    finally:
        await model_a.aclose()
        await model_b.aclose()
        await judge_client.aclose()


async def async_main() -> None:
    args = parse_args()
    if args.head_to_head:
        await run_head_to_head(args)
        return

    tracker = RegressionTracker(args.postgres_dsn, args.jsonl_results, args.alert_webhook, args.regression_threshold)
    if args.init_db:
        await tracker.init_db()
    results = await run_standard(args)
    for result in results:
        await tracker.save_result(result)
    await tracker.write_markdown_report(results, args.report_out)
    print(json.dumps({"run_id": args.run_id, "benchmarks": [result.to_record() for result in results], "report": str(args.report_out)}, indent=2))


def main() -> None:
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
