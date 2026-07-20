"""Repository vector index contracts and local/production backend adapters."""

from __future__ import annotations

import hashlib
import math
import os
import re
import uuid
from collections import Counter
from typing import Any, Literal, Protocol
from urllib.parse import urlparse

import httpx

from pydantic import Field

from src.aeitron.db import LocalStore
from src.aeitron.shared.schemas import StrictModel


TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{1,}|0x[0-9A-Fa-f]+|[./\\\w-]+\.[A-Za-z0-9]+")
VectorBackendName = Literal["local_hashing", "faiss", "hnsw", "qdrant", "pgvector"]


class VectorBackendConfig(StrictModel):
    backend: VectorBackendName = "local_hashing"
    dims: int = Field(default=384, ge=64, le=4096)
    qdrant_url: str | None = None
    qdrant_collection: str = "aeitron_code_chunks"
    postgres_dsn: str | None = None
    hnsw_space: str = "cosine"
    embedding_url: str | None = None
    embedding_model: str = "aeitron-code-embedding"


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


class VectorSyncReport(StrictModel):
    project_id: str
    backend: VectorBackendName
    collection: str
    indexed_chunks: int = Field(ge=0)
    deleted_stale_chunks: int = Field(ge=0)
    embedding_dimensions: int = Field(ge=1)
    revision_sha256: str


class VectorIndexBackend(Protocol):
    config: VectorBackendConfig

    def search(self, *, project_id: str, query: str, top_k: int = 12) -> VectorSearchReport:
        ...

    def sync_project(self, *, project_id: str, batch_size: int = 64) -> VectorSyncReport:
        ...


class EmbeddingProvider(Protocol):
    dims: int

    def embed(self, text: str) -> list[float]:
        ...

    def embed_many(self, texts: list[str]) -> list[list[float]]:
        ...


class LocalHashingEmbeddingProvider:
    def __init__(self, *, dims: int = 384) -> None:
        self.dims = dims

    def embed(self, text: str) -> list[float]:
        return hashed_embedding(text, dims=self.dims)

    def embed_many(self, texts: list[str]) -> list[list[float]]:
        return [self.embed(text) for text in texts]


class HttpEmbeddingProvider:
    """Production embedding provider contract.

    The endpoint must return either {"embedding": [...]} or OpenAI-style
    {"data": [{"embedding": [...]}]}. Missing or malformed vectors fail fast.
    """

    def __init__(self, *, endpoint: str, model: str, dims: int) -> None:
        self.endpoint = endpoint.rstrip("/")
        self.model = model
        self.dims = dims
        parsed = urlparse(self.endpoint)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("embedding endpoint must be an absolute HTTP(S) URL")
        if parsed.username or parsed.password:
            raise ValueError("embedding endpoint must not contain embedded credentials")
        if parsed.query or parsed.fragment:
            raise ValueError("embedding endpoint must not contain a query string or fragment")
        if parsed.scheme != "https" and parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("remote embedding endpoints must use HTTPS")

    def embed(self, text: str) -> list[float]:
        return self.embed_many([text])[0]

    def embed_many(self, texts: list[str]) -> list[list[float]]:
        if not texts or len(texts) > 256:
            raise ValueError("embedding batch size must be between 1 and 256")
        request_input: str | list[str] = texts[0] if len(texts) == 1 else texts
        response = httpx.post(
            self.endpoint,
            json={"model": self.model, "input": request_input},
            timeout=30.0,
        )
        response.raise_for_status()
        payload = response.json()
        if len(texts) == 1 and isinstance(payload.get("embedding"), list):
            vectors = [payload["embedding"]]
        else:
            data = payload.get("data")
            if not isinstance(data, list) or len(data) != len(texts):
                raise RuntimeError("embedding provider returned an invalid batch")
            ordered = sorted(data, key=lambda item: int(item.get("index", 0)))
            vectors = [item.get("embedding") for item in ordered]
        return [_validated_embedding(vector, dims=self.dims) for vector in vectors]


def create_embedding_provider(config: VectorBackendConfig, *, allow_local_hashing: bool) -> EmbeddingProvider:
    endpoint = config.embedding_url or os.environ.get("AEITRON_EMBEDDING_URL")
    if endpoint:
        return HttpEmbeddingProvider(endpoint=endpoint, model=config.embedding_model, dims=config.dims)
    if allow_local_hashing:
        return LocalHashingEmbeddingProvider(dims=config.dims)
    raise RuntimeError("production vector backend requires embedding_url/AEITRON_EMBEDDING_URL")


def _validated_embedding(value: Any, *, dims: int) -> list[float]:
    if not isinstance(value, list) or len(value) != dims:
        raise RuntimeError(f"embedding provider returned invalid vector dimensions; expected {dims}")
    vector = [float(item) for item in value]
    if any(not math.isfinite(item) for item in vector):
        raise RuntimeError("embedding provider returned a non-finite vector")
    return vector


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

    This backend is a deterministic development fallback for small repositories.
    It is not semantic enough or scalable enough for production retrieval.
    """

    def __init__(self, store: LocalStore | None = None, *, dims: int = 384, config: VectorBackendConfig | None = None) -> None:
        self.store = store or LocalStore()
        self.config = config or VectorBackendConfig(backend="local_hashing", dims=dims)
        self.dims = self.config.dims
        self.embedding_provider = create_embedding_provider(self.config, allow_local_hashing=True)

    def search(self, *, project_id: str, query: str, top_k: int = 12) -> VectorSearchReport:
        query_vector = self.embedding_provider.embed(query)
        scored: list[VectorSearchResult] = []
        for chunk in self.store.list_chunks(project_id):
            score = cosine(query_vector, self.embedding_provider.embed(chunk_search_text(chunk)))
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

    def sync_project(self, *, project_id: str, batch_size: int = 64) -> VectorSyncReport:
        if batch_size < 1 or batch_size > 256:
            raise ValueError("vector sync batch_size must be between 1 and 256")
        chunks = self.store.list_chunks(project_id)
        digest = hashlib.sha256()
        for chunk in chunks:
            digest.update(str(chunk["id"]).encode("utf-8"))
            digest.update(hashlib.sha256(chunk_search_text(chunk).encode("utf-8")).digest())
        return VectorSyncReport(
            project_id=project_id,
            backend=self.config.backend,
            collection="local-exact-scan",
            indexed_chunks=len(chunks),
            deleted_stale_chunks=0,
            embedding_dimensions=self.dims,
            revision_sha256=digest.hexdigest(),
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
        active = config or VectorBackendConfig(backend="qdrant")
        active = active.model_copy(
            update={
                "qdrant_url": active.qdrant_url or os.environ.get("AEITRON_QDRANT_URL"),
                "embedding_url": active.embedding_url or os.environ.get("AEITRON_EMBEDDING_URL"),
            }
        )
        if not active.qdrant_url:
            raise RuntimeError("Qdrant backend requested but qdrant_url/AEITRON_QDRANT_URL is not configured")
        self.embedding_provider = create_embedding_provider(active, allow_local_hashing=False)
        try:
            from qdrant_client import QdrantClient  # type: ignore
        except ImportError as exc:
            raise RuntimeError("Qdrant backend requested but qdrant-client is not installed") from exc
        self.store = store or LocalStore()
        self.config = active
        self.dims = active.dims
        self.client = QdrantClient(url=active.qdrant_url)

    @staticmethod
    def _point_id(project_id: str, chunk_id: str) -> str:
        return str(uuid.uuid5(uuid.NAMESPACE_URL, f"aeitron:{project_id}:{chunk_id}"))

    def _ensure_collection(self) -> None:
        from qdrant_client import models

        try:
            exists = bool(self.client.collection_exists(self.config.qdrant_collection))
        except AttributeError:
            try:
                self.client.get_collection(self.config.qdrant_collection)
                exists = True
            except Exception:
                exists = False
        if not exists:
            self.client.create_collection(
                collection_name=self.config.qdrant_collection,
                vectors_config=models.VectorParams(size=self.dims, distance=models.Distance.COSINE),
            )
        try:
            self.client.create_payload_index(
                collection_name=self.config.qdrant_collection,
                field_name="project_id",
                field_schema=models.PayloadSchemaType.KEYWORD,
                wait=True,
            )
        except Exception as exc:
            if "already exists" not in str(exc).lower():
                raise RuntimeError(f"Qdrant payload index creation failed: {exc}") from exc

    def sync_project(self, *, project_id: str, batch_size: int = 64) -> VectorSyncReport:
        from qdrant_client import models

        if batch_size < 1 or batch_size > 256:
            raise ValueError("Qdrant batch_size must be between 1 and 256")
        chunks = self.store.list_chunks(project_id)
        self._ensure_collection()
        digest = hashlib.sha256()
        desired_ids: set[str] = set()
        for start in range(0, len(chunks), batch_size):
            batch = chunks[start : start + batch_size]
            texts = [chunk_search_text(chunk) for chunk in batch]
            vectors = self.embedding_provider.embed_many(texts)
            if len(vectors) != len(batch):
                raise RuntimeError("embedding provider returned the wrong number of vectors")
            points = []
            for chunk, text, vector in zip(batch, texts, vectors, strict=True):
                point_id = self._point_id(project_id, str(chunk["id"]))
                desired_ids.add(point_id)
                text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
                digest.update(point_id.encode("ascii"))
                digest.update(bytes.fromhex(text_hash))
                points.append(
                    models.PointStruct(
                        id=point_id,
                        vector=_validated_embedding(vector, dims=self.dims),
                        payload={
                            "project_id": project_id,
                            "chunk_id": str(chunk["id"]),
                            "path": str(chunk["path"]),
                            "start_line": int(chunk["start_line"]),
                            "end_line": int(chunk["end_line"]),
                            "symbol_name": chunk.get("symbol_name"),
                            "content": str(chunk["content"]),
                            "metadata": dict(chunk.get("metadata") or {}),
                            "content_sha256": text_hash,
                        },
                    )
                )
            try:
                self.client.upsert(
                    collection_name=self.config.qdrant_collection,
                    points=points,
                    wait=True,
                )
            except Exception as exc:
                raise RuntimeError(f"Qdrant upsert failed: {exc}") from exc

        existing_ids: set[str] = set()
        offset: Any = None
        project_filter = models.Filter(
            must=[models.FieldCondition(key="project_id", match=models.MatchValue(value=project_id))]
        )
        while True:
            records, offset = self.client.scroll(
                collection_name=self.config.qdrant_collection,
                scroll_filter=project_filter,
                limit=256,
                offset=offset,
                with_payload=False,
                with_vectors=False,
            )
            existing_ids.update(str(record.id) for record in records)
            if offset is None:
                break
        stale_ids = sorted(existing_ids - desired_ids)
        if stale_ids:
            self.client.delete(
                collection_name=self.config.qdrant_collection,
                points_selector=models.PointIdsList(points=stale_ids),
                wait=True,
            )
        return VectorSyncReport(
            project_id=project_id,
            backend="qdrant",
            collection=self.config.qdrant_collection,
            indexed_chunks=len(chunks),
            deleted_stale_chunks=len(stale_ids),
            embedding_dimensions=self.dims,
            revision_sha256=digest.hexdigest(),
        )

    def search(self, *, project_id: str, query: str, top_k: int = 12) -> VectorSearchReport:
        vector = self.embedding_provider.embed(query)
        try:
            from qdrant_client import models

            query_filter = models.Filter(
                must=[models.FieldCondition(key="project_id", match=models.MatchValue(value=project_id))]
            )
            if hasattr(self.client, "query_points"):
                response = self.client.query_points(
                    collection_name=self.config.qdrant_collection,
                    query=vector,
                    limit=top_k,
                    query_filter=query_filter,
                )
                hits = response.points
            else:
                hits = self.client.search(
                    collection_name=self.config.qdrant_collection,
                    query_vector=vector,
                    limit=top_k,
                    query_filter=query_filter,
                )
        except Exception as exc:
            raise RuntimeError(f"Qdrant search failed: {exc}") from exc
        results: list[VectorSearchResult] = []
        for hit in hits:
            payload = dict(getattr(hit, "payload", {}) or {})
            results.append(
                VectorSearchResult(
                    chunk_id=str(payload.get("chunk_id") or getattr(hit, "id", "")),
                    path=str(payload.get("path") or ""),
                    start_line=int(payload.get("start_line") or 0),
                    end_line=int(payload.get("end_line") or 0),
                    symbol_name=payload.get("symbol_name"),
                    score=round(float(getattr(hit, "score", 0.0)), 6),
                    content=str(payload.get("content") or ""),
                    metadata=dict(payload.get("metadata") or {}),
                )
            )
        return VectorSearchReport(project_id=project_id, query=query, backend="qdrant", dims=self.dims, results=results)


class PgVectorIndex(LocalVectorIndex):
    def __init__(self, store: LocalStore | None = None, *, config: VectorBackendConfig | None = None) -> None:
        active = config or VectorBackendConfig(backend="pgvector")
        active = active.model_copy(
            update={
                "postgres_dsn": active.postgres_dsn or os.environ.get("AEITRON_DATABASE_URL"),
                "embedding_url": active.embedding_url or os.environ.get("AEITRON_EMBEDDING_URL"),
            }
        )
        if not active.postgres_dsn:
            raise RuntimeError("pgvector backend requested but postgres_dsn/AEITRON_DATABASE_URL is not configured")
        create_embedding_provider(active, allow_local_hashing=False)
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
            production_grade=False,
            notes=["dev and validation fallback only", "exact scan", "not semantic enough for production"],
        )
    ]
    for backend, package, production_notes in [
        ("faiss", "faiss", ["dependency available", "persistent ANN implementation is not complete"]),
        ("hnsw", "hnswlib", ["dependency available", "persistent ANN implementation is not complete"]),
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
                production_grade=False,
                notes=production_notes,
            )
        )
    capabilities.append(
        VectorIndexCapability(
            backend="qdrant",
            available=bool(os.environ.get("AEITRON_QDRANT_URL") and os.environ.get("AEITRON_EMBEDDING_URL")),
            reason="AEITRON_QDRANT_URL and AEITRON_EMBEDDING_URL configured"
            if os.environ.get("AEITRON_QDRANT_URL") and os.environ.get("AEITRON_EMBEDDING_URL")
            else "AEITRON_QDRANT_URL or AEITRON_EMBEDDING_URL not configured",
            production_grade=bool(os.environ.get("AEITRON_QDRANT_URL") and os.environ.get("AEITRON_EMBEDDING_URL")),
            notes=["distributed vector database", "best for long-term memory and many projects"],
        )
    )
    capabilities.append(
        VectorIndexCapability(
            backend="pgvector",
            available=bool(os.environ.get("AEITRON_DATABASE_URL") and os.environ.get("AEITRON_EMBEDDING_URL")),
            reason="AEITRON_DATABASE_URL and AEITRON_EMBEDDING_URL configured"
            if os.environ.get("AEITRON_DATABASE_URL") and os.environ.get("AEITRON_EMBEDDING_URL")
            else "AEITRON_DATABASE_URL or AEITRON_EMBEDDING_URL not configured",
            production_grade=False,
            notes=["configuration contract only", "native pgvector persistence/query implementation is not complete"],
        )
    )
    return capabilities

