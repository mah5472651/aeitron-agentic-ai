"""One-command benchmark pack runner for Mythos coding/security evaluation."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from pydantic import Field

from src.mythos.evaluation.benchmark_suites import BenchmarkSuiteSpec, BenchmarkSuitesReport, run_benchmark_suites
from src.mythos.shared.schemas import StrictModel


class BenchmarkPackConfig(StrictModel):
    human_eval_path: str | None = None
    mbpp_path: str | None = None
    swe_bench_path: str | None = None
    cyberseceval_path: str | None = None
    custom_security_path: str | None = None
    strict: bool = True


class BenchmarkPackReport(StrictModel):
    status: str
    strict: bool
    required_suites: list[str]
    optional_suites: list[str]
    suite_report: dict
    recommendations: list[str] = Field(default_factory=list)


def _spec(name: str, kind: str, path: str | None, *, required: bool) -> BenchmarkSuiteSpec | None:
    if not path:
        return None
    return BenchmarkSuiteSpec(name=name, kind=kind, path=path, required=required)  # type: ignore[arg-type]


def run_benchmark_pack(config: BenchmarkPackConfig, *, output_dir: str | Path) -> BenchmarkPackReport:
    specs = [
        _spec("humaneval", "human_eval_style", config.human_eval_path, required=config.strict),
        _spec("mbpp", "mbpp_style", config.mbpp_path, required=config.strict),
        _spec("swe_bench", "swe_bench_style", config.swe_bench_path, required=config.strict),
        _spec("cyberseceval", "cyberseceval_style", config.cyberseceval_path, required=config.strict),
        _spec("custom_security", "custom_security", config.custom_security_path, required=False),
    ]
    active_specs = [item for item in specs if item is not None]
    if not active_specs:
        raise ValueError("at least one benchmark path is required")
    suite_report: BenchmarkSuitesReport = run_benchmark_suites(active_specs)
    root = Path(output_dir)
    suite_report.write(root)
    required = [item.name for item in active_specs if item.required]
    optional = [item.name for item in active_specs if not item.required]
    recommendations: list[str] = []
    missing_required = [item.name for item in suite_report.suites if item.status == "failed" and "missing" in item.reason]
    if missing_required:
        recommendations.append("Provide local benchmark JSONL files for required suites before claiming benchmark coverage.")
    if suite_report.aggregate_score < 0.75:
        recommendations.append("Investigate benchmark failures before promoting the checkpoint.")
    if "custom_security" not in optional:
        recommendations.append("Add Mythos-owned custom security regression suite for non-public holdout coverage.")
    report = BenchmarkPackReport(
        status=suite_report.status,
        strict=config.strict,
        required_suites=required,
        optional_suites=optional,
        suite_report=suite_report.model_dump(),
        recommendations=recommendations,
    )
    (root / "benchmark_pack_report.json").write_text(json.dumps(report.model_dump(), indent=2, sort_keys=True), encoding="utf-8")
    return report


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Mythos HumanEval/MBPP/SWE/CyberSec benchmark pack.")
    parser.add_argument("--human-eval")
    parser.add_argument("--mbpp")
    parser.add_argument("--swe-bench")
    parser.add_argument("--cyberseceval")
    parser.add_argument("--custom-security")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--non-strict", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    report = run_benchmark_pack(
        BenchmarkPackConfig(
            human_eval_path=args.human_eval,
            mbpp_path=args.mbpp,
            swe_bench_path=args.swe_bench,
            cyberseceval_path=args.cyberseceval,
            custom_security_path=args.custom_security,
            strict=not args.non_strict,
        ),
        output_dir=args.output_dir,
    )
    print(json.dumps(report.model_dump(), indent=2, sort_keys=True))
    if report.status != "passed":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
