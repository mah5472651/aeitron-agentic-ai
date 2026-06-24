#!/usr/bin/env python
"""Shared schemas for the Phase 9 evaluation harness."""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any, Literal


BenchmarkName = Literal["humaneval", "mbpp", "cyberseceval2", "custom_security", "head_to_head"]


@dataclass(frozen=True)
class Generation:
    text: str
    model: str
    latency_ms: float
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class EvalSample:
    sample_id: str
    prompt: str
    tests: str = ""
    entry_point: str | None = None
    category: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SampleResult:
    sample_id: str
    benchmark: str
    passed: bool
    score: float
    category: str | None = None
    output: str = ""
    stderr: str = ""
    exit_code: int | None = None
    latency_ms: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class BenchmarkResult:
    run_id: str
    benchmark: str
    score: float
    metrics: dict[str, float]
    sample_results: list[SampleResult]
    timestamp: float = field(default_factory=time.time)

    def to_record(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["passed"] = sum(1 for item in self.sample_results if item.passed)
        payload["total"] = len(self.sample_results)
        return payload


@dataclass(frozen=True)
class HeadToHeadResult:
    sample_id: str
    prompt: str
    model_a: str
    model_b: str
    winner: Literal["model_a", "model_b", "tie"]
    scores: dict[str, float]
    rationale: str

