"""MVP repository indexing engine.

This module gives Mythos a Cursor-style local repository intelligence baseline:
file inventory, content hashes, language detection, chunking, symbol extraction,
and storage into the MVP database. It intentionally works without Qdrant so the
system is testable on day one; vector storage can subscribe to the same chunks.
"""

from __future__ import annotations

import ast
import hashlib
import re
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator

from pydantic import Field

from src.mythos.db.local_store import LocalStore
from src.mythos.shared.schemas import StrictModel


LANGUAGE_BY_SUFFIX = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".c": "c",
    ".h": "c",
    ".cc": "cpp",
    ".cpp": "cpp",
    ".hpp": "cpp",
    ".sh": "bash",
    ".bash": "bash",
    ".md": "markdown",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".json": "json",
    ".toml": "toml",
}

DEFAULT_EXCLUDES = {
    ".git",
    ".hg",
    ".svn",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".venv",
    "venv",
    "env",
    "node_modules",
    "dist",
    "build",
    "target",
    ".next",
    ".turbo",
    ".idea",
    ".vscode",
}

SYMBOL_REGEX = re.compile(
    r"^\s*(?:async\s+)?(?:def|class|function|func|fn|struct|enum|interface|type)\s+([A-Za-z_][\w]*)",
    re.MULTILINE,
)


class IndexReport(StrictModel):
    project_id: str
    repo_path: str
    status: str
    file_count: int
    chunk_count: int
    symbol_count: int
    duration_ms: float
    errors: list[str] = Field(default_factory=list)


@dataclass(frozen=True)
class SourceFile:
    path: Path
    relative_path: str
    language: str
    content: str
    content_hash: str
    size_bytes: int


def stable_uuid(*parts: object) -> str:
    raw = "\x1f".join(str(part) for part in parts)
    digest = hashlib.sha256(raw.encode("utf-8", "surrogatepass")).hexdigest()
    return str(uuid.UUID(digest[:32]))


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


class RepositoryIndexer:
    def __init__(self, store: LocalStore | None = None) -> None:
        self.store = store or LocalStore()

    def index_project(
        self,
        *,
        project_id: str,
        include_suffixes: set[str] | None = None,
        max_file_bytes: int = 1_000_000,
        max_chunk_lines: int = 120,
    ) -> IndexReport:
        started = time.perf_counter()
        project = self.store.get_project(project_id)
        if project is None:
            raise KeyError(f"unknown project: {project_id}")
        repo_path = Path(str(project["repo_path"])).resolve()
        if not repo_path.exists() or not repo_path.is_dir():
            raise FileNotFoundError(f"project repo_path is not a directory: {repo_path}")

        self.store.update_project_index_status(project_id, "indexing")
        self.store.clear_index(project_id)

        errors: list[str] = []
        file_count = 0
        chunk_count = 0
        symbol_count = 0
        try:
            for source_file in self.iter_source_files(
                repo_path,
                include_suffixes=include_suffixes,
                max_file_bytes=max_file_bytes,
            ):
                file_count += 1
                file_id = self.store.upsert_workspace_file(
                    project_id=project_id,
                    path=source_file.relative_path,
                    language=source_file.language,
                    content_hash=source_file.content_hash,
                    size_bytes=source_file.size_bytes,
                )
                chunks = list(
                    self.chunk_file(
                        project_id=project_id,
                        file_id=file_id,
                        source_file=source_file,
                        max_chunk_lines=max_chunk_lines,
                    )
                )
                chunk_count += self.store.insert_code_chunks(chunks)
                symbol_count += sum(1 for chunk in chunks if chunk.get("symbol_name"))
        except Exception:
            self.store.update_project_index_status(project_id, "failed")
            raise

        self.store.update_project_index_status(project_id, "ready", indexed_at=time.time())
        return IndexReport(
            project_id=project_id,
            repo_path=str(repo_path),
            status="ready",
            file_count=file_count,
            chunk_count=chunk_count,
            symbol_count=symbol_count,
            duration_ms=(time.perf_counter() - started) * 1000,
            errors=errors,
        )

    def iter_source_files(
        self,
        repo_path: Path,
        *,
        include_suffixes: set[str] | None,
        max_file_bytes: int,
    ) -> Iterator[SourceFile]:
        suffixes = include_suffixes or set(LANGUAGE_BY_SUFFIX)
        for path in sorted(repo_path.rglob("*")):
            if not path.is_file():
                continue
            if any(part in DEFAULT_EXCLUDES for part in path.relative_to(repo_path).parts):
                continue
            suffix = path.suffix.lower()
            if suffix not in suffixes or suffix not in LANGUAGE_BY_SUFFIX:
                continue
            try:
                raw = path.read_bytes()
            except OSError:
                continue
            if len(raw) > max_file_bytes or b"\x00" in raw[:4096]:
                continue
            text = raw.decode("utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")
            yield SourceFile(
                path=path,
                relative_path=path.relative_to(repo_path).as_posix(),
                language=LANGUAGE_BY_SUFFIX[suffix],
                content=text,
                content_hash=hashlib.sha256(raw).hexdigest(),
                size_bytes=len(raw),
            )

    def chunk_file(
        self,
        *,
        project_id: str,
        file_id: str,
        source_file: SourceFile,
        max_chunk_lines: int,
    ) -> Iterable[dict[str, Any]]:
        if source_file.language == "python":
            chunks = list(self.python_symbol_chunks(source_file))
            if chunks:
                for chunk in chunks:
                    yield self.chunk_payload(project_id, file_id, source_file, **chunk)
                return
        for chunk in self.line_chunks(source_file, max_chunk_lines=max_chunk_lines):
            yield self.chunk_payload(project_id, file_id, source_file, **chunk)

    def python_symbol_chunks(self, source_file: SourceFile) -> Iterable[dict[str, Any]]:
        try:
            tree = ast.parse(source_file.content)
        except SyntaxError:
            return []
        lines = source_file.content.splitlines()
        chunks: list[dict[str, Any]] = []
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            start = max(1, getattr(node, "lineno", 1))
            end = max(start, getattr(node, "end_lineno", start))
            content = "\n".join(lines[start - 1 : end])
            chunks.append(
                {
                    "start_line": start,
                    "end_line": end,
                    "symbol_name": node.name,
                    "kind": "class" if isinstance(node, ast.ClassDef) else "function",
                    "content": content,
                    "metadata": {"parser": "python_ast"},
                }
            )
        return sorted(chunks, key=lambda item: (item["start_line"], item["end_line"]))

    def line_chunks(self, source_file: SourceFile, *, max_chunk_lines: int) -> Iterable[dict[str, Any]]:
        lines = source_file.content.splitlines()
        if not lines:
            return
        for start_index in range(0, len(lines), max_chunk_lines):
            end_index = min(len(lines), start_index + max_chunk_lines)
            content = "\n".join(lines[start_index:end_index])
            symbol_match = SYMBOL_REGEX.search(content)
            yield {
                "start_line": start_index + 1,
                "end_line": end_index,
                "symbol_name": symbol_match.group(1) if symbol_match else None,
                "kind": "module" if start_index == 0 else "chunk",
                "content": content,
                "metadata": {"parser": "line_chunker"},
            }

    def chunk_payload(
        self,
        project_id: str,
        file_id: str,
        source_file: SourceFile,
        *,
        start_line: int,
        end_line: int,
        symbol_name: str | None,
        kind: str,
        content: str,
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        chunk_hash = sha256_text(content)
        return {
            "id": stable_uuid(project_id, source_file.relative_path, start_line, end_line, chunk_hash),
            "project_id": project_id,
            "file_id": file_id,
            "path": source_file.relative_path,
            "language": source_file.language,
            "start_line": start_line,
            "end_line": end_line,
            "symbol_name": symbol_name,
            "kind": kind,
            "chunk_hash": chunk_hash,
            "token_count": estimate_tokens(content),
            "content": content,
            "metadata": metadata,
        }
