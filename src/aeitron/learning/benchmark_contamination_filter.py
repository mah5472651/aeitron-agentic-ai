"""Benchmark contamination filtering for dataset construction."""

from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path

from pydantic import Field

from src.aeitron.learning.quality import iter_jsonl, stable_hash
from src.aeitron.shared.schemas import StrictModel


DEFAULT_BENCHMARK_PATTERNS = [
    r"\bhumaneval\b",
    r"\bmbpp\b",
    r"\bswe-bench\b",
    r"\bcyberseceval\b",
    r"\bapps benchmark\b",
    r"\bcodecontests\b",
    r"\bpass@(?:1|5|10)\b",
    r"\bcanonical_solution\b",
    r"\btest_list\b",
    r"\bentry_point\b",
]


def load_patterns(path: str | Path | None) -> list[str]:
    if path is None:
        return DEFAULT_BENCHMARK_PATTERNS.copy()
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(payload, list):
        return [str(item) for item in payload]
    return [str(item) for item in payload.get("patterns", [])]


class BenchmarkContaminationHit(StrictModel):
    input_path: str
    line_number: int
    source: str
    pattern: str
    content_hash: str | None = None


class ContaminationHit(StrictModel):
    source_path: str
    line_number: int
    url: str | None = None
    reason: str
    content_hash: str


class BenchmarkContaminationFilterReport(StrictModel):
    input_paths: list[str]
    output_path: str
    accepted: int
    rejected: int
    hits: list[BenchmarkContaminationHit] = Field(default_factory=list)
    created_at_unix: float = Field(default_factory=time.time)


class ContaminationReport(StrictModel):
    scanned_rows: int
    hits: list[ContaminationHit] = Field(default_factory=list)
    blocked: bool
    created_at_unix: float = Field(default_factory=time.time)


class BenchmarkContaminationFilter:
    def __init__(self, patterns: list[str] | None = None) -> None:
        self.patterns = patterns or DEFAULT_BENCHMARK_PATTERNS
        self.compiled = [re.compile(pattern, re.IGNORECASE) for pattern in self.patterns]

    def find_pattern(self, text: str) -> str | None:
        for raw, compiled in zip(self.patterns, self.compiled):
            if compiled.search(text):
                return raw
        return None


class ContaminationDetector:
    """Read-only benchmark/holdout leakage scanner.

    Filtering and scanning live in this module so Aeitron has one authoritative
    benchmark-contamination policy instead of parallel basic/advanced versions.
    """

    def __init__(self, patterns: list[str] | None = None) -> None:
        self.filter = BenchmarkContaminationFilter(patterns)

    def scan_jsonl(self, paths: list[str | Path], *, block_on_hit: bool = True) -> ContaminationReport:
        hits: list[ContaminationHit] = []
        scanned = 0
        for path in paths:
            source = Path(path)
            for line_number, row in enumerate(iter_jsonl(source), start=1):
                scanned += 1
                text = str(row.get("text") or row.get("content") or "")
                pattern = self.filter.find_pattern(text)
                if pattern is not None:
                    hits.append(
                        ContaminationHit(
                            source_path=str(source),
                            line_number=line_number,
                            url=row.get("url"),
                            reason=f"benchmark_pattern:{pattern}",
                            content_hash=str(row.get("content_hash") or stable_hash(text)),
                        )
                    )
        return ContaminationReport(scanned_rows=scanned, hits=hits, blocked=bool(hits and block_on_hit))


def filter_benchmark_contamination_jsonl(
    input_paths: list[str | Path],
    output_path: str | Path,
    *,
    patterns: list[str] | None = None,
) -> BenchmarkContaminationFilterReport:
    detector = BenchmarkContaminationFilter(patterns)
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    accepted = 0
    rejected = 0
    hits: list[BenchmarkContaminationHit] = []
    with target.open("w", encoding="utf-8") as handle:
        for path in input_paths:
            for line_number, row in enumerate(iter_jsonl(path), start=1):
                text = str(row.get("text") or row.get("content") or "")
                pattern = detector.find_pattern(text)
                if pattern is not None:
                    rejected += 1
                    hits.append(
                        BenchmarkContaminationHit(
                            input_path=str(path),
                            line_number=line_number,
                            source=str(row.get("source") or "unknown"),
                            pattern=pattern,
                            content_hash=row.get("content_hash"),
                        )
                    )
                    continue
                handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
                accepted += 1
    return BenchmarkContaminationFilterReport(
        input_paths=[str(path) for path in input_paths],
        output_path=str(target),
        accepted=accepted,
        rejected=rejected,
        hits=hits,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Remove benchmark-contaminated rows from Aeitron JSONL shards.")
    parser.add_argument("--input", nargs="+", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--patterns")
    args = parser.parse_args()
    report = filter_benchmark_contamination_jsonl(args.input, args.output, patterns=load_patterns(args.patterns))
    print(json.dumps(report.model_dump(), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

