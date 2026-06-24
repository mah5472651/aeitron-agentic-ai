#!/usr/bin/env python
"""Benchmark runners for HumanEval, MBPP, CyberSecEval 2, and custom security."""

from __future__ import annotations

import asyncio
import json
import math
import os
from collections import defaultdict
from pathlib import Path
from typing import Any

from src.phase9.model_client import BaseModelClient
from src.phase9.models import BenchmarkResult, EvalSample, SampleResult
from src.phase9.sandbox_adapter import SandboxBenchmarkRunner
from src.phase9.security_suite import build_security_suite, insecure_code_detected, score_security_case


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def pinned_dataset_revision() -> str:
    revision = os.environ.get("HF_DATASET_REVISION")
    if not revision:
        raise RuntimeError("Set HF_DATASET_REVISION to an exact dataset commit, or provide a local benchmark JSONL.")
    return revision


def load_humaneval(path: Path | None = None) -> list[EvalSample]:
    if path is None:
        try:
            from datasets import load_dataset

            data = load_dataset("openai_humaneval", split="test", revision=pinned_dataset_revision())
            rows = list(data)
        except Exception as exc:
            raise RuntimeError("Provide --humaneval-jsonl or install datasets with openai_humaneval access.") from exc
    else:
        rows = read_jsonl(path)
    samples: list[EvalSample] = []
    for row in rows:
        samples.append(
            EvalSample(
                sample_id=str(row.get("task_id") or row.get("id")),
                prompt=str(row["prompt"]),
                tests=str(row.get("test") or row.get("tests") or ""),
                entry_point=str(row.get("entry_point") or ""),
                metadata=row,
            )
        )
    return samples


def load_mbpp(path: Path | None = None) -> list[EvalSample]:
    if path is None:
        try:
            from datasets import load_dataset

            data = load_dataset("mbpp", "sanitized", split="test", revision=pinned_dataset_revision())
            rows = list(data)
        except Exception as exc:
            raise RuntimeError("Provide --mbpp-jsonl or install datasets with MBPP access.") from exc
    else:
        rows = read_jsonl(path)
    samples: list[EvalSample] = []
    for row in rows:
        test_list = row.get("test_list") or row.get("tests") or []
        tests = "\n".join(test_list) if isinstance(test_list, list) else str(test_list)
        prompt = row.get("prompt") or row.get("text") or row.get("question")
        samples.append(EvalSample(sample_id=str(row.get("task_id") or row.get("id")), prompt=str(prompt), tests=tests, metadata=row))
    return samples


def load_cyberseceval2(path: Path) -> list[EvalSample]:
    rows = read_jsonl(path)
    samples: list[EvalSample] = []
    for index, row in enumerate(rows):
        prompt = row.get("prompt") or row.get("input") or row.get("instruction")
        samples.append(
            EvalSample(
                sample_id=str(row.get("id") or row.get("task_id") or f"cyberseceval2-{index}"),
                prompt=str(prompt),
                category=str(row.get("category") or row.get("test_type") or "security"),
                metadata=row,
            )
        )
    return samples


def pass_at_k(total: int, correct: int, k: int) -> float:
    """Unbiased estimator of pass@k from Chen et al. 2021."""

    if total <= 0 or correct <= 0:
        return 0.0
    if correct == total:
        return 1.0
    if total < k:
        return 1.0 - math.comb(total - correct, total) / math.comb(total, total)
    return 1.0 - math.comb(total - correct, k) / math.comb(total, k)


def build_humaneval_program(sample: EvalSample, completion: str) -> str:
    return f"{sample.prompt}{completion}\n\n{sample.tests}\n\ncheck({sample.entry_point})\n"


def build_mbpp_program(sample: EvalSample, completion: str) -> str:
    return f"{completion}\n\n{sample.tests}\n"


class StandardBenchmarkRunner:
    def __init__(
        self,
        model_client: BaseModelClient,
        sandbox: SandboxBenchmarkRunner | None,
        run_id: str,
        concurrency: int = 8,
    ) -> None:
        self.model_client = model_client
        self.sandbox = sandbox
        self.run_id = run_id
        self.semaphore = asyncio.Semaphore(concurrency)

    async def run_humaneval(self, samples: list[EvalSample], completions_per_prompt: int = 10) -> BenchmarkResult:
        if self.sandbox is None:
            raise RuntimeError("HumanEval requires a sandbox runner.")
        results: list[SampleResult] = []

        async def one(sample: EvalSample) -> SampleResult:
            async with self.semaphore:
                generations = await self.model_client.generate(sample.prompt, n=completions_per_prompt, temperature=0.2, max_tokens=768)
                outcomes = []
                for index, generation in enumerate(generations):
                    code = build_humaneval_program(sample, generation.text)
                    ok, exit_code, stdout, stderr, latency_ms = await self.sandbox.run_python(code, request_id=f"{self.run_id}-{sample.sample_id}-{index}")
                    outcomes.append((ok, exit_code, stdout, stderr, latency_ms))
                correct = sum(1 for ok, *_ in outcomes if ok)
                return SampleResult(
                    sample_id=sample.sample_id,
                    benchmark="humaneval",
                    passed=bool(outcomes and outcomes[0][0]),
                    score=pass_at_k(len(outcomes), correct, 1),
                    exit_code=outcomes[0][1] if outcomes else None,
                    stderr=outcomes[0][3] if outcomes else "",
                    latency_ms=outcomes[0][4] if outcomes else 0.0,
                    metadata={"pass@10": pass_at_k(len(outcomes), correct, 10), "correct": correct, "n": len(outcomes)},
                )

        results = await asyncio.gather(*(one(sample) for sample in samples))
        pass1 = sum(result.score for result in results) / max(1, len(results))
        pass10 = sum(float(result.metadata["pass@10"]) for result in results) / max(1, len(results))
        return BenchmarkResult(self.run_id, "humaneval", pass1, {"pass@1": pass1, "pass@10": pass10}, list(results))

    async def run_mbpp(self, samples: list[EvalSample]) -> BenchmarkResult:
        if self.sandbox is None:
            raise RuntimeError("MBPP requires a sandbox runner.")

        async def one(sample: EvalSample) -> SampleResult:
            async with self.semaphore:
                generation = (await self.model_client.generate(sample.prompt, n=1, temperature=0.2, max_tokens=768))[0]
                ok, exit_code, stdout, stderr, latency_ms = await self.sandbox.run_python(build_mbpp_program(sample, generation.text), request_id=f"{self.run_id}-{sample.sample_id}")
                return SampleResult(sample.sample_id, "mbpp", ok, 1.0 if ok else 0.0, exit_code=exit_code, output=stdout, stderr=stderr, latency_ms=latency_ms)

        results = await asyncio.gather(*(one(sample) for sample in samples))
        score = sum(result.score for result in results) / max(1, len(results))
        return BenchmarkResult(self.run_id, "mbpp", score, {"pass@1": score}, list(results))

    async def run_cyberseceval2(self, samples: list[EvalSample]) -> BenchmarkResult:
        async def one(sample: EvalSample) -> SampleResult:
            async with self.semaphore:
                generation = (await self.model_client.generate(sample.prompt, n=1, temperature=0.1, max_tokens=768))[0]
                insecure = insecure_code_detected(generation.text)
                return SampleResult(
                    sample_id=sample.sample_id,
                    benchmark="cyberseceval2",
                    passed=not insecure,
                    score=0.0 if insecure else 1.0,
                    category=sample.category,
                    output=generation.text,
                    latency_ms=generation.latency_ms,
                    metadata={"insecure_code": insecure},
                )

        results = await asyncio.gather(*(one(sample) for sample in samples))
        secure_rate = sum(result.score for result in results) / max(1, len(results))
        insecure_rate = 1.0 - secure_rate
        return BenchmarkResult(self.run_id, "cyberseceval2", secure_rate, {"secure_rate": secure_rate, "insecure_code_rate": insecure_rate}, list(results))


class CustomSecurityRunner:
    def __init__(self, model_client: BaseModelClient, run_id: str, concurrency: int = 8) -> None:
        self.model_client = model_client
        self.run_id = run_id
        self.semaphore = asyncio.Semaphore(concurrency)

    async def run(self) -> BenchmarkResult:
        cases = build_security_suite()

        async def one(case: Any) -> SampleResult:
            async with self.semaphore:
                generation = (await self.model_client.generate(case.prompt, n=1, temperature=0.1, max_tokens=768))[0]
                passed, score, checks = score_security_case(case, generation.text)
                return SampleResult(
                    sample_id=case.case_id,
                    benchmark="custom_security",
                    passed=passed,
                    score=score,
                    category=case.category,
                    output=generation.text,
                    latency_ms=generation.latency_ms,
                    metadata=checks,
                )

        results = await asyncio.gather(*(one(case) for case in cases))
        by_category: dict[str, list[float]] = defaultdict(list)
        for result in results:
            by_category[str(result.category)].append(result.score)
        metrics = {category: sum(scores) / max(1, len(scores)) for category, scores in by_category.items()}
        score = sum(result.score for result in results) / max(1, len(results))
        metrics["overall"] = score
        return BenchmarkResult(self.run_id, "custom_security", score, metrics, list(results))
