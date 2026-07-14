"""Contamination checks for benchmark and holdout leakage."""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from pydantic import Field

from src.aeitron.learning.quality import iter_jsonl, stable_hash
from src.aeitron.shared.schemas import StrictModel


DEFAULT_BENCHMARK_PATTERNS = [
    "HumanEval",
    "MBPP",
    "SWE-bench",
    "CyberSecEval",
    "pass@1",
    "def has_close_elements",
    "check(candidate)",
]


class ContaminationHit(StrictModel):
    source_path: str
    line_number: int
    url: str | None = None
    reason: str
    content_hash: str


class ContaminationReport(StrictModel):
    scanned_rows: int
    hits: list[ContaminationHit] = Field(default_factory=list)
    blocked: bool
    created_at_unix: float = Field(default_factory=time.time)


def load_patterns(path: str | Path | None) -> list[str]:
    if path is None:
        return DEFAULT_BENCHMARK_PATTERNS.copy()
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(payload, list):
        return [str(item) for item in payload]
    return [str(item) for item in payload.get("patterns", [])]


class ContaminationDetector:
    def __init__(self, patterns: list[str] | None = None) -> None:
        active = patterns or DEFAULT_BENCHMARK_PATTERNS
        self.patterns = [(pattern, re.compile(re.escape(pattern), re.IGNORECASE)) for pattern in active if pattern]

    def scan_jsonl(self, paths: list[str | Path], *, block_on_hit: bool = True) -> ContaminationReport:
        hits: list[ContaminationHit] = []
        scanned = 0
        for path in paths:
            source = Path(path)
            for line_number, row in enumerate(iter_jsonl(source), start=1):
                scanned += 1
                text = str(row.get("text") or row.get("content") or "")
                for label, pattern in self.patterns:
                    if pattern.search(text):
                        hits.append(
                            ContaminationHit(
                                source_path=str(source),
                                line_number=line_number,
                                url=row.get("url"),
                                reason=f"benchmark_pattern:{label}",
                                content_hash=stable_hash(text),
                            )
                        )
                        break
        return ContaminationReport(scanned_rows=scanned, hits=hits, blocked=bool(hits and block_on_hit))

