"""Repository vector index contract and local hashed embedding backend."""

from __future__ import annotations

import hashlib
import math
import re
from collections import Counter
from typing import Any

from pydantic import Field

from src.mythos.db import LocalStore
from src.mythos.shared.schemas import StrictModel


TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{1,}|0x[0-9A-Fa-f]+|[./\\\w-]+\.[A-Za-z0-9]+")


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
    backend: str = "local_hashing"
    results: list[VectorSearchResult]


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


class LocalVectorIndex:
    def __init__(self, store: LocalStore | None = None, *, dims: int = 384) -> None:
        self.store = store or LocalStore()
        self.dims = dims

    def search(self, *, project_id: str, query: str, top_k: int = 12) -> VectorSearchReport:
        query_vector = hashed_embedding(query, dims=self.dims)
        scored: list[VectorSearchResult] = []
        for chunk in self.store.list_chunks(project_id):
            metadata = chunk.get("metadata") or {}
            haystack = " ".join(
                [
                    str(chunk.get("path") or ""),
                    str(chunk.get("symbol_name") or ""),
                    str(metadata.get("signature") or ""),
                    " ".join(str(item) for item in metadata.get("dependencies", []) if item),
                    str(chunk.get("content") or ""),
                ]
            )
            score = cosine(query_vector, hashed_embedding(haystack, dims=self.dims))
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
                    metadata=metadata,
                )
            )
        return VectorSearchReport(
            project_id=project_id,
            query=query,
            results=sorted(scored, key=lambda item: item.score, reverse=True)[:top_k],
        )
