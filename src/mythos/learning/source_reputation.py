"""Source reputation scoring for production data acquisition."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

from pydantic import Field

from src.mythos.shared.schemas import StrictModel


class SourceReputationScore(StrictModel):
    source: str
    rows: int
    avg_quality_score: float = Field(ge=0.0, le=1.0)
    task_coverage: float = Field(ge=0.0, le=1.0)
    security_coverage: float = Field(ge=0.0, le=1.0)
    code_coverage: float = Field(ge=0.0, le=1.0)
    review_approval_rate: float = Field(ge=0.0, le=1.0)
    duplicate_rate: float = Field(ge=0.0, le=1.0)
    contamination_rate: float = Field(ge=0.0, le=1.0)
    license_trust: float = Field(ge=0.0, le=1.0)
    benchmark_feedback_score: float = Field(ge=0.0, le=1.0)
    reputation_score: float = Field(ge=0.0, le=1.0)
    action: str
    reasons: list[str] = Field(default_factory=list)


class SourceReputationReport(StrictModel):
    sources: list[SourceReputationScore]
    created_at_unix: float = Field(default_factory=time.time)


def _load_json(path: str | Path | None) -> dict[str, Any]:
    if not path:
        return {}
    source = Path(path)
    if not source.exists():
        return {}
    return json.loads(source.read_text(encoding="utf-8"))


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


def _license_trust(source: str, source_quality: dict[str, Any]) -> float:
    lowered = source.lower()
    if any(token in lowered for token in ("nvd", "cisa", "mitre", "owasp", "osv", "github-advisory", "cwe", "capec", "rustsec")):
        return 1.0
    if source_quality.get("avg_quality_score", 0.0) >= 0.65:
        return 0.85
    return 0.7


def build_source_reputation_report(
    *,
    source_quality_report_path: str | Path,
    task_report_path: str | Path | None = None,
    review_report_path: str | Path | None = None,
    feedback_report_path: str | Path | None = None,
    contamination_report_path: str | Path | None = None,
    dedup_report_path: str | Path | None = None,
) -> SourceReputationReport:
    source_quality = _load_json(source_quality_report_path)
    task_report = _load_json(task_report_path)
    review_report = _load_json(review_report_path)
    feedback_report = _load_json(feedback_report_path)
    contamination_report = _load_json(contamination_report_path)
    dedup_report = _load_json(dedup_report_path)

    contamination_hits = contamination_report.get("hits", []) or []
    contamination_total = max(1, sum(int(item.get("rows", 0)) for item in source_quality.get("sources", [])))
    contamination_rate_global = _clamp(len(contamination_hits) / contamination_total)
    duplicate_total = float(dedup_report.get("exact_duplicates", 0) + dedup_report.get("near_duplicates", 0))
    dedup_denominator = max(1.0, duplicate_total + float(dedup_report.get("accepted", 0)))
    duplicate_rate_global = _clamp(duplicate_total / dedup_denominator)
    approved = float(review_report.get("approved", 0))
    rejected = float(review_report.get("rejected", 0))
    review_approval_rate = _clamp(approved / max(1.0, approved + rejected)) if review_report else 0.5
    recommendations = feedback_report.get("recommendations", []) if feedback_report else []
    benchmark_feedback_score = 0.75 if not recommendations else 0.6

    task_by_language = task_report.get("by_language", {}) if task_report else {}
    task_total = max(1, int(task_report.get("extracted", 0))) if task_report else 1
    task_coverage_global = _clamp(len(task_by_language) / 8.0) if task_report else 0.0

    scores: list[SourceReputationScore] = []
    for item in source_quality.get("sources", []):
        rows = int(item.get("rows", 0))
        security_coverage = _clamp(float(item.get("defensive_security_rows", 0)) / max(1, rows))
        code_coverage = _clamp(float(item.get("code_rows", 0)) / max(1, rows))
        task_coverage = _clamp((task_total / max(1, rows)) * task_coverage_global) if rows else 0.0
        avg_quality = _clamp(float(item.get("avg_quality_score", 0.0)))
        license_trust = _license_trust(str(item.get("source") or "unknown"), item)
        reputation = _clamp(
            (0.42 * avg_quality)
            + (0.20 * security_coverage)
            + (0.14 * code_coverage)
            + (0.10 * task_coverage)
            + (0.06 * review_approval_rate)
            + (0.05 * license_trust)
            + (0.03 * benchmark_feedback_score)
            - (0.20 * contamination_rate_global)
            - (0.16 * duplicate_rate_global)
        )
        reasons: list[str] = []
        if avg_quality < 0.60:
            reasons.append("low_average_quality")
        if security_coverage < 0.12:
            reasons.append("low_security_coverage")
        if code_coverage < 0.08:
            reasons.append("low_code_coverage")
        if contamination_rate_global > 0:
            reasons.append("contamination_seen_in_run")
        if duplicate_rate_global > 0.15:
            reasons.append("high_duplicate_pressure")
        action = "promote" if reputation >= 0.82 else "watch" if reputation >= 0.64 else "throttle" if reputation >= 0.46 else "block"
        scores.append(
            SourceReputationScore(
                source=str(item.get("source") or "unknown"),
                rows=rows,
                avg_quality_score=round(avg_quality, 6),
                task_coverage=round(task_coverage, 6),
                security_coverage=round(security_coverage, 6),
                code_coverage=round(code_coverage, 6),
                review_approval_rate=round(review_approval_rate, 6),
                duplicate_rate=round(duplicate_rate_global, 6),
                contamination_rate=round(contamination_rate_global, 6),
                license_trust=round(license_trust, 6),
                benchmark_feedback_score=round(benchmark_feedback_score, 6),
                reputation_score=round(reputation, 6),
                action=action,
                reasons=reasons,
            )
        )
    scores.sort(key=lambda item: item.reputation_score, reverse=True)
    return SourceReputationReport(sources=scores)


def write_source_reputation_report(output_path: str | Path, **kwargs: object) -> SourceReputationReport:
    report = build_source_reputation_report(**kwargs)
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report.model_dump(), indent=2, sort_keys=True), encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Build Mythos source reputation report.")
    parser.add_argument("--source-quality-report", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--task-report")
    parser.add_argument("--review-report")
    parser.add_argument("--feedback-report")
    parser.add_argument("--contamination-report")
    parser.add_argument("--dedup-report")
    args = parser.parse_args()
    report = write_source_reputation_report(
        args.output,
        source_quality_report_path=args.source_quality_report,
        task_report_path=args.task_report,
        review_report_path=args.review_report,
        feedback_report_path=args.feedback_report,
        contamination_report_path=args.contamination_report,
        dedup_report_path=args.dedup_report,
    )
    print(json.dumps(report.model_dump(), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
