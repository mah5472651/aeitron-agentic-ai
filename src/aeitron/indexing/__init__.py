"""Repository intelligence package for Aeitron MVP."""

from src.aeitron.indexing.context_builder import (
    ContextBuilder,
    HybridRAGEngine,
    RAGEvaluationReport,
    RAGEvaluationTask,
    WorkspaceContextBuilder,
)
from src.aeitron.indexing.repository_indexer import RepositoryIndexer
from src.aeitron.indexing.vector_index import (
    LocalVectorIndex,
    ScratchCodeEmbeddingModel,
    ScratchEmbeddingConfig,
    VectorBackendConfig,
    VectorIndexCapability,
    VectorSearchReport,
    VectorSearchResult,
    VectorSyncReport,
    create_vector_index,
    save_scratch_embedding_checkpoint,
    vector_capabilities,
)

__all__ = [
    "ContextBuilder",
    "HybridRAGEngine",
    "RAGEvaluationReport",
    "RAGEvaluationTask",
    "LocalVectorIndex",
    "RepositoryIndexer",
    "ScratchCodeEmbeddingModel",
    "ScratchEmbeddingConfig",
    "VectorBackendConfig",
    "VectorIndexCapability",
    "VectorSearchReport",
    "VectorSearchResult",
    "VectorSyncReport",
    "WorkspaceContextBuilder",
    "create_vector_index",
    "save_scratch_embedding_checkpoint",
    "vector_capabilities",
]

