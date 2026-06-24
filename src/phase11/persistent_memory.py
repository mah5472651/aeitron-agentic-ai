#!/usr/bin/env python
"""Persistent long-context memory gateway.

The gateway has a local in-process index for development and optional Redis,
PostgreSQL, and Qdrant sinks for production deployments. External dependencies
are imported lazily so Phase 11 remains runnable on a clean CPU dev machine.
"""

from __future__ import annotations

import json
import math
import os
import time
from dataclasses import asdict, dataclass, field
from hashlib import blake2b, sha256
from typing import Any


def now_ms() -> int:
    return int(time.time() * 1000)


@dataclass(frozen=True)
class MemoryRecord:
    record_id: str
    workspace: str
    source: str
    content: str
    embedding: list[float]
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at_ms: int = field(default_factory=now_ms)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class HashEmbedding:
    """Small deterministic embedding model for local architecture validation.

    It is not a semantic replacement for a trained code embedding model. It gives
    the memory stack a stable vector contract today, and can be swapped later
    without changing the retrieval/storage APIs.
    """

    def __init__(self, dimensions: int = 384) -> None:
        if dimensions <= 0:
            raise ValueError("dimensions must be positive")
        self.dimensions = dimensions

    def embed(self, text: str) -> list[float]:
        vector = [0.0] * self.dimensions
        for token in text.lower().replace("_", " ").split():
            digest = blake2b(token.encode("utf-8", errors="replace"), digest_size=8).digest()
            slot = int.from_bytes(digest[:4], "big") % self.dimensions
            sign = 1.0 if digest[-1] % 2 == 0 else -1.0
            vector[slot] += sign
        norm = math.sqrt(sum(value * value for value in vector)) or 1.0
        return [value / norm for value in vector]


def memory_record_id(workspace: str, source: str, content: str) -> str:
    return sha256(f"{workspace}\x1f{source}\x1f{content}".encode("utf-8", errors="replace")).hexdigest()[:32]


def cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    numerator = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(a * a for a in left)) or 1.0
    right_norm = math.sqrt(sum(b * b for b in right)) or 1.0
    return numerator / (left_norm * right_norm)


class PersistentMemoryGateway:
    """Unified memory sink for local, Redis, Postgres, and Qdrant backends."""

    def __init__(
        self,
        *,
        workspace: str,
        redis_url: str | None = None,
        postgres_dsn: str | None = None,
        qdrant_url: str | None = None,
        qdrant_collection: str = "phase11_long_context_memory",
        embedding_dimensions: int = 384,
        strict_external: bool = False,
    ) -> None:
        self.workspace = workspace
        self.redis_url = redis_url or os.environ.get("PHASE11_REDIS_URL")
        self.postgres_dsn = postgres_dsn or os.environ.get("PHASE11_POSTGRES_DSN")
        self.qdrant_url = qdrant_url or os.environ.get("PHASE11_QDRANT_URL")
        self.qdrant_collection = qdrant_collection
        self.strict_external = strict_external
        self.embedder = HashEmbedding(embedding_dimensions)
        self.local_records: dict[str, MemoryRecord] = {}
        self.last_errors: list[str] = []
        self._pg_pool: Any | None = None
        self._redis: Any | None = None
        self._qdrant: Any | None = None

    async def initialize(self) -> dict[str, Any]:
        results: dict[str, Any] = {"local": True, "redis": False, "postgres": False, "qdrant": False}
        if self.redis_url:
            results["redis"] = await self._try("redis", self._init_redis())
        if self.postgres_dsn:
            results["postgres"] = await self._try("postgres", self._init_postgres())
        if self.qdrant_url:
            results["qdrant"] = await self._try("qdrant", self._init_qdrant())
        return {"backends": results, "errors": list(self.last_errors)}

    def embed_text(self, text: str) -> list[float]:
        return self.embedder.embed(text)

    def build_record(self, *, source: str, content: str, metadata: dict[str, Any] | None = None) -> MemoryRecord:
        embedding = self.embed_text(f"{source}\n{content}")
        return MemoryRecord(
            record_id=memory_record_id(self.workspace, source, content),
            workspace=self.workspace,
            source=source,
            content=content,
            embedding=embedding,
            metadata=metadata or {},
        )

    async def upsert(self, records: list[MemoryRecord]) -> dict[str, Any]:
        for record in records:
            self.local_records[record.record_id] = record

        results: dict[str, Any] = {"local": len(records), "redis": 0, "postgres": 0, "qdrant": 0}
        if not records:
            return {"upserted": results, "errors": list(self.last_errors)}
        if self.redis_url:
            ok = await self._try("redis", self._upsert_redis(records))
            results["redis"] = len(records) if ok else 0
        if self.postgres_dsn:
            ok = await self._try("postgres", self._upsert_postgres(records))
            results["postgres"] = len(records) if ok else 0
        if self.qdrant_url:
            ok = await self._try("qdrant", self._upsert_qdrant(records))
            results["qdrant"] = len(records) if ok else 0
        return {"upserted": results, "errors": list(self.last_errors)}

    def search_local(self, query: str, *, limit: int = 8) -> list[dict[str, Any]]:
        query_vector = self.embed_text(query)
        scored = [
            (cosine_similarity(query_vector, record.embedding), record)
            for record in self.local_records.values()
        ]
        scored.sort(key=lambda item: item[0], reverse=True)
        return [
            {"score": score, "record": record.to_dict()}
            for score, record in scored[:limit]
        ]

    async def aclose(self) -> None:
        if self._redis is not None:
            await self._try("redis", self._redis.aclose())
        if self._pg_pool is not None:
            await self._try("postgres", self._pg_pool.close())

    async def _try(self, label: str, awaitable: Any) -> bool:
        try:
            await awaitable
            return True
        except Exception as exc:
            message = f"{label}: {type(exc).__name__}: {exc}"
            self.last_errors.append(message)
            if self.strict_external:
                raise
            return False

    async def _init_redis(self) -> None:
        import redis.asyncio as redis

        self._redis = redis.from_url(self.redis_url, decode_responses=True)
        await self._redis.ping()

    async def _upsert_redis(self, records: list[MemoryRecord]) -> None:
        if self._redis is None:
            await self._init_redis()
        pipe = self._redis.pipeline()
        for record in records:
            key = f"phase11:memory:{record.record_id}"
            pipe.hset(key, mapping={"payload": json.dumps(record.to_dict(), ensure_ascii=False)})
        await pipe.execute()

    async def _init_postgres(self) -> None:
        import asyncpg

        self._pg_pool = await asyncpg.create_pool(self.postgres_dsn, min_size=1, max_size=4)
        async with self._pg_pool.acquire() as conn:
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS phase11_memory_records (
                    record_id TEXT PRIMARY KEY,
                    workspace TEXT NOT NULL,
                    source TEXT NOT NULL,
                    content TEXT NOT NULL,
                    embedding JSONB NOT NULL,
                    metadata JSONB NOT NULL,
                    created_at_ms BIGINT NOT NULL
                )
                """
            )

    async def _upsert_postgres(self, records: list[MemoryRecord]) -> None:
        if self._pg_pool is None:
            await self._init_postgres()
        rows = [
            (
                record.record_id,
                record.workspace,
                record.source,
                record.content,
                json.dumps(record.embedding),
                json.dumps(record.metadata),
                record.created_at_ms,
            )
            for record in records
        ]
        async with self._pg_pool.acquire() as conn:
            await conn.executemany(
                """
                INSERT INTO phase11_memory_records
                    (record_id, workspace, source, content, embedding, metadata, created_at_ms)
                VALUES ($1, $2, $3, $4, $5::jsonb, $6::jsonb, $7)
                ON CONFLICT (record_id) DO UPDATE SET
                    content = EXCLUDED.content,
                    embedding = EXCLUDED.embedding,
                    metadata = EXCLUDED.metadata,
                    created_at_ms = EXCLUDED.created_at_ms
                """,
                rows,
            )

    async def _init_qdrant(self) -> None:
        from qdrant_client import AsyncQdrantClient
        from qdrant_client.http.models import Distance, VectorParams

        self._qdrant = AsyncQdrantClient(url=self.qdrant_url)
        collections = await self._qdrant.get_collections()
        existing = {item.name for item in collections.collections}
        if self.qdrant_collection not in existing:
            await self._qdrant.create_collection(
                collection_name=self.qdrant_collection,
                vectors_config=VectorParams(size=self.embedder.dimensions, distance=Distance.COSINE),
            )

    async def _upsert_qdrant(self, records: list[MemoryRecord]) -> None:
        from qdrant_client.http.models import PointStruct

        if self._qdrant is None:
            await self._init_qdrant()
        points = [
            PointStruct(
                id=record.record_id,
                vector=record.embedding,
                payload={
                    "workspace": record.workspace,
                    "source": record.source,
                    "content": record.content[:8000],
                    "metadata": record.metadata,
                    "created_at_ms": record.created_at_ms,
                },
            )
            for record in records
        ]
        await self._qdrant.upsert(collection_name=self.qdrant_collection, points=points)
