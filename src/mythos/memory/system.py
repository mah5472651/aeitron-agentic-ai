"""Native unified memory system."""

from __future__ import annotations

from typing import Any

import time

from pydantic import Field

from src.mythos.shared.schemas import StrictModel


class MemoryEntry(StrictModel):
    kind: str
    content: dict[str, Any]
    relevance: float = Field(ge=0.0, le=1.0)
    success_rate: float = Field(ge=0.0, le=1.0)
    usage_count: int = 0
    created_at_unix: float = Field(default_factory=time.time)


class MythosMemory:
    def __init__(self, *, project_id: str = "default") -> None:
        self.project_id = project_id
        self.entries: list[MemoryEntry] = []

    def remember_verified_fix(self, failure: str, fix: str, context: str) -> dict[str, Any]:
        entry = MemoryEntry(kind="verified_fix", content={"failure": failure, "fix": fix, "context": context}, relevance=0.8, success_rate=1.0)
        self.entries.append(entry)
        return entry.model_dump()

    def retrieve(self, query: str, *, limit: int = 5) -> dict[str, Any]:
        terms = {term.lower() for term in query.split() if term.strip()}
        ranked = sorted(
            self.entries,
            key=lambda entry: (
                len(terms.intersection(str(entry.content).lower().split())) * 0.4
                + entry.success_rate * 0.3
                + entry.relevance * 0.2
                + min(1.0, entry.usage_count / 10) * 0.1
            ),
            reverse=True,
        )
        return {"project_id": self.project_id, "hits": [entry.model_dump() for entry in ranked[:limit]]}
