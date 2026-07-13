"""Adapters for external-style benchmark suites.

These adapters intentionally require local files. Mythos does not silently
download protected benchmarks into training or eval runs.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from src.mythos.evaluation.benchmarks import BenchmarkHarness, BenchmarkRunReport, BenchmarkTask
from src.mythos.shared.schemas import StrictModel


SuiteKind = Literal["swe_bench_style", "human_eval_style", "mbpp_style", "cyberseceval_style", "custom_security"]


class BenchmarkSuiteSpec(StrictModel):
    name: str
    kind: SuiteKind
    path: str
    required: bool = True


class BenchmarkSuiteResult(StrictModel):
    name: str
    kind: str
    status: str
    score: float = Field(ge=0.0, le=1.0)
    total: int
    passed: int
    reason: str = ""
    report: dict[str, Any] | None = None


class BenchmarkSuitesReport(StrictModel):
    status: str
    suites: list[BenchmarkSuiteResult]
    aggregate_score: float
    created_at_unix: float = Field(default_factory=time.time)

    def write(self, output_dir: str | Path) -> Path:
        root = Path(output_dir)
        root.mkdir(parents=True, exist_ok=True)
        target = root / "benchmark_suites_report.json"
        target.write_text(json.dumps(self.model_dump(), indent=2, sort_keys=True), encoding="utf-8")
        write_markdown(self, root / "benchmark_suites_report.md")
        return target


def _load_jsonl(path: str | Path) -> list[dict[str, Any]]:
    rows = []
    with Path(path).open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSONL in {path} line {line_number}: {exc.msg}") from exc
    return rows


def swe_bench_style_to_tasks(path: str | Path) -> list[BenchmarkTask]:
    tasks = []
    for index, row in enumerate(_load_jsonl(path)):
        task_id = str(row.get("instance_id") or row.get("task_id") or f"swe-{index}")
        files = row.get("files", {}) if isinstance(row.get("files"), dict) else {}
        patch = str(row.get("patch") or row.get("gold_patch") or row.get("test_patch") or "")
        if patch:
            files = {**files, "patch.diff": patch}
        expected = row.get("expected_findings") or row.get("expected_terms") or ["diff", "test"]
        tasks.append(
            BenchmarkTask(
                task_id=task_id,
                benchmark="swe_style",
                prompt=str(row.get("problem_statement") or row.get("prompt") or ""),
                files={str(key): str(value) for key, value in files.items()},
                expected_findings=[str(item) for item in expected],
                tags=["swe_bench_style"],
            )
        )
    return tasks


def code_style_to_tasks(path: str | Path, *, tag: str) -> list[BenchmarkTask]:
    tasks = []
    for index, row in enumerate(_load_jsonl(path)):
        task_id = str(row.get("task_id") or row.get("name") or f"{tag}-{index}")
        prompt = str(row.get("prompt") or row.get("text") or row.get("question") or "")
        solution = str(row.get("canonical_solution") or row.get("code") or row.get("answer") or "")
        expected = row.get("expected_terms") or (["def"] if tag == "human_eval_style" else [])
        source_text = f"{prompt}\n{solution}".strip() if tag == "human_eval_style" else (solution or prompt)
        tasks.append(
            BenchmarkTask(
                task_id=task_id,
                benchmark="swe_style",
                prompt=prompt,
                files={"solution.py": source_text},
                expected_findings=[str(item) for item in expected],
                tags=[tag],
            )
        )
    return tasks


def cyberseceval_style_to_tasks(path: str | Path) -> list[BenchmarkTask]:
    tasks = []
    for index, row in enumerate(_load_jsonl(path)):
        code = str(row.get("code") or row.get("content") or row.get("snippet") or "")
        expected = row.get("expected_findings") or row.get("cwe") or row.get("vulnerability") or []
        if isinstance(expected, str):
            expected = [expected]
        tasks.append(
            BenchmarkTask(
                task_id=str(row.get("task_id") or row.get("id") or f"security-{index}"),
                benchmark="security_static",
                prompt=str(row.get("prompt") or row.get("question") or "Find defensive security issues."),
                files={str(row.get("filename") or "snippet.txt"): code},
                expected_findings=[str(item) for item in expected],
                tags=["cyberseceval_style"],
            )
        )
    return tasks


def load_suite_tasks(spec: BenchmarkSuiteSpec) -> list[BenchmarkTask]:
    if spec.kind == "swe_bench_style":
        return swe_bench_style_to_tasks(spec.path)
    if spec.kind == "human_eval_style":
        return code_style_to_tasks(spec.path, tag="human_eval_style")
    if spec.kind == "mbpp_style":
        return code_style_to_tasks(spec.path, tag="mbpp_style")
    if spec.kind in {"cyberseceval_style", "custom_security"}:
        return cyberseceval_style_to_tasks(spec.path)
    raise ValueError(f"unsupported suite kind: {spec.kind}")


def run_benchmark_suites(specs: list[BenchmarkSuiteSpec]) -> BenchmarkSuitesReport:
    harness = BenchmarkHarness()
    results: list[BenchmarkSuiteResult] = []
    for spec in specs:
        path = Path(spec.path)
        if not path.exists():
            results.append(
                BenchmarkSuiteResult(
                    name=spec.name,
                    kind=spec.kind,
                    status="failed" if spec.required else "skipped",
                    score=0.0,
                    total=0,
                    passed=0,
                    reason=f"benchmark file missing: {path}",
                )
            )
            continue
        suite_report: BenchmarkRunReport = harness.run_static(load_suite_tasks(spec))
        results.append(
            BenchmarkSuiteResult(
                name=spec.name,
                kind=spec.kind,
                status=suite_report.status,
                score=suite_report.score,
                total=suite_report.total,
                passed=suite_report.passed,
                reason="local benchmark suite executed",
                report=suite_report.model_dump(),
            )
        )
    active = [item for item in results if item.status != "skipped"]
    aggregate = sum(item.score for item in active) / max(1, len(active))
    status = "failed" if any(item.status == "failed" for item in active) else "passed"
    return BenchmarkSuitesReport(status=status, suites=results, aggregate_score=round(aggregate, 6))


def write_markdown(report: BenchmarkSuitesReport, path: str | Path) -> Path:
    target = Path(path)
    lines = [
        "# Aeitron Benchmark Suites Report",
        "",
        f"- status: {report.status}",
        f"- aggregate_score: {report.aggregate_score:.4f}",
        "",
        "| suite | kind | status | score | total | passed | reason |",
        "|---|---|---|---:|---:|---:|---|",
    ]
    for suite in report.suites:
        lines.append(f"| {suite.name} | {suite.kind} | {suite.status} | {suite.score:.4f} | {suite.total} | {suite.passed} | {suite.reason} |")
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return target


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run local SWE-Bench/CyberSecEval-style benchmark adapters.")
    parser.add_argument("--suite", action="append", nargs=3, metavar=("NAME", "KIND", "PATH"), default=[])
    parser.add_argument("--optional-suite", action="append", nargs=3, metavar=("NAME", "KIND", "PATH"), default=[])
    parser.add_argument("--output-dir", required=True)
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    specs = [
        BenchmarkSuiteSpec(name=name, kind=kind, path=path, required=True)
        for name, kind, path in args.suite
    ] + [
        BenchmarkSuiteSpec(name=name, kind=kind, path=path, required=False)
        for name, kind, path in args.optional_suite
    ]
    report = run_benchmark_suites(specs)
    report.write(args.output_dir)
    print(json.dumps(report.model_dump(), indent=2, sort_keys=True))
    if report.status != "passed":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
