#!/usr/bin/env python
"""Phase 13 backend quality harness.

Phase 12 proves the architecture can execute the workflows. Phase 13 compares
actual backend response quality on coding/security prompts. It is intentionally
backend-agnostic: mock, OpenAI/vLLM-compatible, and future PyTorch checkpoints
all go through the same ModelBackend API.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

OK = "ok"
WARN = "warn"
FAIL = "fail"


@dataclass(frozen=True)
class QualityTask:
    task_id: str
    category: str
    prompt: str
    expected_keywords: list[str]
    required_markers: list[str] = field(default_factory=list)
    forbidden_terms: list[str] = field(default_factory=list)
    min_chars: int = 120
    weight: float = 1.0


@dataclass(frozen=True)
class ResponseScore:
    task_id: str
    category: str
    backend_label: str
    status: str
    score: float
    latency_ms: float
    message: str
    text_preview: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class BackendQualitySummary:
    backend_label: str
    backend_kind: str
    model: str
    overall_score: float
    category_scores: dict[str, float]
    summary: dict[str, int]
    average_latency_ms: float
    results: list[ResponseScore]


@dataclass(frozen=True)
class BackendComparisonReport:
    run_id: str
    suite: str
    started_at_unix: float
    duration_ms: float
    baseline: BackendQualitySummary
    candidate: BackendQualitySummary
    score_delta: float
    category_deltas: dict[str, float]
    winner_counts: dict[str, int]
    candidate_ready: bool
    architecture_reference: dict[str, Any]
    recommendations: list[str]


def generate_quality_tasks() -> list[QualityTask]:
    tasks: list[QualityTask] = []
    prompts = [
        (
            "short-01",
            "short_prompt_quality",
            "fix login security",
            ["login", "security", "hash", "sql", "test"],
            ["plan", "verify"],
        ),
        (
            "short-02",
            "short_prompt_quality",
            "make this repo safer",
            ["inspect", "security", "dependency", "test", "patch"],
            ["plan", "verify"],
        ),
        (
            "short-03",
            "short_prompt_quality",
            "debug crash",
            ["traceback", "reproduce", "fix", "test", "verify"],
            ["debug", "test"],
        ),
        (
            "code-01",
            "coding_quality",
            "Build a FastAPI endpoint that accepts a username and safely queries PostgreSQL.",
            ["fastapi", "postgres", "parameter", "async", "test"],
            ["code", "verify"],
        ),
        (
            "code-02",
            "coding_quality",
            "Write a Python function that safely normalizes user file paths under a trusted base directory.",
            ["resolve", "base", "path", "traversal", "test"],
            ["code", "security"],
        ),
        (
            "code-03",
            "coding_quality",
            "Implement a Redis token bucket with atomic Lua updates and remaining balance headers.",
            ["redis", "lua", "atomic", "tokens", "headers"],
            ["code", "test"],
        ),
        (
            "sec-01",
            "security_quality",
            "Analyze this code for SQL injection: cursor.execute(\"SELECT * FROM users WHERE name='\" + name + \"'\")",
            ["sql injection", "parameter", "prepared", "cwe", "patch"],
            ["security", "fix"],
        ),
        (
            "sec-02",
            "security_quality",
            "Find and fix the vulnerability in: subprocess.run(cmd, shell=True)",
            ["command injection", "shell", "argument", "allowlist", "test"],
            ["security", "patch"],
        ),
        (
            "sec-03",
            "security_quality",
            "Review C code using strcpy(dst, src) and propose a safe fix.",
            ["buffer", "bounds", "strncpy", "size", "cwe"],
            ["security", "fix"],
        ),
        (
            "debug-01",
            "debugging_quality",
            "A Python test fails with ValueError: invalid literal for int() with base 10. Diagnose and patch.",
            ["reproduce", "input", "validation", "exception", "test"],
            ["debug", "patch"],
        ),
        (
            "debug-02",
            "debugging_quality",
            "A Docker sandbox command times out after 5000ms. Explain how the self-healing loop should react.",
            ["timeout", "sandbox", "telemetry", "repair", "retry"],
            ["diagnosis", "verify"],
        ),
        (
            "format-01",
            "format_compliance",
            "Return exactly this schema with reasoning tokens: <|thought_start|>short reasoning<|thought_end|><|patch_start|>patch<|patch_end|>",
            ["<|thought_start|>", "<|thought_end|>", "<|patch_start|>", "<|patch_end|>"],
            ["<|thought_start|>", "<|patch_end|>"],
            40,
        ),
        (
            "agent-01",
            "agentic_reasoning_quality",
            "Plan an agentic coding run: inspect repo, edit code, run tests, review security, and self-heal failures.",
            ["inspect", "edit", "test", "security", "self-heal"],
            ["plan", "verify"],
        ),
        (
            "agent-02",
            "agentic_reasoning_quality",
            "Given a vague prompt 'make auth better', infer the best coding workflow.",
            ["infer", "auth", "hash", "session", "test"],
            ["plan", "security"],
        ),
        (
            "memory-01",
            "long_context_reasoning_quality",
            "Explain how AST call graphs and vector memory should choose files for a small bug-fix prompt.",
            ["ast", "call graph", "vector", "context", "token"],
            ["memory", "retrieve"],
        ),
    ]
    forbidden = ["cannot help", "as an ai language model", "mock-vllm"]
    for task_id, category, prompt, keywords, markers, *rest in prompts:
        min_chars = rest[0] if rest else 120
        tasks.append(
            QualityTask(
                task_id=task_id,
                category=category,
                prompt=prompt,
                expected_keywords=keywords,
                required_markers=markers,
                forbidden_terms=forbidden,
                min_chars=min_chars,
            )
        )
    return tasks


def select_suite(tasks: list[QualityTask], suite: str) -> list[QualityTask]:
    if suite == "full":
        return tasks
    if suite == "quick":
        keep = {"short-01", "code-01", "sec-01", "debug-01", "format-01", "agent-01", "memory-01"}
        return [task for task in tasks if task.task_id in keep]
    raise ValueError("suite must be quick or full")


def build_backend(kind: str, label: str):
    from src.phase11.model_backends import build_backend as phase11_build_backend

    if kind == "mock":
        return phase11_build_backend("mock")
    if kind == "pytorch":
        return phase11_build_backend(
            "pytorch",
            checkpoint=os.environ.get(f"PHASE13_{label.upper()}_CHECKPOINT") or os.environ.get("PHASE11_CHECKPOINT"),
            tokenizer_path=os.environ.get(f"PHASE13_{label.upper()}_TOKENIZER") or os.environ.get("PHASE11_TOKENIZER"),
            device=os.environ.get(f"PHASE13_{label.upper()}_DEVICE") or os.environ.get("PHASE11_DEVICE", "cpu"),
        )
    if kind == "openai_compatible":
        return phase11_build_backend(
            "openai_compatible",
            endpoint=os.environ.get(f"PHASE13_{label.upper()}_ENDPOINT") or os.environ.get("PHASE13_MODEL_ENDPOINT", "http://127.0.0.1:8000/v1"),
            model_name=os.environ.get(f"PHASE13_{label.upper()}_MODEL") or os.environ.get("PHASE13_MODEL_NAME", "security-coder"),
            api_key=os.environ.get(f"PHASE13_{label.upper()}_API_KEY") or os.environ.get("PHASE13_API_KEY"),
        )
    raise ValueError(f"unsupported backend kind: {kind}")


async def generate_text(backend: Any, task: QualityTask) -> tuple[str, float, str]:
    from src.phase11.schemas import ChatMessage, ChatRole, GenerationConfig, GenerationRequest

    started = time.perf_counter()
    response = await backend.generate(
        GenerationRequest(
            messages=[
                ChatMessage(
                    role=ChatRole.SYSTEM,
                    content=(
                        "You are a senior coding and cybersecurity assistant. "
                        "Be specific, practical, security-aware, and include verification steps."
                    ),
                ),
                ChatMessage(role=ChatRole.USER, content=task.prompt),
            ],
            config=GenerationConfig(max_new_tokens=700, temperature=0.2),
        )
    )
    latency_ms = (time.perf_counter() - started) * 1000
    return response.text, latency_ms, response.model


def score_text(task: QualityTask, text: str, *, backend_label: str, latency_ms: float) -> ResponseScore:
    lower = text.lower()
    keyword_hits = [keyword for keyword in task.expected_keywords if keyword.lower() in lower]
    marker_hits = [marker for marker in task.required_markers if marker.lower() in lower]
    forbidden_hits = [term for term in task.forbidden_terms if term.lower() in lower]
    keyword_score = len(keyword_hits) / max(1, len(task.expected_keywords))
    marker_score = len(marker_hits) / max(1, len(task.required_markers))
    length_score = 1.0 if len(text.strip()) >= task.min_chars else max(0.0, len(text.strip()) / max(1, task.min_chars))
    forbidden_score = 0.0 if forbidden_hits else 1.0
    structured = any(marker in lower for marker in ["plan", "patch", "test", "verify", "security", "debug", "risk"])
    structure_score = 1.0 if structured else 0.0
    score = (
        keyword_score * 0.45
        + marker_score * 0.20
        + length_score * 0.15
        + forbidden_score * 0.10
        + structure_score * 0.10
    )
    status = OK if score >= 0.80 else WARN if score >= 0.55 else FAIL
    return ResponseScore(
        task_id=task.task_id,
        category=task.category,
        backend_label=backend_label,
        status=status,
        score=round(score, 4),
        latency_ms=latency_ms,
        message=f"keywords={len(keyword_hits)}/{len(task.expected_keywords)} markers={len(marker_hits)}/{len(task.required_markers)} forbidden={len(forbidden_hits)}",
        text_preview=text[:1000],
        details={
            "keyword_hits": keyword_hits,
            "marker_hits": marker_hits,
            "forbidden_hits": forbidden_hits,
            "length": len(text.strip()),
        },
    )


async def evaluate_backend(kind: str, label: str, tasks: list[QualityTask], concurrency: int) -> BackendQualitySummary:
    backend = build_backend(kind, label)
    model_name = getattr(backend, "model_name", kind)
    semaphore = asyncio.Semaphore(max(1, concurrency))

    async def run_one(task: QualityTask) -> ResponseScore:
        async with semaphore:
            try:
                text, latency_ms, _model = await generate_text(backend, task)
                return score_text(task, text, backend_label=label, latency_ms=latency_ms)
            except Exception as exc:
                return ResponseScore(
                    task_id=task.task_id,
                    category=task.category,
                    backend_label=label,
                    status=FAIL,
                    score=0.0,
                    latency_ms=0.0,
                    message=f"{type(exc).__name__}: {exc}",
                    text_preview="",
                )

    try:
        results = await asyncio.gather(*(run_one(task) for task in tasks))
    finally:
        await backend.aclose()

    category_scores: dict[str, float] = {}
    for category in sorted({task.category for task in tasks}):
        scores = [result.score for result in results if result.category == category]
        category_scores[category] = round(statistics.mean(scores) * 100, 2) if scores else 0.0
    summary = {OK: 0, WARN: 0, FAIL: 0}
    for result in results:
        summary[result.status] = summary.get(result.status, 0) + 1
    overall = round(statistics.mean([result.score for result in results]) * 100, 2) if results else 0.0
    average_latency = round(statistics.mean([result.latency_ms for result in results]), 2) if results else 0.0
    return BackendQualitySummary(
        backend_label=label,
        backend_kind=kind,
        model=model_name,
        overall_score=overall,
        category_scores=category_scores,
        summary=summary,
        average_latency_ms=average_latency,
        results=results,
    )


def latest_architecture_reference() -> dict[str, Any]:
    references: dict[str, Any] = {}
    for label, path in {
        "phase10_readiness": ROOT / "artifacts" / "phase10" / "topclass-readiness.json",
        "phase12_gauntlet": ROOT / "artifacts" / "phase12" / "phase12-full-local.json",
    }.items():
        if not path.exists():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        references[label] = {
            "path": str(path),
            "score": payload.get("score") or payload.get("overall_score"),
            "passed": payload.get("passed"),
            "architecture_ready": payload.get("architecture_ready"),
            "summary": payload.get("summary"),
        }
    return references


def compare_reports(
    *,
    run_id: str,
    suite: str,
    started: float,
    baseline: BackendQualitySummary,
    candidate: BackendQualitySummary,
    pass_score: float,
) -> BackendComparisonReport:
    category_deltas: dict[str, float] = {}
    all_categories = set(baseline.category_scores) | set(candidate.category_scores)
    for category in sorted(all_categories):
        category_deltas[category] = round(candidate.category_scores.get(category, 0.0) - baseline.category_scores.get(category, 0.0), 2)
    winner_counts = {"baseline": 0, "candidate": 0, "tie": 0}
    baseline_by_task = {result.task_id: result for result in baseline.results}
    for candidate_result in candidate.results:
        baseline_result = baseline_by_task.get(candidate_result.task_id)
        if baseline_result is None:
            continue
        delta = candidate_result.score - baseline_result.score
        if abs(delta) < 0.03:
            winner_counts["tie"] += 1
        elif delta > 0:
            winner_counts["candidate"] += 1
        else:
            winner_counts["baseline"] += 1
    recommendations = build_recommendations(baseline, candidate, category_deltas, pass_score)
    return BackendComparisonReport(
        run_id=run_id,
        suite=suite,
        started_at_unix=started,
        duration_ms=(time.time() - started) * 1000,
        baseline=baseline,
        candidate=candidate,
        score_delta=round(candidate.overall_score - baseline.overall_score, 2),
        category_deltas=category_deltas,
        winner_counts=winner_counts,
        candidate_ready=candidate.overall_score >= pass_score and candidate.summary.get(FAIL, 0) == 0,
        architecture_reference=latest_architecture_reference(),
        recommendations=recommendations,
    )


def build_recommendations(
    baseline: BackendQualitySummary,
    candidate: BackendQualitySummary,
    category_deltas: dict[str, float],
    pass_score: float,
) -> list[str]:
    recs: list[str] = []
    if candidate.backend_kind == "openai_compatible" and candidate.summary.get(FAIL, 0) == len(candidate.results):
        recs.append("Candidate endpoint did not produce usable responses; verify vLLM/OpenAI-compatible server URL and model name.")
    if candidate.overall_score < pass_score:
        recs.append(f"Candidate backend is below target score {pass_score:.1f}; use this report as the gap list before training or deployment.")
    if category_deltas:
        weakest = min(candidate.category_scores.items(), key=lambda item: item[1])
        recs.append(f"Weakest candidate category is {weakest[0]} at {weakest[1]:.1f}; expand data/evals there first.")
    if candidate.overall_score > baseline.overall_score:
        recs.append("Candidate beats baseline overall; run the full Phase 12 gauntlet with this backend next.")
    else:
        recs.append("Candidate does not beat the architecture mock baseline yet; this is expected for local smoke mocks and untrained checkpoints.")
    if baseline.backend_kind == "mock":
        recs.append("Baseline mock is an architecture control, not an intelligence target.")
    return recs


def export_tasks(tasks: list[QualityTask], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(json.dumps(asdict(task), ensure_ascii=False) for task in tasks) + "\n", encoding="utf-8")


def write_reports(report: BackendComparisonReport, output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / f"{report.run_id}.json"
    md_path = output_dir / f"{report.run_id}.md"
    json_path.write_text(json.dumps(asdict(report), indent=2, ensure_ascii=False), encoding="utf-8")

    lines = [
        "# Phase 13 Backend Quality Comparison",
        "",
        f"- Run ID: `{report.run_id}`",
        f"- Suite: `{report.suite}`",
        f"- Candidate Ready: `{report.candidate_ready}`",
        f"- Baseline: `{report.baseline.backend_kind}` score `{report.baseline.overall_score:.2f}`",
        f"- Candidate: `{report.candidate.backend_kind}` score `{report.candidate.overall_score:.2f}`",
        f"- Delta: `{report.score_delta:+.2f}`",
        f"- Winner Counts: `{report.winner_counts}`",
        "",
        "## Category Delta",
        "",
        "| Category | Baseline | Candidate | Delta |",
        "| --- | ---: | ---: | ---: |",
    ]
    for category in sorted(set(report.baseline.category_scores) | set(report.candidate.category_scores)):
        lines.append(
            f"| {category} | {report.baseline.category_scores.get(category, 0.0):.2f} | "
            f"{report.candidate.category_scores.get(category, 0.0):.2f} | {report.category_deltas.get(category, 0.0):+.2f} |"
        )
    lines.extend(["", "## Candidate Task Results", "", "| Task | Category | Status | Score | Message |", "| --- | --- | --- | ---: | --- |"])
    for result in report.candidate.results:
        lines.append(f"| {result.task_id} | {result.category} | {result.status} | {result.score * 100:.1f} | {result.message.replace('|', '/')} |")
    lines.extend(["", "## Recommendations", ""])
    lines.extend(f"- {rec}" for rec in report.recommendations)
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, md_path


async def run_comparison(args: argparse.Namespace) -> BackendComparisonReport:
    started = time.time()
    tasks = select_suite(generate_quality_tasks(), args.suite)
    if args.max_tasks:
        tasks = tasks[: args.max_tasks]
    export_tasks(generate_quality_tasks(), args.export_tasks)
    baseline, candidate = await asyncio.gather(
        evaluate_backend(args.baseline_backend, "baseline", tasks, args.concurrency),
        evaluate_backend(args.candidate_backend, "candidate", tasks, args.concurrency),
    )
    return compare_reports(
        run_id=args.run_id,
        suite=args.suite,
        started=started,
        baseline=baseline,
        candidate=candidate,
        pass_score=args.pass_score,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Phase 13 backend quality comparison.")
    parser.add_argument("--run-id", default=f"phase13-{int(time.time())}")
    parser.add_argument("--suite", choices=["quick", "full"], default="quick")
    parser.add_argument("--baseline-backend", choices=["mock", "pytorch", "openai_compatible"], default="mock")
    parser.add_argument("--candidate-backend", choices=["mock", "pytorch", "openai_compatible"], default="openai_compatible")
    parser.add_argument("--output-dir", type=Path, default=Path("artifacts/phase13"))
    parser.add_argument("--export-tasks", type=Path, default=Path("data/phase13/backend_quality_tasks.jsonl"))
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--max-tasks", type=int, default=0)
    parser.add_argument("--pass-score", type=float, default=80.0)
    parser.add_argument("--strict", action="store_true", help="Exit non-zero when candidate is below target.")
    return parser.parse_args()


async def async_main() -> int:
    args = parse_args()
    report = await run_comparison(args)
    json_path, md_path = write_reports(report, args.output_dir)
    print(
        json.dumps(
            {
                "run_id": report.run_id,
                "suite": report.suite,
                "candidate_ready": report.candidate_ready,
                "baseline_score": report.baseline.overall_score,
                "candidate_score": report.candidate.overall_score,
                "score_delta": report.score_delta,
                "winner_counts": report.winner_counts,
                "json": str(json_path),
                "markdown": str(md_path),
            },
            indent=2,
        )
    )
    return 1 if args.strict and not report.candidate_ready else 0


def main() -> None:
    raise SystemExit(asyncio.run(async_main()))


if __name__ == "__main__":
    main()
