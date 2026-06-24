#!/usr/bin/env python
"""Failure/fix/outcome experience memory for self-improving architecture loops."""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any, Iterable

from pydantic import BaseModel, ConfigDict, Field


def record_id(*parts: object) -> str:
    raw = "\x1f".join(str(part) for part in parts).encode("utf-8", errors="replace")
    return f"exp-{hashlib.sha256(raw).hexdigest()[:24]}"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class ExperienceRecord(StrictModel):
    record_id: str
    source_run_id: str
    task_id: str
    category: str
    failure: str
    fix: str
    outcome: str
    confidence: float = Field(ge=0.0, le=1.0)
    created_at_unix: float = Field(default_factory=time.time)
    tags: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ExperienceMemoryStore:
    def __init__(self, path: str | Path = "artifacts/phase16/experience_memory.jsonl") -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, record: ExperienceRecord) -> None:
        if record.record_id in {item.record_id for item in self.load()}:
            return
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(record.model_dump_json() + "\n")

    def append_many(self, records: Iterable[ExperienceRecord]) -> int:
        existing_ids = {item.record_id for item in self.load()}
        count = 0
        with self.path.open("a", encoding="utf-8") as handle:
            for record in records:
                if record.record_id in existing_ids:
                    continue
                handle.write(record.model_dump_json() + "\n")
                existing_ids.add(record.record_id)
                count += 1
        return count

    def load(self, limit: int | None = None) -> list[ExperienceRecord]:
        if not self.path.exists():
            return []
        records: list[ExperienceRecord] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                records.append(ExperienceRecord.model_validate_json(line))
            except ValueError:
                continue
            if limit and len(records) >= limit:
                break
        return records

    def search(self, query: str, *, limit: int = 5) -> list[ExperienceRecord]:
        tokens = {token.lower() for token in query.split() if len(token) >= 3}
        scored: list[tuple[int, ExperienceRecord]] = []
        for record in self.load():
            haystack = " ".join(
                [record.category, record.failure, record.fix, record.outcome, " ".join(record.tags)]
            ).lower()
            score = sum(1 for token in tokens if token in haystack)
            if score:
                scored.append((score, record))
        scored.sort(key=lambda item: (-item[0], -item[1].created_at_unix))
        return [record for _, record in scored[:limit]]

    def promote_scorecard_failures(self, scorecard_path: str | Path) -> list[ExperienceRecord]:
        path = Path(scorecard_path)
        payload = json.loads(path.read_text(encoding="utf-8"))
        run_id = str(payload.get("run_id") or path.stem)
        records: list[ExperienceRecord] = []
        for section_name in ("mock", "real"):
            section = payload.get(section_name)
            if not isinstance(section, dict):
                continue
            for failure in section.get("failure_report") or []:
                task_id = str(failure.get("task_id") or "unknown")
                category = str(failure.get("category") or "unknown")
                failed_phase = str(failure.get("failed_phase") or "unknown")
                issue_type = str(failure.get("issue_type") or "unknown")
                recommendation = str(failure.get("recommendation") or "Inspect failure and add a targeted regression.")
                message = str(failure.get("message") or "")
                records.append(
                    ExperienceRecord(
                        record_id=record_id(run_id, section_name, task_id, category, failed_phase, issue_type),
                        source_run_id=run_id,
                        task_id=task_id,
                        category=category,
                        failure=f"{failed_phase}:{issue_type}: {message}",
                        fix=recommendation,
                        outcome="promoted_from_scorecard_failure",
                        confidence=max(0.0, min(1.0, 1.0 - float(failure.get("score") or 0.0))),
                        tags=[section_name, category, failed_phase, issue_type],
                        metadata={"scorecard": str(path), "failure": failure},
                    )
                )
        existing_ids = {item.record_id for item in self.load()}
        new_records = [record for record in records if record.record_id not in existing_ids]
        self.append_many(new_records)
        return new_records
