"""Repository vector index contracts and local/production backend adapters."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import threading
import time
import uuid
from collections import Counter
from pathlib import Path
from typing import Any, Literal, Protocol
from urllib.parse import urlparse

import httpx

from pydantic import Field

from src.aeitron.db import LocalStore
from src.aeitron.learning.quality import SECRET_RE
from src.aeitron.shared.schemas import StrictModel

try:
    import torch
    from torch import nn
    from torch.nn import functional as F
except ImportError:  # pragma: no cover - dependency readiness handles this path.
    torch = None  # type: ignore[assignment]
    nn = None  # type: ignore[assignment]
    F = None  # type: ignore[assignment]


TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{1,}|0x[0-9A-Fa-f]+|[./\\\w-]+\.[A-Za-z0-9]+")
VectorBackendName = Literal["local_hashing", "faiss", "hnsw", "qdrant", "pgvector"]


class ScratchEmbeddingConfig(StrictModel):
    model_name: str = "Aeitron-Code-Embed-v1"
    vocab_size: int = Field(default=128_000, ge=256)
    hidden_size: int = Field(default=768, ge=64)
    num_layers: int = Field(default=12, ge=1)
    num_attention_heads: int = Field(default=12, ge=1)
    projection_dimension: int = Field(default=768, ge=64)
    intermediate_size: int = Field(default=3072, ge=128)
    max_sequence_length: int = Field(default=4096, ge=32)
    dropout: float = Field(default=0.1, ge=0.0, lt=1.0)
    temperature: float = Field(default=0.05, gt=0.0, le=1.0)

    def model_post_init(self, __context: Any) -> None:
        if self.hidden_size % self.num_attention_heads:
            raise ValueError("hidden_size must be divisible by num_attention_heads")


if nn is not None:
    class ScratchCodeEmbeddingModel(nn.Module):
        """Aeitron-owned random-initialized dual-encoder backbone."""

        def __init__(self, config: ScratchEmbeddingConfig) -> None:
            super().__init__()
            self.config = config
            self.token_embeddings = nn.Embedding(config.vocab_size, config.hidden_size)
            self.position_embeddings = nn.Embedding(config.max_sequence_length, config.hidden_size)
            layer = nn.TransformerEncoderLayer(
                d_model=config.hidden_size,
                nhead=config.num_attention_heads,
                dim_feedforward=config.intermediate_size,
                dropout=config.dropout,
                activation="gelu",
                batch_first=True,
                norm_first=True,
            )
            self.encoder = nn.TransformerEncoder(layer, num_layers=config.num_layers)
            self.output_norm = nn.LayerNorm(config.hidden_size)
            self.projection = nn.Linear(config.hidden_size, config.projection_dimension, bias=False)
            self.apply(self._initialize)

        @staticmethod
        def _initialize(module: nn.Module) -> None:
            if isinstance(module, (nn.Linear, nn.Embedding)):
                nn.init.normal_(module.weight, mean=0.0, std=0.02)
            if isinstance(module, nn.Linear) and module.bias is not None:
                nn.init.zeros_(module.bias)

        def forward(self, input_ids: "torch.Tensor", attention_mask: "torch.Tensor") -> "torch.Tensor":
            if input_ids.ndim != 2 or attention_mask.shape != input_ids.shape:
                raise ValueError("input_ids and attention_mask must have matching [batch, sequence] shapes")
            if input_ids.shape[1] > self.config.max_sequence_length:
                raise ValueError("embedding input exceeds configured maximum sequence length")
            if input_ids.numel() and (int(input_ids.min()) < 0 or int(input_ids.max()) >= self.config.vocab_size):
                raise ValueError("embedding input contains an out-of-vocabulary token ID")
            positions = torch.arange(input_ids.shape[1], device=input_ids.device).unsqueeze(0)
            hidden = self.token_embeddings(input_ids) + self.position_embeddings(positions)
            hidden = self.encoder(hidden, src_key_padding_mask=~attention_mask.bool())
            hidden = self.output_norm(hidden)
            weights = attention_mask.to(hidden.dtype).unsqueeze(-1)
            pooled = (hidden * weights).sum(dim=1) / weights.sum(dim=1).clamp_min(1.0)
            return F.normalize(self.projection(pooled).float(), p=2, dim=-1)

        def contrastive_loss(
            self,
            query_ids: "torch.Tensor",
            query_mask: "torch.Tensor",
            positive_ids: "torch.Tensor",
            positive_mask: "torch.Tensor",
        ) -> "torch.Tensor":
            query = self(query_ids, query_mask)
            positive = self(positive_ids, positive_mask)
            logits = query @ positive.transpose(0, 1) / self.config.temperature
            labels = torch.arange(logits.shape[0], device=logits.device)
            return 0.5 * (F.cross_entropy(logits, labels) + F.cross_entropy(logits.transpose(0, 1), labels))
else:
    class ScratchCodeEmbeddingModel:  # type: ignore[no-redef]
        def __init__(self, _config: ScratchEmbeddingConfig) -> None:
            raise RuntimeError("PyTorch is required for the Aeitron scratch embedding model")


def save_scratch_embedding_checkpoint(
    model: ScratchCodeEmbeddingModel,
    output_dir: str | Path,
    *,
    tokenizer_sha256: str,
    dataset_manifest_sha256: str,
) -> dict[str, Any]:
    if torch is None:
        raise RuntimeError("PyTorch is required to save an embedding checkpoint")
    try:
        from safetensors.torch import save_file
    except ImportError as exc:
        raise RuntimeError("safetensors is required for secure embedding checkpoint serialization") from exc
    for name, value in {
        "tokenizer_sha256": tokenizer_sha256,
        "dataset_manifest_sha256": dataset_manifest_sha256,
    }.items():
        if re.fullmatch(r"[0-9a-f]{64}", value) is None:
            raise ValueError(f"{name} must be a lowercase SHA-256 digest")
    target = Path(output_dir).resolve()
    target.mkdir(parents=True, exist_ok=True)
    checkpoint = target / "embedding_model.safetensors"
    temporary = target / ".embedding_model.safetensors.tmp"
    tensors = {
        name: value.detach().cpu().contiguous()
        for name, value in model.state_dict().items()
    }
    save_file(tensors, str(temporary), metadata={"format": "aeitron-scratch-embedding-v1"})
    os.replace(temporary, checkpoint)
    checkpoint_sha256 = hashlib.sha256(checkpoint.read_bytes()).hexdigest()
    manifest = {
        "schema_version": 1,
        "model_name": model.config.model_name,
        "scratch_only": True,
        "borrowed_weights": False,
        "projection_dimension": model.config.projection_dimension,
        "tokenizer_sha256": tokenizer_sha256,
        "dataset_manifest_sha256": dataset_manifest_sha256,
        "checkpoint_sha256": checkpoint_sha256,
        "config": model.config.model_dump(),
        "status": "built_not_gpu_proven",
    }
    manifest_path = target / "embedding_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {**manifest, "manifest_path": str(manifest_path), "checkpoint_path": str(checkpoint)}


class VectorBackendConfig(StrictModel):
    backend: VectorBackendName = "local_hashing"
    dims: int = Field(default=384, ge=64, le=4096)
    qdrant_url: str | None = None
    qdrant_collection: str = "aeitron_code_chunks"
    postgres_dsn: str | None = None
    hnsw_space: str = "cosine"
    embedding_url: str | None = None
    embedding_model: str = "Aeitron-Code-Embed-v1"
    embedding_manifest_path: str | None = None
    production_mode: bool = False
    qdrant_alias: str = "aeitron-rag-current"
    qdrant_expected_points: int = Field(default=100_000_000, ge=1)
    qdrant_replication_factor: int = Field(default=2, ge=1, le=9)


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
    organization_id: str = "local"
    project_id: str
    revision_id: str = ""
    query: str
    backend: VectorBackendName = "local_hashing"
    dims: int = 384
    results: list[VectorSearchResult]


class VectorSyncReport(StrictModel):
    organization_id: str = "local"
    project_id: str
    revision_id: str = ""
    backend: VectorBackendName
    collection: str
    indexed_chunks: int = Field(ge=0)
    deleted_stale_chunks: int = Field(ge=0)
    embedding_dimensions: int = Field(ge=1)
    revision_sha256: str


class VectorIndexBackend(Protocol):
    config: VectorBackendConfig

    def search(
        self, *, organization_id: str = "local", project_id: str,
        revision_id: str = "", query: str, top_k: int = 12,
    ) -> VectorSearchReport:
        ...

    def sync_project(
        self, *, organization_id: str = "local", project_id: str,
        revision_id: str = "", batch_size: int = 64,
    ) -> VectorSyncReport:
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
        self._lock = threading.Lock()
        self._failure_count = 0
        self._circuit_open_until = 0.0
        headers = {"User-Agent": "Aeitron-RAG/1"}
        api_key = os.environ.get("AEITRON_EMBEDDING_API_KEY")
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        cert: str | tuple[str, str] | None = None
        client_cert = os.environ.get("AEITRON_EMBEDDING_CLIENT_CERT")
        client_key = os.environ.get("AEITRON_EMBEDDING_CLIENT_KEY")
        if bool(client_cert) != bool(client_key):
            raise ValueError("embedding mTLS requires both client certificate and key")
        if client_cert and client_key:
            cert = (client_cert, client_key)
        self.client = httpx.Client(
            timeout=httpx.Timeout(30.0, connect=5.0),
            follow_redirects=False,
            trust_env=False,
            verify=os.environ.get("AEITRON_EMBEDDING_CA_BUNDLE", True),
            cert=cert,
            headers=headers,
            limits=httpx.Limits(max_connections=64, max_keepalive_connections=16),
        )

    def embed(self, text: str) -> list[float]:
        return self.embed_many([text])[0]

    def embed_many(self, texts: list[str]) -> list[list[float]]:
        if not texts or len(texts) > 256:
            raise ValueError("embedding batch size must be between 1 and 256")
        if any(not isinstance(text, str) or not text or len(text.encode("utf-8")) > 2_000_000 for text in texts):
            raise ValueError("embedding inputs must be non-empty strings no larger than 2MB")
        with self._lock:
            if time.monotonic() < self._circuit_open_until:
                raise RuntimeError("embedding provider circuit is open")
        request_input: str | list[str] = texts[0] if len(texts) == 1 else texts
        try:
            response = self.client.post(self.endpoint, json={"model": self.model, "input": request_input})
            response.raise_for_status()
            if len(response.content) > 64 * 1024 * 1024:
                raise RuntimeError("embedding provider response exceeded 64MB")
            payload = response.json()
        except (httpx.HTTPError, ValueError, RuntimeError) as exc:
            with self._lock:
                self._failure_count += 1
                if self._failure_count >= 3:
                    self._circuit_open_until = time.monotonic() + 30.0
            raise RuntimeError(f"embedding provider request failed: {type(exc).__name__}") from exc
        if len(texts) == 1 and isinstance(payload.get("embedding"), list):
            vectors = [payload["embedding"]]
        else:
            data = payload.get("data")
            if not isinstance(data, list) or len(data) != len(texts):
                raise RuntimeError("embedding provider returned an invalid batch")
            ordered = sorted(data, key=lambda item: int(item.get("index", 0)))
            vectors = [item.get("embedding") for item in ordered]
        validated = [_validated_embedding(vector, dims=self.dims) for vector in vectors]
        with self._lock:
            self._failure_count = 0
            self._circuit_open_until = 0.0
        return validated


def validate_embedding_manifest(config: VectorBackendConfig) -> dict[str, Any]:
    path_value = config.embedding_manifest_path or os.environ.get("AEITRON_EMBEDDING_MANIFEST")
    if not path_value:
        if config.production_mode:
            raise RuntimeError("production embeddings require AEITRON_EMBEDDING_MANIFEST")
        return {"status": "not_bound", "scratch_only": False}
    path = os.path.realpath(os.path.expanduser(path_value))
    try:
        with open(path, encoding="utf-8") as handle:
            manifest = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("embedding model manifest is unreadable") from exc
    if manifest.get("scratch_only") is not True or manifest.get("borrowed_weights") is not False:
        raise RuntimeError("embedding manifest does not prove Aeitron scratch-only weights")
    if str(manifest.get("model_name")) != config.embedding_model:
        raise RuntimeError("embedding manifest model name mismatch")
    if int(manifest.get("projection_dimension", 0)) != config.dims:
        raise RuntimeError("embedding manifest dimension mismatch")
    tokenizer_hash = str(manifest.get("tokenizer_sha256") or "")
    checkpoint_hash = str(manifest.get("checkpoint_sha256") or "")
    if not re.fullmatch(r"[0-9a-f]{64}", tokenizer_hash) or not re.fullmatch(r"[0-9a-f]{64}", checkpoint_hash):
        raise RuntimeError("embedding manifest requires tokenizer and checkpoint SHA-256 bindings")
    return manifest


def create_embedding_provider(config: VectorBackendConfig, *, allow_local_hashing: bool) -> EmbeddingProvider:
    endpoint = config.embedding_url or os.environ.get("AEITRON_EMBEDDING_URL")
    if endpoint:
        validate_embedding_manifest(config)
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

    def search(
        self, *, organization_id: str = "local", project_id: str,
        revision_id: str = "", query: str, top_k: int = 12,
    ) -> VectorSearchReport:
        self.store.require_project_access(project_id, organization_id)
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
            organization_id=organization_id,
            project_id=project_id,
            revision_id=revision_id,
            query=query,
            backend=self.config.backend,
            dims=self.dims,
            results=sorted(scored, key=lambda item: item.score, reverse=True)[:top_k],
        )

    def sync_project(
        self, *, organization_id: str = "local", project_id: str,
        revision_id: str = "", batch_size: int = 64,
    ) -> VectorSyncReport:
        self.store.require_project_access(project_id, organization_id)
        if batch_size < 1 or batch_size > 256:
            raise ValueError("vector sync batch_size must be between 1 and 256")
        chunks = self.store.list_chunks(project_id)
        digest = hashlib.sha256()
        for chunk in chunks:
            digest.update(str(chunk["id"]).encode("utf-8"))
            digest.update(hashlib.sha256(chunk_search_text(chunk).encode("utf-8")).digest())
        return VectorSyncReport(
            organization_id=organization_id,
            project_id=project_id,
            revision_id=revision_id,
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
        if active.qdrant_collection == "aeitron_code_chunks":
            version_suffix = hashlib.sha256(active.embedding_model.encode("utf-8")).hexdigest()[:12]
            active = active.model_copy(update={"qdrant_collection": f"aeitron_code_chunks_{version_suffix}"})
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

    def _promote_alias(self) -> None:
        if not self.config.production_mode:
            return
        if not hasattr(self.client, "update_collection_aliases"):
            raise RuntimeError("Qdrant client does not support atomic alias promotion")
        from qdrant_client import models

        operations: list[Any] = []
        try:
            aliases = self.client.get_aliases().aliases
            if any(getattr(item, "alias_name", None) == self.config.qdrant_alias for item in aliases):
                operations.append(
                    models.DeleteAliasOperation(
                        delete_alias=models.DeleteAlias(alias_name=self.config.qdrant_alias)
                    )
                )
        except Exception as exc:
            raise RuntimeError(f"Qdrant alias discovery failed: {exc}") from exc
        operations.append(
            models.CreateAliasOperation(
                create_alias=models.CreateAlias(
                    collection_name=self.config.qdrant_collection,
                    alias_name=self.config.qdrant_alias,
                )
            )
        )
        try:
            self.client.update_collection_aliases(change_aliases_operations=operations)
        except Exception as exc:
            raise RuntimeError(f"Qdrant alias promotion failed: {exc}") from exc

    @staticmethod
    def _point_id(organization_id: str, project_id: str, revision_id: str, chunk_id: str) -> str:
        del revision_id
        return str(uuid.uuid5(uuid.NAMESPACE_URL, f"aeitron:{organization_id}:{project_id}:{chunk_id}"))

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
            shard_count = (
                max(16, min(256, math.ceil(self.config.qdrant_expected_points / 10_000_000)))
                if self.config.production_mode
                else 1
            )
            self.client.create_collection(
                collection_name=self.config.qdrant_collection,
                vectors_config=models.VectorParams(
                    size=self.dims,
                    distance=models.Distance.COSINE,
                    on_disk=self.config.production_mode,
                ),
                shard_number=shard_count,
                replication_factor=self.config.qdrant_replication_factor if self.config.production_mode else 1,
                write_consistency_factor=(self.config.qdrant_replication_factor // 2) + 1 if self.config.production_mode else 1,
            )
        for field_name in ("organization_id", "project_id", "revision_id", "content_hash"):
            try:
                self.client.create_payload_index(
                    collection_name=self.config.qdrant_collection,
                    field_name=field_name,
                    field_schema=models.PayloadSchemaType.KEYWORD,
                    wait=True,
                )
            except Exception as exc:
                if "already exists" not in str(exc).lower():
                    raise RuntimeError(f"Qdrant payload index creation failed for {field_name}: {exc}") from exc

    @staticmethod
    def _tenant_filter(models: Any, organization_id: str, project_id: str, revision_id: str) -> Any:
        must = [
            models.FieldCondition(key="organization_id", match=models.MatchValue(value=organization_id)),
            models.FieldCondition(key="project_id", match=models.MatchValue(value=project_id)),
        ]
        if revision_id:
            must.append(models.FieldCondition(key="revision_id", match=models.MatchValue(value=revision_id)))
        return models.Filter(must=must)

    def sync_project(
        self, *, organization_id: str = "local", project_id: str,
        revision_id: str = "", batch_size: int = 64,
    ) -> VectorSyncReport:
        from qdrant_client import models

        if batch_size < 1 or batch_size > 256:
            raise ValueError("Qdrant batch_size must be between 1 and 256")
        project = self.store.require_project_access(project_id, organization_id)
        active_revision = str(project.get("active_index_revision") or "")
        revision_id = revision_id or active_revision
        if not revision_id or revision_id != active_revision:
            raise RuntimeError("Qdrant sync requires the active committed index revision")
        chunks = self.store.list_chunks(project_id)
        if any(str(chunk.get("index_revision") or "") != revision_id for chunk in chunks):
            raise RuntimeError("local chunks are not consistently bound to the active index revision")
        sensitive = [
            str(chunk["id"])
            for chunk in chunks
            if SECRET_RE.search(str(chunk.get("content") or ""))
            or "-----BEGIN PRIVATE KEY-----" in str(chunk.get("content") or "")
        ]
        if sensitive:
            raise RuntimeError(f"secret policy blocked embedding for {len(sensitive)} chunk(s)")
        self._ensure_collection()
        digest = hashlib.sha256()
        desired_ids: set[str] = set()
        for chunk in chunks:
            digest.update(str(chunk["id"]).encode("utf-8"))
            digest.update(str(chunk.get("chunk_hash") or "").encode("ascii"))
        existing_hashes: dict[str, str] = {}
        existing_ids: set[str] = set()
        offset: Any = None
        project_filter = self._tenant_filter(models, organization_id, project_id, "")
        while True:
            records, offset = self.client.scroll(
                collection_name=self.config.qdrant_collection,
                scroll_filter=project_filter,
                limit=256,
                offset=offset,
                with_payload=True,
                with_vectors=False,
            )
            for record in records:
                point_id = str(record.id)
                existing_ids.add(point_id)
                payload = dict(getattr(record, "payload", {}) or {})
                existing_hashes[point_id] = str(payload.get("content_hash") or "")
            if offset is None:
                break
        reused_ids: list[str] = []
        for start in range(0, len(chunks), batch_size):
            batch = chunks[start : start + batch_size]
            changed_batch: list[dict[str, Any]] = []
            for chunk in batch:
                point_id = self._point_id(organization_id, project_id, revision_id, str(chunk["id"]))
                desired_ids.add(point_id)
                if existing_hashes.get(point_id) == str(chunk.get("chunk_hash") or ""):
                    reused_ids.append(point_id)
                else:
                    changed_batch.append(chunk)
            if not changed_batch:
                continue
            texts = [chunk_search_text(chunk) for chunk in changed_batch]
            vectors = self.embedding_provider.embed_many(texts)
            if len(vectors) != len(changed_batch):
                raise RuntimeError("embedding provider returned the wrong number of vectors")
            points = []
            for chunk, text, vector in zip(changed_batch, texts, vectors, strict=True):
                point_id = self._point_id(organization_id, project_id, revision_id, str(chunk["id"]))
                text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
                points.append(
                    models.PointStruct(
                        id=point_id,
                        vector=_validated_embedding(vector, dims=self.dims),
                        payload={
                            "organization_id": organization_id,
                            "project_id": project_id,
                            "revision_id": revision_id,
                            "chunk_id": str(chunk["id"]),
                            "path": str(chunk["path"]),
                            "start_line": int(chunk["start_line"]),
                            "end_line": int(chunk["end_line"]),
                            "symbol_name": chunk.get("symbol_name"),
                            "language": str(chunk.get("language") or ""),
                            "source_kind": "repository",
                            "content_hash": str(chunk.get("chunk_hash") or text_hash),
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
        if reused_ids:
            if not hasattr(self.client, "set_payload"):
                if self.config.production_mode:
                    raise RuntimeError("Qdrant client cannot update revision payload for reused vectors")
            else:
                self.client.set_payload(
                    collection_name=self.config.qdrant_collection,
                    payload={"revision_id": revision_id},
                    points=reused_ids,
                    wait=True,
                )
        stale_ids = sorted(existing_ids - desired_ids)
        if stale_ids:
            self.client.delete(
                collection_name=self.config.qdrant_collection,
                points_selector=models.PointIdsList(points=stale_ids),
                wait=True,
            )
        self._promote_alias()
        return VectorSyncReport(
            organization_id=organization_id,
            project_id=project_id,
            revision_id=revision_id,
            backend="qdrant",
            collection=self.config.qdrant_collection,
            indexed_chunks=len(chunks),
            deleted_stale_chunks=len(stale_ids),
            embedding_dimensions=self.dims,
            revision_sha256=digest.hexdigest(),
        )

    def search(
        self, *, organization_id: str = "local", project_id: str,
        revision_id: str = "", query: str, top_k: int = 12,
    ) -> VectorSearchReport:
        if top_k < 1 or top_k > 500:
            raise ValueError("top_k must be between 1 and 500")
        project = self.store.require_project_access(project_id, organization_id)
        active_revision = str(project.get("active_index_revision") or "")
        revision_id = revision_id or active_revision
        if not revision_id or revision_id != active_revision:
            raise RuntimeError("Qdrant search requires the active committed index revision")
        vector = self.embedding_provider.embed(query)
        try:
            from qdrant_client import models

            query_filter = self._tenant_filter(models, organization_id, project_id, revision_id)
            if hasattr(self.client, "query_points"):
                response = self.client.query_points(
                    collection_name=self.config.qdrant_alias if self.config.production_mode else self.config.qdrant_collection,
                    query=vector,
                    limit=top_k,
                    query_filter=query_filter,
                )
                hits = response.points
            else:
                hits = self.client.search(
                    collection_name=self.config.qdrant_alias if self.config.production_mode else self.config.qdrant_collection,
                    query_vector=vector,
                    limit=top_k,
                    query_filter=query_filter,
                )
        except Exception as exc:
            raise RuntimeError(f"Qdrant search failed: {exc}") from exc
        results: list[VectorSearchResult] = []
        for hit in hits:
            payload = dict(getattr(hit, "payload", {}) or {})
            chunk_id = str(payload.get("chunk_id") or getattr(hit, "id", ""))
            chunk = self.store.get_chunk(chunk_id, project_id=project_id)
            if chunk is None or str(chunk.get("index_revision") or "") != revision_id:
                continue
            results.append(
                VectorSearchResult(
                    chunk_id=chunk_id,
                    path=str(chunk.get("path") or ""),
                    start_line=int(chunk.get("start_line") or 0),
                    end_line=int(chunk.get("end_line") or 0),
                    symbol_name=chunk.get("symbol_name"),
                    score=round(float(getattr(hit, "score", 0.0)), 6),
                    content=str(chunk.get("content") or ""),
                    metadata=dict(chunk.get("metadata") or {}),
                )
            )
        return VectorSearchReport(
            organization_id=organization_id,
            project_id=project_id,
            revision_id=revision_id,
            query=query,
            backend="qdrant",
            dims=self.dims,
            results=results,
        )


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

