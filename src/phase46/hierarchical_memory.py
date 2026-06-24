#!/usr/bin/env python
"""Phase 46 hierarchical memory.

Five local layers: working, session, project, experience, and knowledge. The
contract is intentionally simple so future Redis/Postgres/vector stores can
replace individual layers without changing planner callers.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class MemoryLayer(str, Enum):
    WORKING = "working"
    SESSION = "session"
    PROJECT = "project"
    EXPERIENCE = "experience"
    KNOWLEDGE = "knowledge"


class MemoryItem(StrictModel):
    item_id: str
    layer: MemoryLayer
    content: str
    importance: float = Field(ge=0.0, le=1.0)
    tags: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at_unix: float = Field(default_factory=time.time)


class MemorySearchHit(StrictModel):
    score: float
    item: MemoryItem


class HierarchicalMemoryReport(StrictModel):
    run_id: str
    query: str
    layers: dict[str, int]
    hits: list[MemorySearchHit]
    context_block: str
    recommendation: str
    created_at_unix: float = Field(default_factory=time.time)


def stable_id(*parts: object) -> str:
    raw = "\x1f".join(str(part) for part in parts).encode("utf-8", errors="replace")
    return hashlib.sha256(raw).hexdigest()[:24]


def tokenize(text: str) -> set[str]:
    return {token.strip(".,:;()[]{}<>\"'").lower() for token in text.split() if len(token.strip()) >= 3}


class HierarchicalMemory:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or (ROOT / "artifacts" / "phase46")
        self.root.mkdir(parents=True, exist_ok=True)

    def add(
        self,
        layer: MemoryLayer | str,
        content: str,
        *,
        importance: float = 0.6,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        item_id: str | None = None,
    ) -> MemoryItem:
        parsed_layer = MemoryLayer(layer)
        item = MemoryItem(
            item_id=item_id or stable_id(parsed_layer.value, content, time.time_ns()),
            layer=parsed_layer,
            content=content,
            importance=importance,
            tags=tags or [],
            metadata=metadata or {},
        )
        self._append(item)
        return item

    def load(self, layer: MemoryLayer | str | None = None) -> list[MemoryItem]:
        layers = [MemoryLayer(layer)] if layer else list(MemoryLayer)
        items: list[MemoryItem] = []
        for current in layers:
            path = self._path(current)
            if not path.exists():
                continue
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    items.append(MemoryItem.model_validate(json.loads(line)))
                except (ValueError, TypeError, json.JSONDecodeError):
                    continue
        return items

    def search(self, query: str, *, limit: int = 10) -> list[MemorySearchHit]:
        query_tokens = tokenize(query)
        hits: list[MemorySearchHit] = []
        seen_content: set[tuple[str, str]] = set()
        layer_weight = {
            MemoryLayer.WORKING: 1.15,
            MemoryLayer.SESSION: 1.05,
            MemoryLayer.PROJECT: 1.0,
            MemoryLayer.EXPERIENCE: 1.12,
            MemoryLayer.KNOWLEDGE: 0.95,
        }
        for item in self.load():
            fingerprint = (item.layer.value, " ".join(item.content.lower().split()))
            if fingerprint in seen_content:
                continue
            seen_content.add(fingerprint)
            item_tokens = tokenize(" ".join([item.content, " ".join(item.tags)]))
            overlap = len(query_tokens & item_tokens)
            if overlap == 0:
                continue
            denominator = max(1, len(query_tokens | item_tokens))
            score = (overlap / denominator + item.importance * 0.35) * layer_weight[item.layer]
            hits.append(MemorySearchHit(score=min(1.0, score), item=item))
        hits.sort(key=lambda hit: (hit.score, hit.item.created_at_unix), reverse=True)
        return hits[:limit]

    def run(self, query: str, *, run_id: str, seed: bool = False, limit: int = 10) -> HierarchicalMemoryReport:
        if seed:
            self.seed_defaults()
        hits = self.search(query, limit=limit)
        layers = {layer.value: len(self.load(layer)) for layer in MemoryLayer}
        context = self.render_context(hits)
        return HierarchicalMemoryReport(
            run_id=run_id,
            query=query,
            layers=layers,
            hits=hits,
            context_block=context,
            recommendation="Inject layered memory into Phase 43/45 planner prompts before TaskGraph execution.",
        )

    def render_context(self, hits: list[MemorySearchHit]) -> str:
        if not hits:
            return "No hierarchical memory hits found."
        return "\n".join(f"- {hit.item.layer.value} score={hit.score:.3f}: {' '.join(hit.item.content.split())[:360]}" for hit in hits)

    def seed_defaults(self) -> None:
        defaults = [
            (
                MemoryLayer.KNOWLEDGE,
                "Secure login systems require hashing, rate limiting, MFA-ready flows, session revocation, and audit logs.",
                ["login", "security"],
                0.82,
            ),
            (
                MemoryLayer.EXPERIENCE,
                "Past architecture runs failed when planner output skipped verifier and security gates.",
                ["planner", "verifier"],
                0.86,
            ),
            (
                MemoryLayer.PROJECT,
                "This repository routes serious agent work through Phase 40, Phase 43, TaskGraph, critic, verifier, and memory.",
                ["phase40", "architecture"],
                0.78,
            ),
        ]
        existing = {item.item_id for item in self.load()}
        for layer, content, tags, importance in defaults:
            item_id = stable_id("seed", layer.value, content)
            if item_id not in existing:
                self.add(layer, content, tags=tags, importance=importance, item_id=item_id)

    def _append(self, item: MemoryItem) -> None:
        path = self._path(item.layer)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(item.model_dump(), ensure_ascii=False) + "\n")

    def _path(self, layer: MemoryLayer) -> Path:
        return self.root / f"{layer.value}.jsonl"


def write_report(report: HierarchicalMemoryReport, output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / f"{report.run_id}.json"
    latest_path = output_dir / "hierarchical-memory-latest.json"
    json_path.write_text(json.dumps(report.model_dump(), indent=2, ensure_ascii=False), encoding="utf-8")
    latest_path.write_text(json.dumps(report.model_dump(), indent=2, ensure_ascii=False), encoding="utf-8")
    return json_path, latest_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Phase 46 hierarchical memory search.")
    parser.add_argument("--query", default="secure planner verifier")
    parser.add_argument("--run-id", default=f"phase46-{int(time.time())}")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "artifacts" / "phase46")
    parser.add_argument("--seed", action="store_true")
    parser.add_argument("--limit", type=int, default=10)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    memory = HierarchicalMemory(args.output_dir)
    report = memory.run(args.query, run_id=args.run_id, seed=args.seed, limit=args.limit)
    json_path, _ = write_report(report, args.output_dir)
    print(json.dumps({"run_id": report.run_id, "hits": len(report.hits), "layers": report.layers, "json": str(json_path)}, indent=2))


if __name__ == "__main__":
    main()
