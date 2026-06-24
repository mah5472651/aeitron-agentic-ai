#!/usr/bin/env python
"""Phase 37 production vector memory layer.

This module upgrades Phase 25 token matching into a vector retrieval contract.
It works locally with deterministic hash embeddings and can optionally mirror
records into Qdrant and PostgreSQL/pgvector-compatible tables when those
services are configured.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.phase11.persistent_memory import HashEmbedding, MemoryRecord, PersistentMemoryGateway, cosine_similarity
from src.phase16.experience_memory import ExperienceMemoryStore, ExperienceRecord


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class VectorMemoryHit(StrictModel):
    score: float
    record_id: str
    source: str
    content: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class VectorMemoryReport(StrictModel):
    run_id: str
    mode: str
    indexed_records: int
    hits: list[VectorMemoryHit]
    backends: dict[str, Any]
    embedding_backend: str
    embedding_dimensions: int
    context_block: str
    recommendation: str
    created_at_unix: float = Field(default_factory=time.time)


def experience_to_content(record: ExperienceRecord) -> str:
    return "\n".join(
        [
            f"category: {record.category}",
            f"failure: {record.failure}",
            f"fix: {record.fix}",
            f"outcome: {record.outcome}",
            f"confidence: {record.confidence:.3f}",
            f"tags: {' '.join(record.tags)}",
        ]
    )


class SentenceTransformerEmbedding:
    """Optional semantic embedder with deterministic local fallback elsewhere."""

    def __init__(self, model_name: str, *, strict: bool = False) -> None:
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            if strict:
                raise RuntimeError("sentence-transformers is not installed") from exc
            raise
        self.model_name = model_name
        self.model = SentenceTransformer(model_name)
        self.dimensions = int(self.model.get_sentence_embedding_dimension() or 384)

    def embed(self, text: str) -> list[float]:
        vector = self.model.encode(text, normalize_embeddings=True)
        return [float(value) for value in vector.tolist()]


def build_embedder(dimensions: int, *, strict_external: bool) -> tuple[Any, str, int]:
    requested = os.environ.get("PHASE37_EMBEDDER", "hash").strip().lower()
    model_name = os.environ.get("PHASE37_EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2")
    if requested in {"sentence-transformers", "sentence_transformers", "semantic", "st"}:
        try:
            embedder = SentenceTransformerEmbedding(model_name, strict=strict_external)
            return embedder, f"sentence-transformers:{model_name}", embedder.dimensions
        except Exception:
            if strict_external:
                raise
    return HashEmbedding(dimensions), f"hash:{dimensions}", dimensions


class VectorExperienceMemory:
    """Vector retrieval for failure/fix/outcome records."""

    def __init__(
        self,
        *,
        workspace: str = "mythos",
        paths: list[Path] | None = None,
        index_path: Path | None = None,
        qdrant_url: str | None = None,
        postgres_dsn: str | None = None,
        embedding_dimensions: int = 384,
        strict_external: bool = False,
    ) -> None:
        self.workspace = workspace
        self.paths = paths or [
            ROOT / "artifacts" / "phase21" / "experience_memory.jsonl",
            ROOT / "artifacts" / "phase16" / "experience_memory.jsonl",
        ]
        self.index_path = index_path or (ROOT / "artifacts" / "phase37" / "vector-memory-index.jsonl")
        self.embedder, self.embedding_backend, self.embedding_dimensions = build_embedder(
            embedding_dimensions,
            strict_external=strict_external,
        )
        self.gateway = PersistentMemoryGateway(
            workspace=workspace,
            qdrant_url=qdrant_url or os.environ.get("PHASE37_QDRANT_URL") or os.environ.get("PHASE11_QDRANT_URL"),
            postgres_dsn=postgres_dsn or os.environ.get("PHASE37_POSTGRES_DSN") or os.environ.get("PHASE11_POSTGRES_DSN"),
            qdrant_collection=os.environ.get("PHASE37_QDRANT_COLLECTION", "phase37_experience_memory"),
            embedding_dimensions=self.embedding_dimensions,
            strict_external=strict_external,
        )
        self.local_records: dict[str, MemoryRecord] = {}

    async def initialize(self) -> dict[str, Any]:
        self._load_local_index()
        return await self.gateway.initialize()

    async def build_index(self, *, limit: int | None = None) -> dict[str, Any]:
        records = self._load_experience(limit=limit)
        memory_records = [self._to_memory_record(record) for record in records]
        self.local_records = {record.record_id: record for record in memory_records}
        self._write_local_index(memory_records)
        upsert = await self.gateway.upsert(memory_records)
        return {
            "source_records": len(records),
            "indexed_records": len(memory_records),
            "index_path": str(self.index_path),
            "upsert": upsert,
        }

    async def search(self, query: str, *, limit: int = 8, min_score: float = 0.0) -> list[VectorMemoryHit]:
        self._load_local_index()
        query_vector = self.embedder.embed(query)
        scored: list[tuple[float, MemoryRecord]] = []
        for record in self.local_records.values():
            score = cosine_similarity(query_vector, record.embedding)
            if score >= min_score:
                scored.append((score, record))
        scored.sort(key=lambda item: (item[0], item[1].created_at_ms), reverse=True)
        return [
            VectorMemoryHit(
                score=float(score),
                record_id=record.record_id,
                source=record.source,
                content=record.content,
                metadata=record.metadata,
            )
            for score, record in scored[:limit]
        ]

    async def run(self, query: str, *, run_id: str, limit: int, rebuild: bool) -> VectorMemoryReport:
        backends = await self.initialize()
        indexed = len(self.local_records)
        mode = "search"
        if rebuild or indexed == 0:
            mode = "index_and_search"
            build = await self.build_index()
            indexed = int(build["indexed_records"])
            backends = {"initialize": backends, "build": build}
        hits = await self.search(query, limit=limit)
        context = self.render_context(hits)
        recommendation = (
            "Inject these vector-ranked failure/fix/outcome memories into planner and critic prompts."
            if hits
            else "No vector memory hits found; promote more verified failures and fixes first."
        )
        return VectorMemoryReport(
            run_id=run_id,
            mode=mode,
            indexed_records=indexed,
            hits=hits,
            backends=backends,
            embedding_backend=self.embedding_backend,
            embedding_dimensions=self.embedding_dimensions,
            context_block=context,
            recommendation=recommendation,
        )

    def render_context(self, hits: list[VectorMemoryHit]) -> str:
        if not hits:
            return "No relevant vector experience memory found."
        lines = []
        for hit in hits:
            content = " ".join(hit.content.split())[:420]
            lines.append(f"- score={hit.score:.3f} source={hit.source} {content}")
        return "\n".join(lines)

    def _load_experience(self, *, limit: int | None) -> list[ExperienceRecord]:
        records: list[ExperienceRecord] = []
        seen: set[str] = set()
        for path in self.paths:
            store = ExperienceMemoryStore(path)
            for record in store.load():
                if record.record_id in seen:
                    continue
                seen.add(record.record_id)
                records.append(record)
                if limit and len(records) >= limit:
                    return records
        return records

    def _to_memory_record(self, record: ExperienceRecord) -> MemoryRecord:
        content = experience_to_content(record)
        embedding = self.embedder.embed(f"{record.category}\n{record.failure}\n{record.fix}\n{record.outcome}\n{' '.join(record.tags)}")
        return MemoryRecord(
            record_id=record.record_id,
            workspace=self.workspace,
            source=f"experience:{record.source_run_id}:{record.task_id}",
            content=content,
            embedding=embedding,
            metadata=record.model_dump(),
            created_at_ms=int(record.created_at_unix * 1000),
        )

    def _load_local_index(self) -> None:
        if self.local_records or not self.index_path.exists():
            return
        records: dict[str, MemoryRecord] = {}
        for line in self.index_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
                record = MemoryRecord(**payload)
                records[record.record_id] = record
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
        self.local_records = records

    def _write_local_index(self, records: list[MemoryRecord]) -> None:
        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        with self.index_path.open("w", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record.to_dict(), ensure_ascii=False) + "\n")


def write_report(report: VectorMemoryReport, output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / f"{report.run_id}.json"
    latest_path = output_dir / "vector-memory-latest.json"
    json_path.write_text(json.dumps(report.model_dump(), indent=2, ensure_ascii=False), encoding="utf-8")
    latest_path.write_text(json.dumps(report.model_dump(), indent=2, ensure_ascii=False), encoding="utf-8")
    return json_path, latest_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Phase 37 vector memory retrieval.")
    parser.add_argument("--query", default="model output verifier failure security patch")
    parser.add_argument("--run-id", default=f"phase37-{int(time.time())}")
    parser.add_argument("--limit", type=int, default=8)
    parser.add_argument("--source-limit", type=int)
    parser.add_argument("--workspace", default="mythos")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "artifacts" / "phase37")
    parser.add_argument("--index-path", type=Path)
    parser.add_argument("--qdrant-url")
    parser.add_argument("--postgres-dsn")
    parser.add_argument("--rebuild", action="store_true")
    parser.add_argument("--strict-external", action="store_true")
    return parser.parse_args()


async def async_main() -> int:
    args = parse_args()
    memory = VectorExperienceMemory(
        workspace=args.workspace,
        index_path=args.index_path,
        qdrant_url=args.qdrant_url,
        postgres_dsn=args.postgres_dsn,
        strict_external=args.strict_external,
    )
    if args.source_limit is not None:
        await memory.initialize()
        await memory.build_index(limit=args.source_limit)
    report = await memory.run(args.query, run_id=args.run_id, limit=args.limit, rebuild=args.rebuild)
    json_path, _ = write_report(report, args.output_dir)
    print(
        json.dumps(
            {
                "run_id": report.run_id,
                "mode": report.mode,
                "indexed_records": report.indexed_records,
                "hits": len(report.hits),
                "json": str(json_path),
            },
            indent=2,
        )
    )
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(async_main()))


if __name__ == "__main__":
    main()
