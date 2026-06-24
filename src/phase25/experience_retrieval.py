#!/usr/bin/env python
"""Retrieve failure/fix/outcome memory for planner context."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.phase16.experience_memory import ExperienceMemoryStore, ExperienceRecord


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class ExperienceRetrievalReport(StrictModel):
    run_id: str
    query: str
    records: list[dict[str, Any]]
    context_block: str
    recommendation: str
    created_at_unix: float = Field(default_factory=time.time)


class ExperienceRetriever:
    def __init__(self, paths: list[Path] | None = None) -> None:
        self.paths = paths or [
            ROOT / "artifacts" / "phase21" / "experience_memory.jsonl",
            ROOT / "artifacts" / "phase16" / "experience_memory.jsonl",
        ]

    def retrieve(self, query: str, *, limit: int = 8) -> ExperienceRetrievalReport:
        records = self._load_all()
        tokens = {token.lower() for token in query.replace("_", " ").split() if len(token) >= 3}
        scored: list[tuple[int, ExperienceRecord]] = []
        for record in records:
            haystack = " ".join([record.category, record.failure, record.fix, record.outcome, " ".join(record.tags)]).lower()
            score = sum(1 for token in tokens if token in haystack)
            if score:
                scored.append((score, record))
        scored.sort(key=lambda item: (-item[0], -item[1].created_at_unix))
        selected = [record for _, record in scored[:limit]]
        context = self._render(selected)
        return ExperienceRetrievalReport(
            run_id=f"phase25-{int(time.time())}",
            query=query,
            records=[record.model_dump() for record in selected],
            context_block=context,
            recommendation="Inject this context into planner prompts to avoid repeated failure modes." if selected else "No related experience found yet.",
        )

    def _load_all(self) -> list[ExperienceRecord]:
        records: list[ExperienceRecord] = []
        seen: set[str] = set()
        for path in self.paths:
            store = ExperienceMemoryStore(path)
            for record in store.load():
                if record.record_id in seen:
                    continue
                seen.add(record.record_id)
                records.append(record)
        return records

    def _render(self, records: list[ExperienceRecord]) -> str:
        if not records:
            return "No relevant experience memory found."
        parts = []
        for record in records:
            parts.append(
                f"- [{record.category}] failure={record.failure[:220]} | fix={record.fix[:220]} | "
                f"outcome={record.outcome} | confidence={record.confidence:.2f}"
            )
        return "\n".join(parts)


def write_report(report: ExperienceRetrievalReport, output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / f"{report.run_id}.json"
    latest_path = output_dir / "experience-retrieval-latest.json"
    json_path.write_text(json.dumps(report.model_dump(), indent=2, ensure_ascii=False), encoding="utf-8")
    latest_path.write_text(json.dumps(report.model_dump(), indent=2, ensure_ascii=False), encoding="utf-8")
    return json_path, latest_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Retrieve Phase 25 experience memory.")
    parser.add_argument("--query", default="model output verifier failure security patch")
    parser.add_argument("--limit", type=int, default=8)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "artifacts" / "phase25")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    report = ExperienceRetriever().retrieve(args.query, limit=args.limit)
    json_path, _ = write_report(report, args.output_dir)
    print(json.dumps({"run_id": report.run_id, "records": len(report.records), "json": str(json_path)}, indent=2))


if __name__ == "__main__":
    main()

