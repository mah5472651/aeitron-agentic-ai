"""One-command benchmark pack runner for Mythos coding/security evaluation."""

from __future__ import annotations

import argparse
import gzip
import json
import time
import urllib.request
from pathlib import Path
from typing import Any

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
    production: bool = False
    min_human_eval_tasks: int = 164
    min_mbpp_tasks: int = 374
    min_swe_bench_tasks: int = 1
    min_cyberseceval_tasks: int = 1


class BenchmarkPackReport(StrictModel):
    status: str
    strict: bool
    required_suites: list[str]
    optional_suites: list[str]
    suite_report: dict
    recommendations: list[str] = Field(default_factory=list)


class BenchmarkMaterializationReport(StrictModel):
    status: str
    output_dir: str
    files: dict[str, str]
    rows: dict[str, int]
    sources: dict[str, str]
    created_at_unix: float = Field(default_factory=time.time)


PUBLIC_BENCHMARK_SOURCES = {
    "humaneval": "https://raw.githubusercontent.com/openai/human-eval/master/data/HumanEval.jsonl.gz",
    "mbpp": "https://raw.githubusercontent.com/google-research/google-research/master/mbpp/mbpp.jsonl",
}


def _download_bytes(url: str, *, max_bytes: int = 20_000_000) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "AeitronBenchmarkMaterializer/1.0"})
    with urllib.request.urlopen(request, timeout=60) as response:  # nosec B310 - URLs are fixed allowlisted constants.
        payload = response.read(max_bytes + 1)
    if len(payload) > max_bytes:
        raise ValueError(f"benchmark download exceeded {max_bytes} bytes: {url}")
    return payload


def _write_jsonl_rows(path: Path, rows: list[dict[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
    return len(rows)


def materialize_public_benchmark_pack(output_dir: str | Path) -> BenchmarkMaterializationReport:
    """Fetch public coding benchmarks into Mythos' local eval JSONL format."""
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    human_payload = gzip.decompress(_download_bytes(PUBLIC_BENCHMARK_SOURCES["humaneval"]))
    human_rows = [json.loads(line) for line in human_payload.decode("utf-8").splitlines() if line.strip()]
    mbpp_payload = _download_bytes(PUBLIC_BENCHMARK_SOURCES["mbpp"])
    mbpp_rows = [json.loads(line) for line in mbpp_payload.decode("utf-8").splitlines() if line.strip()]
    files = {
        "humaneval": str(root / "humaneval.jsonl"),
        "mbpp": str(root / "mbpp.jsonl"),
    }
    rows = {
        "humaneval": _write_jsonl_rows(Path(files["humaneval"]), human_rows),
        "mbpp": _write_jsonl_rows(Path(files["mbpp"]), mbpp_rows),
    }
    report = BenchmarkMaterializationReport(
        status="passed" if rows["humaneval"] >= 164 and rows["mbpp"] >= 374 else "failed",
        output_dir=str(root),
        files=files,
        rows=rows,
        sources=PUBLIC_BENCHMARK_SOURCES,
    )
    (root / "benchmark_materialization_report.json").write_text(json.dumps(report.model_dump(), indent=2, sort_keys=True), encoding="utf-8")
    return report


def _spec(name: str, kind: str, path: str | None, *, required: bool) -> BenchmarkSuiteSpec | None:
    if not path:
        return None
    return BenchmarkSuiteSpec(name=name, kind=kind, path=path, required=required)  # type: ignore[arg-type]


def _jsonl_count(path: str | None) -> int:
    if not path or not Path(path).exists():
        return 0
    return sum(1 for line in Path(path).read_text(encoding="utf-8-sig").splitlines() if line.strip())


def validate_production_benchmark_pack(config: BenchmarkPackConfig) -> list[str]:
    if not config.production:
        return []
    failures = []
    required = {
        "HumanEval": (config.human_eval_path, config.min_human_eval_tasks),
        "MBPP": (config.mbpp_path, config.min_mbpp_tasks),
        "SWE-Bench": (config.swe_bench_path, config.min_swe_bench_tasks),
        "CyberSecEval": (config.cyberseceval_path, config.min_cyberseceval_tasks),
    }
    for name, (path, minimum) in required.items():
        count = _jsonl_count(path)
        if count < minimum:
            failures.append(f"{name} requires at least {minimum} JSONL rows, found {count}")
    return failures


def run_benchmark_pack(config: BenchmarkPackConfig, *, output_dir: str | Path) -> BenchmarkPackReport:
    production_failures = validate_production_benchmark_pack(config)
    if production_failures:
        root = Path(output_dir)
        root.mkdir(parents=True, exist_ok=True)
        report = BenchmarkPackReport(
            status="failed",
            strict=config.strict,
            required_suites=[],
            optional_suites=[],
            suite_report={"status": "failed", "production_failures": production_failures},
            recommendations=production_failures,
        )
        (root / "benchmark_pack_report.json").write_text(json.dumps(report.model_dump(), indent=2, sort_keys=True), encoding="utf-8")
        return report
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
    parser.add_argument("--materialize-public", action="store_true", help="Download public HumanEval and MBPP JSONL files into --target-dir.")
    parser.add_argument("--target-dir", default="data/eval")
    parser.add_argument("--human-eval")
    parser.add_argument("--mbpp")
    parser.add_argument("--swe-bench")
    parser.add_argument("--cyberseceval")
    parser.add_argument("--custom-security")
    parser.add_argument("--output-dir", default="artifacts/aeitron/benchmark-pack")
    parser.add_argument("--non-strict", action="store_true")
    parser.add_argument("--production", action="store_true")
    parser.add_argument("--min-human-eval-tasks", type=int, default=164)
    parser.add_argument("--min-mbpp-tasks", type=int, default=374)
    parser.add_argument("--min-swe-bench-tasks", type=int, default=1)
    parser.add_argument("--min-cyberseceval-tasks", type=int, default=1)
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    if args.materialize_public:
        report = materialize_public_benchmark_pack(args.target_dir)
        print(json.dumps(report.model_dump(), indent=2, sort_keys=True))
        if report.status != "passed":
            raise SystemExit(2)
        return
    report = run_benchmark_pack(
        BenchmarkPackConfig(
            human_eval_path=args.human_eval,
            mbpp_path=args.mbpp,
            swe_bench_path=args.swe_bench,
            cyberseceval_path=args.cyberseceval,
            custom_security_path=args.custom_security,
            strict=not args.non_strict,
            production=args.production,
            min_human_eval_tasks=args.min_human_eval_tasks,
            min_mbpp_tasks=args.min_mbpp_tasks,
            min_swe_bench_tasks=args.min_swe_bench_tasks,
            min_cyberseceval_tasks=args.min_cyberseceval_tasks,
        ),
        output_dir=args.output_dir,
    )
    print(json.dumps(report.model_dump(), indent=2, sort_keys=True))
    if report.status != "passed":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
