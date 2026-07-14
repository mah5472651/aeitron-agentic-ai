"""Repository intelligence package for Aeitron MVP."""

from src.aeitron.indexing.context_builder import ContextBuilder, WorkspaceContextBuilder
from src.aeitron.indexing.repository_indexer import RepositoryIndexer
from src.aeitron.indexing.vector_index import (
    LocalVectorIndex,
    VectorBackendConfig,
    VectorIndexCapability,
    VectorSearchReport,
    VectorSearchResult,
    create_vector_index,
    vector_capabilities,
)

__all__ = [
    "ContextBuilder",
    "LocalVectorIndex",
    "RepositoryIndexer",
    "VectorBackendConfig",
    "VectorIndexCapability",
    "VectorSearchReport",
    "VectorSearchResult",
    "WorkspaceContextBuilder",
    "create_vector_index",
    "vector_capabilities",
]

