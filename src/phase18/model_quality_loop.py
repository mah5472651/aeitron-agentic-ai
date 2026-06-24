#!/usr/bin/env python
"""Real backend scorecard and failure-to-training loop.

Phase 18 answers:

1. How does the connected real model perform on the exact scorecard logic?
2. Which phase/category failed?
3. Which failures should become reviewed SFT/GRPO candidates?
4. What should the dashboard show next?
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.phase14.scorecard_harness import (
    FAIL,
    WARN,
    ScorecardMetrics,
    ScorecardRun,
    ScorecardRunner,
    ScorecardTask,
    TaskOutcome,
    compute_metrics,
    dataset_summary,
    load_exact_golden_tasks,
)

ARTIFACT_DIR = ROOT / "artifacts" / "phase18"


@dataclass(frozen=True)
class FailureCluster:
    key: str
    count: int
    average_score: float
    task_ids: list[str]
    recommendation: str


@dataclass(frozen=True)
class FailureAnalysis:
    total_failures: int
    total_warnings: int
    by_category: list[FailureCluster]
    by_phase: list[FailureCluster]
    by_issue_type: list[FailureCluster]
    root_cause_summary: list[str]
    next_fix_recommendations: list[str]


@dataclass(frozen=True)
class PromotionSummary:
    sft_path: str
    grpo_path: str
    sft_count: int
    grpo_count: int
    review_required: bool
    policy: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Phase18Report:
    run_id: str
    started_at_unix: float
    duration_ms: float
    backend_kind: str
    model_endpoint: str
    model_name: str
    suite: str
    task_dataset: dict[str, int]
    real_ready: bool
    metrics: ScorecardMetrics
    summary: dict[str, int]
    failure_analysis: FailureAnalysis
    promotion: PromotionSummary
    scorecard_run: ScorecardRun
    artifacts: dict[str, str]


def select_balanced_tasks(tasks: list[ScorecardTask], *, max_tasks: int, per_category: int) -> list[ScorecardTask]:
    if max_tasks <= 0:
        return tasks
    selected: list[ScorecardTask] = []
    counts: dict[str, int] = {}
    for task in tasks:
        current = counts.get(task.category, 0)
        if current >= per_category:
            continue
        selected.append(task)
        counts[task.category] = current + 1
        if len(selected) >= max_tasks:
            break
    if len(selected) < max_tasks:
        seen = {task.task_id for task in selected}
        for task in tasks:
            if task.task_id in seen:
                continue
            selected.append(task)
            if len(selected) >= max_tasks:
                break
    return selected


def cluster_outcomes(outcomes: list[TaskOutcome], *, attr: str) -> list[FailureCluster]:
    buckets: dict[str, list[TaskOutcome]] = {}
    for outcome in outcomes:
        value = getattr(outcome, attr) if hasattr(outcome, attr) else None
        key = str(value or "unknown")
        buckets.setdefault(key, []).append(outcome)
    clusters: list[FailureCluster] = []
    for key, items in buckets.items():
        recommendations = [item.recommendation for item in items if item.recommendation]
        average_score = sum(item.score for item in items) / max(1, len(items))
        clusters.append(
            FailureCluster(
                key=key,
                count=len(items),
                average_score=round(average_score, 4),
                task_ids=[item.task_id for item in items[:20]],
                recommendation=recommendations[0] if recommendations else "Add targeted regression coverage.",
            )
        )
    clusters.sort(key=lambda item: (-item.count, item.average_score, item.key))
    return clusters


def analyze_failures(outcomes: list[TaskOutcome]) -> FailureAnalysis:
    weak = [item for item in outcomes if item.status in {FAIL, WARN}]
    failures = [item for item in outcomes if item.status == FAIL]
    warnings = [item for item in outcomes if item.status == WARN]
    by_category = cluster_outcomes(weak, attr="category")
    by_phase = cluster_outcomes(weak, attr="failed_phase")
    by_issue = cluster_outcomes(weak, attr="issue_type")
    root_causes: list[str] = []
    if any(item.issue_type == "model_output" for item in weak):
        root_causes.append("real model output lacks required scorecard signals; this is model/data quality, not architecture plumbing")
    if any(item.failed_phase == "memory_engine" for item in weak):
        root_causes.append("memory/context retrieval missed expected repo signals")
    if any(item.category == "security_finding" for item in weak):
        root_causes.append("security reasoning needs more CWE-specific examples and verifier feedback")
    if any(item.category == "patch_generation" for item in weak):
        root_causes.append("patch generation needs before/after secure fix examples")
    recommendations = []
    for cluster in by_category[:5]:
        recommendations.append(f"{cluster.key}: {cluster.recommendation}")
    if not recommendations:
        recommendations.append("No weak tasks found; increase task count and run full scorecard.")
    return FailureAnalysis(
        total_failures=len(failures),
        total_warnings=len(warnings),
        by_category=by_category,
        by_phase=by_phase,
        by_issue_type=by_issue,
        root_cause_summary=root_causes or ["No dominant failure root cause detected."],
        next_fix_recommendations=recommendations,
    )


def expected_signals(outcome: TaskOutcome) -> list[str]:
    details = outcome.details or {}
    for key in ("expected_signals", "expected_cwes", "expected_paths"):
        value = details.get(key)
        if isinstance(value, list):
            return [str(item) for item in value[:20]]
    return []


def build_training_prompt(outcome: TaskOutcome) -> str:
    return (
        "Improve a coding/security model response for this failed scorecard task.\n\n"
        f"Task ID: {outcome.task_id}\n"
        f"Category: {outcome.category}\n"
        f"Failed phase: {outcome.failed_phase or 'unknown'}\n"
        f"Issue type: {outcome.issue_type or 'unknown'}\n"
        f"Score: {outcome.score:.4f}\n"
        f"Failure message: {outcome.message}\n"
        f"Expected signals: {expected_signals(outcome)}\n\n"
        "Return a defensive, implementation-ready answer with concrete plan, code/patch direction, tests, and verification."
    )


def build_chosen(outcome: TaskOutcome) -> str:
    return (
        "<|thought_start|>"
        f"Identify why {outcome.category} failed, recover missing task signals, and keep the response defensive. "
        "Do not claim execution success unless verifier telemetry proves it."
        "<|thought_end|>"
        "<|patch_start|>"
        f"Target behavior: {outcome.recommendation} "
        "Include precise files or vulnerability classes when known, add tests, and state verifier steps."
        "<|patch_end|>"
    )


def build_rejected(outcome: TaskOutcome) -> str:
    details = outcome.details or {}
    preview = details.get("text_preview")
    if isinstance(preview, str) and preview.strip():
        return preview[:4000]
    return f"Rejected low-scoring response: {outcome.message}"


def promote_failures(
    *,
    run_id: str,
    outcomes: list[TaskOutcome],
    output_dir: Path,
    include_warnings: bool,
) -> PromotionSummary:
    candidates = [item for item in outcomes if item.status == FAIL or (include_warnings and item.status == WARN)]
    sft_path = output_dir / f"{run_id}-reviewed-sft-candidates.jsonl"
    grpo_path = output_dir / f"{run_id}-reviewed-grpo-candidates.jsonl"
    sft_rows = []
    grpo_rows = []
    for outcome in candidates:
        metadata = {
            "source": "phase18_real_model_scorecard",
            "review_status": "candidate_needs_human_or_verifier_review",
            "task_id": outcome.task_id,
            "category": outcome.category,
            "failed_phase": outcome.failed_phase,
            "issue_type": outcome.issue_type,
            "score": outcome.score,
            "recommendation": outcome.recommendation,
            "details": outcome.details,
        }
        prompt = build_training_prompt(outcome)
        chosen = build_chosen(outcome)
        sft_rows.append({"prompt": prompt, "chosen": chosen, "metadata": metadata})
        grpo_rows.append({"prompt": prompt, "chosen": chosen, "rejected": build_rejected(outcome), "metadata": metadata})
    output_dir.mkdir(parents=True, exist_ok=True)
    sft_path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in sft_rows) + ("\n" if sft_rows else ""), encoding="utf-8")
    grpo_path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in grpo_rows) + ("\n" if grpo_rows else ""), encoding="utf-8")
    return PromotionSummary(
        sft_path=str(sft_path),
        grpo_path=str(grpo_path),
        sft_count=len(sft_rows),
        grpo_count=len(grpo_rows),
        review_required=True,
        policy={
            "include_warnings": include_warnings,
            "accepted_statuses": ["fail", "warn"] if include_warnings else ["fail"],
            "note": "Rows are candidates. Do not train until verifier/human review approves them.",
        },
    )


async def run_phase18(args: argparse.Namespace) -> Phase18Report:
    started = time.time()
    old_env = {
        "SCORECARD_MODEL_ENDPOINT": os.environ.get("SCORECARD_MODEL_ENDPOINT"),
        "SCORECARD_MODEL_NAME": os.environ.get("SCORECARD_MODEL_NAME"),
        "SCORECARD_API_KEY": os.environ.get("SCORECARD_API_KEY"),
        "SCORECARD_MAX_NEW_TOKENS": os.environ.get("SCORECARD_MAX_NEW_TOKENS"),
    }
    if args.model_endpoint:
        os.environ["SCORECARD_MODEL_ENDPOINT"] = args.model_endpoint
    if args.model_name:
        os.environ["SCORECARD_MODEL_NAME"] = args.model_name
    if args.api_key:
        os.environ["SCORECARD_API_KEY"] = args.api_key
    if args.max_new_tokens:
        os.environ["SCORECARD_MAX_NEW_TOKENS"] = str(args.max_new_tokens)
    report_endpoint = os.environ.get("SCORECARD_MODEL_ENDPOINT", os.environ.get("PHASE11_MODEL_ENDPOINT", ""))
    report_model = os.environ.get("SCORECARD_MODEL_NAME", os.environ.get("PHASE11_MODEL_NAME", ""))
    all_tasks = load_exact_golden_tasks()
    tasks = all_tasks if args.full else select_balanced_tasks(all_tasks, max_tasks=args.max_tasks, per_category=args.per_category)
    try:
        runner = ScorecardRunner(
            mode="real",
            backend_kind=args.backend_kind,
            run_sandbox=args.run_sandbox,
            context_budget=args.context_budget,
            concurrency=args.concurrency,
        )
        scorecard_run = await runner.run(args.run_id, tasks, previous=None)
    finally:
        for key, value in old_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    sandbox_rate = scorecard_run.metrics.sandbox_test_pass_rate
    metrics = compute_metrics(scorecard_run.outcomes, sandbox_rate=sandbox_rate, regression_count=0)
    analysis = analyze_failures(scorecard_run.outcomes)
    promotion = promote_failures(
        run_id=args.run_id,
        outcomes=scorecard_run.outcomes,
        output_dir=args.output_dir,
        include_warnings=args.include_warnings,
    )
    return Phase18Report(
        run_id=args.run_id,
        started_at_unix=started,
        duration_ms=(time.time() - started) * 1000,
        backend_kind=args.backend_kind,
        model_endpoint=report_endpoint,
        model_name=report_model,
        suite="full" if args.full else "quick_balanced",
        task_dataset=dataset_summary(tasks),
        real_ready=scorecard_run.ready,
        metrics=metrics,
        summary=scorecard_run.summary,
        failure_analysis=analysis,
        promotion=promotion,
        scorecard_run=scorecard_run,
        artifacts={},
    )


def write_reports(report: Phase18Report, output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / f"{report.run_id}.json"
    md_path = output_dir / f"{report.run_id}.md"
    latest_path = output_dir / "model-quality-latest.json"
    payload = asdict(report)
    payload["artifacts"] = {
        "json": str(json_path),
        "markdown": str(md_path),
        "sft_candidates": report.promotion.sft_path,
        "grpo_candidates": report.promotion.grpo_path,
    }
    json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    latest_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    lines = [
        "# Phase 18 Real Model Quality Loop",
        "",
        f"- Run ID: `{report.run_id}`",
        f"- Suite: `{report.suite}`",
        f"- Real ready: `{report.real_ready}`",
        f"- Backend: `{report.backend_kind}`",
        f"- Model: `{report.model_name}`",
        f"- Overall score: `{report.metrics.overall_score:.2f}`",
        f"- Summary: `{report.summary}`",
        f"- SFT candidates: `{report.promotion.sft_count}`",
        f"- GRPO candidates: `{report.promotion.grpo_count}`",
        "",
        "## Metrics",
        "",
        f"- Architecture reliability: `{report.metrics.architecture_reliability_score:.2f}`",
        f"- Agent workflow: `{report.metrics.agent_workflow_completion_score:.2f}`",
        f"- Security detection/fix: `{report.metrics.security_detection_fix_score:.2f}`",
        f"- Short prompt understanding: `{report.metrics.short_prompt_understanding_score:.2f}`",
        f"- Sandbox/test pass rate: `{report.metrics.sandbox_test_pass_rate:.2f}`",
        "",
        "## Failure Clusters",
        "",
        "| Category | Count | Avg Score | Recommendation |",
        "| --- | ---: | ---: | --- |",
    ]
    for cluster in report.failure_analysis.by_category:
        lines.append(f"| {cluster.key} | {cluster.count} | {cluster.average_score:.2f} | {cluster.recommendation.replace('|', '/')} |")
    lines.extend(["", "## Root Causes", ""])
    lines.extend(f"- {item}" for item in report.failure_analysis.root_cause_summary)
    lines.extend(["", "## Next Fixes", ""])
    lines.extend(f"- {item}" for item in report.failure_analysis.next_fix_recommendations)
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, md_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Phase 18 real model scorecard and failure promotion loop.")
    parser.add_argument("--run-id", default=f"phase18-qwen-{int(time.time())}")
    parser.add_argument("--backend-kind", choices=["openai_compatible", "pytorch", "mock"], default="openai_compatible")
    parser.add_argument("--model-endpoint", default=os.environ.get("PHASE18_MODEL_ENDPOINT"))
    parser.add_argument("--model-name", default=os.environ.get("PHASE18_MODEL_NAME"))
    parser.add_argument("--api-key", default=os.environ.get("PHASE18_API_KEY"))
    parser.add_argument("--output-dir", type=Path, default=ARTIFACT_DIR)
    parser.add_argument("--full", action="store_true", help="Run all 90 exact scorecard tasks.")
    parser.add_argument("--max-tasks", type=int, default=5)
    parser.add_argument("--per-category", type=int, default=1)
    parser.add_argument("--context-budget", type=int, default=2500)
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--max-new-tokens", type=int, default=int(os.environ.get("PHASE18_MAX_NEW_TOKENS", "180")))
    parser.add_argument("--run-sandbox", action="store_true")
    parser.add_argument("--include-warnings", action="store_true")
    parser.add_argument("--dry-run", action="store_true", help="Validate task selection and config without calling the model.")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON only.")
    parser.add_argument("--strict", action="store_true")
    return parser.parse_args()


async def async_main() -> int:
    args = parse_args()
    if args.dry_run:
        all_tasks = load_exact_golden_tasks()
        tasks = all_tasks if args.full else select_balanced_tasks(all_tasks, max_tasks=args.max_tasks, per_category=args.per_category)
        payload = {
            "run_id": args.run_id,
            "dry_run": True,
            "suite": "full" if args.full else "quick_balanced",
            "task_dataset": dataset_summary(tasks),
            "backend_kind": args.backend_kind,
            "model_endpoint": args.model_endpoint or os.environ.get("SCORECARD_MODEL_ENDPOINT") or "http://127.0.0.1:8016/v1",
            "model_name": args.model_name or os.environ.get("SCORECARD_MODEL_NAME") or "Qwen/Qwen2.5-Coder-0.5B-Instruct",
            "max_new_tokens": args.max_new_tokens,
            "promotion_policy": "failures and optional warnings become review-required candidates",
        }
        print(json.dumps(payload, indent=2))
        return 0
    report = await run_phase18(args)
    json_path, md_path = write_reports(report, args.output_dir)
    print(
        json.dumps(
            {
                "run_id": report.run_id,
                "suite": report.suite,
                "real_ready": report.real_ready,
                "overall_score": report.metrics.overall_score,
                "summary": report.summary,
                "sft_candidates": report.promotion.sft_count,
                "grpo_candidates": report.promotion.grpo_count,
                "json": str(json_path),
                "markdown": str(md_path),
            },
            indent=2,
        )
    )
    return 1 if args.strict and not report.real_ready else 0


def main() -> None:
    raise SystemExit(asyncio.run(async_main()))


if __name__ == "__main__":
    main()
