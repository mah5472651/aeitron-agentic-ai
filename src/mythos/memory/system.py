"""Consolidated Unified Memory System.

Wraps Phase 51 strict memory because it has the best anti-pollution contract.
"""

from __future__ import annotations

from typing import Any

from src.phase51.high_stability_reasoning_memory import MemoryKind, MemoryLayer, UnifiedMemoryManager


class MythosMemory:
    def __init__(self, *, project_id: str = "default") -> None:
        self.project_id = project_id
        self.manager = UnifiedMemoryManager(project_id=project_id)

    def remember_verified_fix(self, failure: str, fix: str, context: str) -> dict[str, Any]:
        entry = self.manager.ingest(
            MemoryLayer.EXPERIENCE,
            MemoryKind.VERIFIED_FIX,
            {"failure": failure, "fix": fix, "context": context},
            relevance=0.8,
            success_rate=1.0,
        )
        return entry.model_dump()

    def retrieve(self, query: str, *, limit: int = 5) -> dict[str, Any]:
        return self.manager.retrieve(query, limit=limit).model_dump()

