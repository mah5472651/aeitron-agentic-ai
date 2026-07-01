"""Context Builder for indexed Mythos workspaces."""

from __future__ import annotations

import math
import re
import time
import uuid
from collections import Counter
from typing import Any

from pydantic import Field

from src.mythos.db.local_store import LocalStore
from src.mythos.shared.schemas import StrictModel


TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{1,}|[A-Za-z]:\\[^\\s]+|[./\\w-]+\\.[A-Za-z0-9]+")


class ContextChunk(StrictModel):
    chunk_id: str
    path: str
    language: str | None = None
    start_line: int
    end_line: int
    symbol_name: str | None = None
    score: float
    reason: str
    content: str


class ContextBuildReport(StrictModel):
    context_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    project_id: str
    query: str
    token_budget: int
    estimated_tokens: int
    files: list[dict[str, Any]]
    chunks: list[ContextChunk]
    prompt_context: str
    created_at_unix: float = Field(default_factory=time.time)


def query_terms(query: str) -> Counter[str]:
    terms = [term.lower() for term in TOKEN_RE.findall(query)]
    return Counter(term for term in terms if len(term) > 1)


def chunk_terms(chunk: dict[str, Any]) -> Counter[str]:
    text = " ".join(
        str(value or "")
        for value in [
            chunk.get("path"),
            chunk.get("language"),
            chunk.get("symbol_name"),
            chunk.get("kind"),
            chunk.get("content"),
        ]
    )
    return query_terms(text)


def cosine_sparse(left: Counter[str], right: Counter[str]) -> float:
    if not left or not right:
        return 0.0
    dot = sum(left[key] * right.get(key, 0) for key in left)
    left_norm = math.sqrt(sum(value * value for value in left.values()))
    right_norm = math.sqrt(sum(value * value for value in right.values()))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return dot / (left_norm * right_norm)


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


class ContextBuilder:
    def __init__(self, store: LocalStore | None = None) -> None:
        self.store = store or LocalStore()

    def build(
        self,
        *,
        project_id: str,
        query: str,
        token_budget: int = 24_000,
        pinned_files: list[str] | None = None,
        max_chunks: int = 24,
    ) -> ContextBuildReport:
        project = self.store.get_project(project_id)
        if project is None:
            raise KeyError(f"unknown project: {project_id}")
        terms = query_terms(query)
        pinned = {path.replace("\\", "/") for path in (pinned_files or [])}
        chunks = self.store.list_chunks(project_id)
        ranked = sorted(
            (self.score_chunk(chunk, terms, pinned) for chunk in chunks),
            key=lambda item: item["score"],
            reverse=True,
        )
        selected: list[dict[str, Any]] = []
        reserved = max(512, int(token_budget * 0.25))
        remaining = max(512, token_budget - reserved)
        used = 0
        for item in ranked:
            if item["score"] <= 0 and item["path"] not in pinned:
                continue
            cost = estimate_tokens(item["content"])
            if selected and used + cost > remaining:
                continue
            selected.append(item)
            used += cost
            if len(selected) >= max_chunks:
                break
        files = self.file_summary(selected)
        prompt_context = self.render_prompt_context(project, query, selected)
        return ContextBuildReport(
            project_id=project_id,
            query=query,
            token_budget=token_budget,
            estimated_tokens=estimate_tokens(prompt_context),
            files=files,
            chunks=[
                ContextChunk(
                    chunk_id=item["id"],
                    path=item["path"],
                    language=item.get("language"),
                    start_line=item["start_line"],
                    end_line=item["end_line"],
                    symbol_name=item.get("symbol_name"),
                    score=round(float(item["score"]), 6),
                    reason=item["reason"],
                    content=item["content"],
                )
                for item in selected
            ],
            prompt_context=prompt_context,
        )

    def score_chunk(self, chunk: dict[str, Any], terms: Counter[str], pinned: set[str]) -> dict[str, Any]:
        semantic = cosine_sparse(terms, chunk_terms(chunk))
        path = str(chunk.get("path") or "")
        symbol = str(chunk.get("symbol_name") or "").lower()
        path_lower = path.lower()
        keyword_hits = sum(1 for term in terms if term in path_lower or term == symbol)
        keyword = min(1.0, keyword_hits / max(1, len(terms)))
        pinned_score = 1.0 if path in pinned else 0.0
        test_boost = 0.08 if "test" in path_lower or "spec" in path_lower else 0.0
        score = (0.55 * semantic) + (0.25 * keyword) + (0.15 * pinned_score) + test_boost
        reason_bits = []
        if semantic > 0:
            reason_bits.append("semantic")
        if keyword > 0:
            reason_bits.append("keyword")
        if pinned_score:
            reason_bits.append("pinned")
        if test_boost:
            reason_bits.append("test")
        output = dict(chunk)
        output["score"] = min(1.0, score)
        output["reason"] = ",".join(reason_bits) or "fallback"
        return output

    def file_summary(self, chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
        best: dict[str, dict[str, Any]] = {}
        for chunk in chunks:
            path = chunk["path"]
            current = best.get(path)
            if current is None or chunk["score"] > current["score"]:
                best[path] = {
                    "path": path,
                    "language": chunk.get("language"),
                    "score": round(float(chunk["score"]), 6),
                    "reason": chunk["reason"],
                }
        return sorted(best.values(), key=lambda item: item["score"], reverse=True)

    def render_prompt_context(self, project: dict[str, Any], query: str, chunks: list[dict[str, Any]]) -> str:
        parts = [
            "<repo_summary>",
            f"project={project['name']}",
            f"repo_path={project['repo_path']}",
            f"index_status={project['index_status']}",
            "</repo_summary>",
            "<user_request>",
            query,
            "</user_request>",
            "<files>",
        ]
        for chunk in chunks:
            symbol = f' symbol="{chunk.get("symbol_name")}"' if chunk.get("symbol_name") else ""
            parts.append(
                f'<file path="{chunk["path"]}" lines="{chunk["start_line"]}-{chunk["end_line"]}"{symbol}>'
            )
            parts.append(chunk["content"])
            parts.append("</file>")
        parts.append("</files>")
        return "\n".join(parts)
