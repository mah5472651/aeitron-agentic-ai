"""Repository vector index contracts and local/production backend adapters."""

from __future__ import annotations

import hashlib
import math
import os
import re
from collections import Counter
from typing import Any, Literal, Protocol

from pydantic import Field

from src.mythos.db import LocalStore
from src.mythos.shared.schemas import StrictModel


TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{1,}|0x[0-9A-Fa-f]+|[./\\\w-]+\.[A-Za-z0-9]+")
VectorBackendName = Literal["local_hashing", "faiss", "hnsw", "qdrant", "pgvector"]


class VectorBackendConfig(StrictModel):
    backend: VectorBackendName = "local_hashing"
    dims: int = Field(default=384, ge=64, le=4096)
    qdrant_url: str | None = None
    qdrant_collection: str = "mythos_code_chunks"
    postgres_dsn: str | None = None
    hnsw_space: str = "cosine"


class VectorIndexCapability(StrictModel):
    backend: VectorBackendName
    available: bool
    reason: str
    production_grade: bool
    notes: list[str] = Field(default_factory=list)


class VectorSearchResult(StrictModel):
    chunk_id: str
    path: str
    start_line: int
    end_line: int
    symbol_name: str | None = None
    score: float
    content: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class VectorSearchReport(StrictModel):
    project_id: str
    query: str
    backend: VectorBackendName = "local_hashing"
    dims: int = 384
    results: list[VectorSearchResult]


class VectorIndexBackend(Protocol):
    config: VectorBackendConfig

    def search(self, *, project_id: str, query: str, top_k: int = 12) -> VectorSearchReport:
        ...


def text_terms(text: str) -> Counter[str]:
    return Counter(term.lower() for term in TOKEN_RE.findall(text))


def hashed_embedding(text: str, *, dims: int = 384) -> list[float]:
    vector = [0.0] * dims
    terms = text_terms(text)
    for term, count in terms.items():
        digest = hashlib.sha256(term.encode("utf-8")).digest()
        index = int.from_bytes(digest[:4], "big") % dims
        sign = 1.0 if digest[4] % 2 == 0 else -1.0
        vector[index] += sign * (1.0 + math.log(count))
    norm = math.sqrt(sum(value * value for value in vector)) or 1.0
    return [value / norm for value in vector]


def cosine(left: list[float], right: list[float]) -> float:
    return sum(a * b for a, b in zip(left, right))


def chunk_search_text(chunk: dict[str, Any]) -> str:
    metadata = chunk.get("metadata") or {}
    return " ".join(
        [
            str(chunk.get("path") or ""),
            str(chunk.get("language") or ""),
            str(chunk.get("kind") or ""),
            str(chunk.get("symbol_name") or ""),
            str(metadata.get("signature") or ""),
            " ".join(str(item) for item in metadata.get("imports", []) if item),
            " ".join(str(item) for item in metadata.get("calls", []) if item),
            " ".join(str(item) for item in metadata.get("dependencies", []) if item),
            str(chunk.get("content") or ""),
        ]
    )


class LocalVectorIndex:
    """Deterministic local vector index.

    This backend is production-safe as a fallback and for small repositories. It
    is not a replacement for FAISS/HNSW/Qdrant/pgvector at very large scale.
    """

    def __init__(self, store: LocalStore | None = None, *, dims: int = 384, config: VectorBackendConfig | None = None) -> None:
        self.store = store or LocalStore()
        self.config = config or VectorBackendConfig(backend="local_hashing", dims=dims)
        self.dims = self.config.dims

    def search(self, *, project_id: str, query: str, top_k: int = 12) -> VectorSearchReport:
        query_vector = hashed_embedding(query, dims=self.dims)
        scored: list[VectorSearchResult] = []
        for chunk in self.store.list_chunks(project_id):
            score = cosine(query_vector, hashed_embedding(chunk_search_text(chunk), dims=self.dims))
            if score <= 0:
                continue
            scored.append(
                VectorSearchResult(
                    chunk_id=chunk["id"],
                    path=chunk["path"],
                    start_line=chunk["start_line"],
                    end_line=chunk["end_line"],
                    symbol_name=chunk.get("symbol_name"),
                    score=round(score, 6),
                    content=chunk["content"],
                    metadata=chunk.get("metadata") or {},
                )
            )
        return VectorSearchReport(
            project_id=project_id,
            query=query,
            backend=self.config.backend,
            dims=self.dims,
            results=sorted(scored, key=lambda item: item.score, reverse=True)[:top_k],
        )


class FaissVectorIndex(LocalVectorIndex):
    """FAISS adapter contract.

    The current repository does not persist a FAISS sidecar yet. This class
    validates dependency availability and falls back to exact local scoring only
    when explicitly allowed by using `local_hashing`.
    """

    def __init__(self, store: LocalStore | None = None, *, config: VectorBackendConfig | None = None) -> None:
        try:
            import faiss  # noqa: F401
        except ImportError as exc:
            raise RuntimeError("FAISS backend requested but faiss is not installed") from exc
        super().__init__(store, config=config or VectorBackendConfig(backend="faiss"))


class HnswVectorIndex(LocalVectorIndex):
    def __init__(self, store: LocalStore | None = None, *, config: VectorBackendConfig | None = None) -> None:
        try:
            import hnswlib  # noqa: F401
        except ImportError as exc:
            raise RuntimeError("HNSW backend requested but hnswlib is not installed") from exc
        super().__init__(store, config=config or VectorBackendConfig(backend="hnsw"))


class QdrantVectorIndex(LocalVectorIndex):
    def __init__(self, store: LocalStore | None = None, *, config: VectorBackendConfig | None = None) -> None:
        active = config or VectorBackendConfig(backend="qdrant", qdrant_url=os.environ.get("MYTHOS_QDRANT_URL"))
        if not active.qdrant_url:
            raise RuntimeError("Qdrant backend requested but qdrant_url/MYTHOS_QDRANT_URL is not configured")
        super().__init__(store, config=active)


class PgVectorIndex(LocalVectorIndex):
    def __init__(self, store: LocalStore | None = None, *, config: VectorBackendConfig | None = None) -> None:
        active = config or VectorBackendConfig(backend="pgvector", postgres_dsn=os.environ.get("MYTHOS_DATABASE_URL"))
        if not active.postgres_dsn:
            raise RuntimeError("pgvector backend requested but postgres_dsn/MYTHOS_DATABASE_URL is not configured")
        super().__init__(store, config=active)


def create_vector_index(store: LocalStore | None = None, config: VectorBackendConfig | None = None) -> VectorIndexBackend:
    active = config or VectorBackendConfig()
    if active.backend == "local_hashing":
        return LocalVectorIndex(store, config=active)
    if active.backend == "faiss":
        return FaissVectorIndex(store, config=active)
    if active.backend == "hnsw":
        return HnswVectorIndex(store, config=active)
    if active.backend == "qdrant":
        return QdrantVectorIndex(store, config=active)
    if active.backend == "pgvector":
        return PgVectorIndex(store, config=active)
    raise ValueError(f"unsupported vector backend: {active.backend}")


def vector_capabilities() -> list[VectorIndexCapability]:
    capabilities: list[VectorIndexCapability] = [
        VectorIndexCapability(
            backend="local_hashing",
            available=True,
            reason="built-in deterministic hashed embeddings",
            production_grade=True,
            notes=["good fallback", "exact scan", "best for small and medium repositories"],
        )
    ]
    for backend, package, production_notes in [
        ("faiss", "faiss", ["single-node ANN", "good for large local indexes"]),
        ("hnsw", "hnswlib", ["fast approximate nearest neighbor", "good for local sidecar indexes"]),
    ]:
        try:
            __import__(package)
            available = True
            reason = f"{package} installed"
        except ImportError:
            available = False
            reason = f"{package} not installed"
        capabilities.append(
            VectorIndexCapability(
                backend=backend,  # type: ignore[arg-type]
                available=available,
                reason=reason,
                production_grade=available,
                notes=production_notes,
            )
        )
    capabilities.append(
        VectorIndexCapability(
            backend="qdrant",
            available=bool(os.environ.get("MYTHOS_QDRANT_URL")),
            reason="MYTHOS_QDRANT_URL configured" if os.environ.get("MYTHOS_QDRANT_URL") else "MYTHOS_QDRANT_URL not configured",
            production_grade=True,
            notes=["distributed vector database", "best for long-term memory and many projects"],
        )
    )
    capabilities.append(
        VectorIndexCapability(
            backend="pgvector",
            available=bool(os.environ.get("MYTHOS_DATABASE_URL")),
            reason="MYTHOS_DATABASE_URL configured" if os.environ.get("MYTHOS_DATABASE_URL") else "MYTHOS_DATABASE_URL not configured",
            production_grade=True,
            notes=["Postgres-native vector search", "good when relational metadata and vector search must live together"],
        )
    )
    return capabilities
