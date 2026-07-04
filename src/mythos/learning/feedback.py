"""Benchmark feedback loop for dataset quality and task promotion."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from pydantic import Field

from src.mythos.shared.schemas import StrictModel


class FeedbackRecommendation(StrictModel):
    kind: str
    severity: str
    message: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class BenchmarkFeedbackReport(StrictModel):
    benchmark_score: float
    benchmark_total: int
    benchmark_passed: int
    task_approval_rate: float
    avg_quality_score: float
    recommendations: list[FeedbackRecommendation] = Field(default_factory=list)


def load_json(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def build_feedback_report(
    *,
    benchmark_report_path: str | Path | None = None,
    quality_report_path: str | Path | None = None,
    review_report_path: str | Path | None = None,
) -> BenchmarkFeedbackReport:
    benchmark = load_json(benchmark_report_path) if benchmark_report_path else {}
    quality = load_json(quality_report_path) if quality_report_path else {}
    review = load_json(review_report_path) if review_report_path else {}
    benchmark_score = float(benchmark.get("score", 0.0))
    benchmark_total = int(benchmark.get("total", 0))
    benchmark_passed = int(benchmark.get("passed", 0))
    avg_quality_score = float(quality.get("avg_quality_score", 0.0))
    review_total = int(review.get("total", 0))
    review_approved = int(review.get("approved", 0))
    task_approval_rate = review_approved / max(1, review_total)

    recommendations: list[FeedbackRecommendation] = []
    if avg_quality_score < 0.65:
        recommendations.append(
            FeedbackRecommendation(
                kind="quality_gate",
                severity="high",
                message="Average quality score is low; tighten source allowlist and minimum quality thresholds.",
                metadata={"avg_quality_score": avg_quality_score},
            )
        )
    if review_total and task_approval_rate < 0.5:
        recommendations.append(
            FeedbackRecommendation(
                kind="task_extraction",
                severity="high",
                message="Task approval rate is low; improve task extraction prompts and reject noisy sources.",
                metadata={"task_approval_rate": task_approval_rate},
            )
        )
    if benchmark_total and benchmark_score < 0.75:
        recommendations.append(
            FeedbackRecommendation(
                kind="benchmark",
                severity="critical",
                message="Benchmark score is below promotion threshold; do not promote this dataset version to training.",
                metadata={"benchmark_score": benchmark_score, "benchmark_total": benchmark_total},
            )
        )
    if not recommendations:
        recommendations.append(
            FeedbackRecommendation(
                kind="promotion",
                severity="info",
                message="Dataset version passes current feedback gates and can be queued for training/evaluation promotion.",
            )
        )
    return BenchmarkFeedbackReport(
        benchmark_score=benchmark_score,
        benchmark_total=benchmark_total,
        benchmark_passed=benchmark_passed,
        task_approval_rate=round(task_approval_rate, 6),
        avg_quality_score=avg_quality_score,
        recommendations=recommendations,
    )


def write_feedback_report(
    *,
    output_path: str | Path,
    benchmark_report_path: str | Path | None = None,
    quality_report_path: str | Path | None = None,
    review_report_path: str | Path | None = None,
) -> BenchmarkFeedbackReport:
    report = build_feedback_report(
        benchmark_report_path=benchmark_report_path,
        quality_report_path=quality_report_path,
        review_report_path=review_report_path,
    )
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report.model_dump(), indent=2, sort_keys=True), encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Build Mythos benchmark/data feedback report.")
    parser.add_argument("--output", required=True)
    parser.add_argument("--benchmark-report")
    parser.add_argument("--quality-report")
    parser.add_argument("--review-report")
    args = parser.parse_args()
    report = write_feedback_report(
        output_path=args.output,
        benchmark_report_path=args.benchmark_report,
        quality_report_path=args.quality_report,
        review_report_path=args.review_report,
    )
    print(json.dumps(report.model_dump(), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
