"""MVP repository indexing engine.

This module gives Aeitron a Cursor-style local repository intelligence baseline:
file inventory, content hashes, language detection, chunking, symbol extraction,
and storage into the MVP database. It intentionally works without Qdrant so the
system is testable on day one; vector storage can subscribe to the same chunks.
"""

from __future__ import annotations

import ast
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator

from pydantic import Field

from src.aeitron.db.local_store import LocalStore
from src.aeitron.learning.storage import ObjectStore, ObjectStoreConfig, create_object_store
from src.aeitron.shared.schemas import StrictModel


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

IMPORT_REGEX_BY_LANGUAGE = {
    "javascript": re.compile(r"^\s*import\s+.*?\s+from\s+['\"]([^'\"]+)['\"]|^\s*const\s+.+?=\s+require\(['\"]([^'\"]+)['\"]\)", re.MULTILINE),
    "typescript": re.compile(r"^\s*import\s+.*?\s+from\s+['\"]([^'\"]+)['\"]|^\s*const\s+.+?=\s+require\(['\"]([^'\"]+)['\"]\)", re.MULTILINE),
    "go": re.compile(r"^\s*import\s+(?:\(\s*)?\"([^\"]+)\"", re.MULTILINE),
    "rust": re.compile(r"^\s*use\s+([^;]+);", re.MULTILINE),
    "java": re.compile(r"^\s*import\s+([^;]+);", re.MULTILINE),
    "cpp": re.compile(r"^\s*#\s*include\s+[<\"]([^>\"]+)[>\"]", re.MULTILINE),
    "c": re.compile(r"^\s*#\s*include\s+[<\"]([^>\"]+)[>\"]", re.MULTILINE),
    "bash": re.compile(r"^\s*(?:source|\.)\s+(.+)$", re.MULTILINE),
}

CHUNKER_VERSION = "aeitron-code-chunker-v2"


@dataclass(frozen=True)
class PythonModuleFacts:
    imports: list[str]
    module_calls: list[str]
    module_mutations: list[str]


class IndexReport(StrictModel):
    project_id: str
    repo_path: str
    status: str
    file_count: int
    chunk_count: int
    symbol_count: int
    revision_id: str
    source_revision: str
    source_snapshot_sha256: str
    chunker_version: str = CHUNKER_VERSION
    changed_file_count: int = 0
    removed_file_count: int = 0
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
    def __init__(
        self,
        store: LocalStore | None = None,
        *,
        object_store: ObjectStore | None = None,
        production_mode: bool | None = None,
    ) -> None:
        self.store = store or LocalStore()
        self.production_mode = (
            production_mode
            if production_mode is not None
            else os.environ.get("AEITRON_ENV", "development").lower() == "production"
        )
        self.object_store = object_store
        if self.production_mode and self.object_store is None:
            uri = os.environ.get("AEITRON_RAG_OBJECT_STORE_URI") or os.environ.get("AEITRON_OBJECT_STORE_URI")
            if not uri or not uri.startswith("s3://"):
                raise RuntimeError("production repository indexing requires S3/MinIO snapshot storage")
            self.object_store = create_object_store(
                ObjectStoreConfig(
                    uri=uri,
                    endpoint_url=os.environ.get("AEITRON_OBJECT_STORE_ENDPOINT_URL"),
                )
            )

    def index_project(
        self,
        *,
        project_id: str,
        include_suffixes: set[str] | None = None,
        max_file_bytes: int = 1_000_000,
        max_chunk_lines: int = 120,
        max_chunk_tokens: int = 512,
        overlap_tokens: int = 64,
    ) -> IndexReport:
        started = time.perf_counter()
        project = self.store.get_project(project_id)
        if project is None:
            raise KeyError(f"unknown project: {project_id}")
        repo_path = Path(str(project["repo_path"])).resolve()
        if not repo_path.exists() or not repo_path.is_dir():
            raise FileNotFoundError(f"project repo_path is not a directory: {repo_path}")

        errors: list[str] = []
        if max_chunk_tokens < 128 or max_chunk_tokens > 4096:
            raise ValueError("max_chunk_tokens must be between 128 and 4096")
        if overlap_tokens < 0 or overlap_tokens >= max_chunk_tokens:
            raise ValueError("overlap_tokens must be non-negative and smaller than max_chunk_tokens")

        source_files = list(
            self.iter_source_files(
                repo_path,
                include_suffixes=include_suffixes,
                max_file_bytes=max_file_bytes,
            )
        )
        source_snapshot_sha256 = self.source_snapshot_sha256(source_files)
        source_revision = self.resolve_source_revision(repo_path, source_snapshot_sha256)
        snapshot_object = self.persist_source_snapshot(
            organization_id=str(project["organization_id"]),
            project_id=project_id,
            snapshot_sha256=source_snapshot_sha256,
            source_files=source_files,
        )
        previous_files = {
            str(row["path"]): str(row["content_hash"])
            for row in self.store.connection.execute(
                "SELECT path, content_hash FROM workspace_files WHERE project_id = ?",
                (project_id,),
            ).fetchall()
        }
        current_hashes = {item.relative_path: item.content_hash for item in source_files}
        changed_file_count = sum(
            1 for path, content_hash in current_hashes.items() if previous_files.get(path) != content_hash
        )
        removed_file_count = len(set(previous_files) - set(current_hashes))
        manifest_base = {
            "schema_version": 1,
            "organization_id": project["organization_id"],
            "project_id": project_id,
            "source_revision": source_revision,
            "source_snapshot_sha256": source_snapshot_sha256,
            "chunker_version": CHUNKER_VERSION,
            "max_chunk_tokens": max_chunk_tokens,
            "overlap_tokens": overlap_tokens,
            "source_snapshot_object": snapshot_object,
        }
        revision = self.store.begin_index_revision(
            project_id=project_id,
            source_revision=source_revision,
            source_snapshot_sha256=source_snapshot_sha256,
            chunker_version=CHUNKER_VERSION,
            manifest=manifest_base,
        )
        revision_id = str(revision["id"])
        files: list[dict[str, Any]] = []
        all_chunks: list[dict[str, Any]] = []
        try:
            for source_file in source_files:
                file_id = stable_uuid(
                    project["organization_id"], project_id, source_file.relative_path,
                    source_file.content_hash, CHUNKER_VERSION,
                )
                files.append(
                    {
                        "id": file_id,
                        "path": source_file.relative_path,
                        "language": source_file.language,
                        "content_hash": source_file.content_hash,
                        "size_bytes": source_file.size_bytes,
                    }
                )
                chunks = list(
                    self.chunk_file(
                        organization_id=str(project["organization_id"]),
                        project_id=project_id,
                        file_id=file_id,
                        source_file=source_file,
                        max_chunk_lines=max_chunk_lines,
                        max_chunk_tokens=max_chunk_tokens,
                        overlap_tokens=overlap_tokens,
                    )
                )
                all_chunks.extend(chunks)
            manifest = {
                **manifest_base,
                "file_count": len(files),
                "chunk_count": len(all_chunks),
                "symbol_count": sum(1 for chunk in all_chunks if chunk.get("symbol_name")),
                "changed_file_count": changed_file_count,
                "removed_file_count": removed_file_count,
            }
            manifest["manifest_sha256"] = sha256_text(json.dumps(manifest, sort_keys=True, separators=(",", ":")))
            self.store.commit_index_revision(
                revision_id=revision_id,
                files=files,
                chunks=all_chunks,
                manifest=manifest,
            )
        except Exception as exc:
            try:
                self.store.fail_index_revision(revision_id, str(exc))
            except RuntimeError:
                pass
            raise
        return IndexReport(
            project_id=project_id,
            repo_path=str(repo_path),
            status="ready",
            file_count=len(files),
            chunk_count=len(all_chunks),
            symbol_count=sum(1 for chunk in all_chunks if chunk.get("symbol_name")),
            revision_id=revision_id,
            source_revision=source_revision,
            source_snapshot_sha256=source_snapshot_sha256,
            changed_file_count=changed_file_count,
            removed_file_count=removed_file_count,
            duration_ms=(time.perf_counter() - started) * 1000,
            errors=errors,
        )

    @staticmethod
    def source_snapshot_sha256(source_files: list[SourceFile]) -> str:
        digest = hashlib.sha256()
        for source_file in sorted(source_files, key=lambda item: item.relative_path):
            digest.update(source_file.relative_path.encode("utf-8", "surrogatepass"))
            digest.update(b"\x00")
            digest.update(bytes.fromhex(source_file.content_hash))
        return digest.hexdigest()

    @staticmethod
    def resolve_source_revision(repo_path: Path, snapshot_sha256: str) -> str:
        executable = shutil.which("git")
        if executable is None:
            return f"snapshot:{snapshot_sha256}"
        environment = {
            "PATH": os.environ.get("PATH", ""),
            "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_TERMINAL_PROMPT": "0",
        }
        try:
            result = subprocess.run(  # nosec B603 - fixed executable and fixed argument vector.
                [executable, "-C", str(repo_path), "rev-parse", "--verify", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
                timeout=5.0,
                env=environment,
            )
            commit = result.stdout.strip().lower()
            if re.fullmatch(r"[0-9a-f]{40,64}", commit):
                return f"git:{commit}"
        except (OSError, subprocess.SubprocessError):
            pass
        return f"snapshot:{snapshot_sha256}"

    def persist_source_snapshot(
        self,
        *,
        organization_id: str,
        project_id: str,
        snapshot_sha256: str,
        source_files: list[SourceFile],
    ) -> dict[str, Any] | None:
        if self.object_store is None:
            return None
        key = f"rag/snapshots/{organization_id}/{project_id}/{snapshot_sha256}.tar"
        with tempfile.TemporaryDirectory(prefix="aeitron-rag-snapshot-") as directory:
            archive = Path(directory) / "snapshot.tar"
            with tarfile.open(archive, mode="w", format=tarfile.PAX_FORMAT) as handle:
                manifest_rows: list[dict[str, Any]] = []
                for source_file in sorted(source_files, key=lambda item: item.relative_path):
                    payload = source_file.content.encode("utf-8", "surrogatepass")
                    info = tarfile.TarInfo(name=source_file.relative_path)
                    info.size = len(payload)
                    info.mode = 0o600
                    info.mtime = 0
                    info.uid = info.gid = 0
                    info.uname = info.gname = ""
                    handle.addfile(info, io.BytesIO(payload))
                    manifest_rows.append(
                        {
                            "path": source_file.relative_path,
                            "content_hash": source_file.content_hash,
                            "size_bytes": source_file.size_bytes,
                        }
                    )
                manifest_payload = json.dumps(
                    {
                        "schema_version": 1,
                        "snapshot_sha256": snapshot_sha256,
                        "files": manifest_rows,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
                info = tarfile.TarInfo(name=".aeitron-snapshot-manifest.json")
                info.size = len(manifest_payload)
                info.mode = 0o600
                info.mtime = 0
                info.uid = info.gid = 0
                info.uname = info.gname = ""
                handle.addfile(info, io.BytesIO(manifest_payload))
            stored = self.object_store.put_file(archive, key=key)
        return {
            "uri": stored.uri,
            "sha256": stored.sha256,
            "size_bytes": stored.size_bytes,
            "snapshot_sha256": snapshot_sha256,
        }

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
        organization_id: str,
        project_id: str,
        file_id: str,
        source_file: SourceFile,
        max_chunk_lines: int,
        max_chunk_tokens: int = 512,
        overlap_tokens: int = 64,
    ) -> Iterable[dict[str, Any]]:
        if source_file.language == "python":
            chunks = list(self.python_symbol_chunks(source_file))
            if chunks:
                for chunk in chunks:
                    for bounded in self.bound_chunk(chunk, max_chunk_tokens=max_chunk_tokens, overlap_tokens=overlap_tokens):
                        yield self.chunk_payload(organization_id, project_id, file_id, source_file, **bounded)
                return
        for chunk in self.line_chunks(source_file, max_chunk_lines=max_chunk_lines):
            for bounded in self.bound_chunk(chunk, max_chunk_tokens=max_chunk_tokens, overlap_tokens=overlap_tokens):
                yield self.chunk_payload(organization_id, project_id, file_id, source_file, **bounded)

    def bound_chunk(
        self,
        chunk: dict[str, Any],
        *,
        max_chunk_tokens: int,
        overlap_tokens: int,
    ) -> Iterable[dict[str, Any]]:
        content = str(chunk["content"])
        if estimate_tokens(content) <= max_chunk_tokens:
            yield chunk
            return
        lines = content.splitlines()
        max_chars = max_chunk_tokens * 4
        overlap_chars = overlap_tokens * 4
        start = 0
        part = 0
        while start < len(content):
            end = min(len(content), start + max_chars)
            if end < len(content):
                newline = content.rfind("\n", start, end)
                if newline > start:
                    end = newline
            segment = content[start:end]
            start_line_offset = content[:start].count("\n")
            end_line_offset = start_line_offset + max(0, segment.count("\n"))
            bounded = dict(chunk)
            bounded["content"] = segment
            bounded["start_line"] = int(chunk["start_line"]) + start_line_offset
            bounded["end_line"] = min(int(chunk["end_line"]), int(chunk["start_line"]) + end_line_offset)
            bounded["kind"] = f"{chunk['kind']}_part"
            bounded["metadata"] = {**dict(chunk.get("metadata") or {}), "parent_symbol": chunk.get("symbol_name"), "part": part}
            yield bounded
            part += 1
            if end >= len(content):
                break
            start = max(start + 1, end - overlap_chars)

    def python_symbol_chunks(self, source_file: SourceFile) -> Iterable[dict[str, Any]]:
        try:
            tree = ast.parse(source_file.content)
        except SyntaxError:
            return []
        lines = source_file.content.splitlines()
        facts = self.python_module_facts(tree)
        chunks: list[dict[str, Any]] = []
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            start = max(1, getattr(node, "lineno", 1))
            end = max(start, getattr(node, "end_lineno", start))
            content = "\n".join(lines[start - 1 : end])
            signature = self.python_signature(node)
            calls = self.python_calls(node)
            mutations = self.python_mutations(node)
            decorators = [
                self.safe_unparse(decorator)
                for decorator in getattr(node, "decorator_list", [])
                if self.safe_unparse(decorator)
            ]
            chunks.append(
                {
                    "start_line": start,
                    "end_line": end,
                    "symbol_name": node.name,
                    "kind": "class" if isinstance(node, ast.ClassDef) else "function",
                    "content": content,
                    "metadata": {
                        "parser": "python_ast",
                        "signature": signature,
                        "imports": facts.imports,
                        "calls": calls,
                        "dependencies": sorted(set(facts.imports + calls)),
                        "state_mutations": mutations,
                        "decorators": decorators,
                        "docstring": ast.get_docstring(node) or "",
                    },
                }
            )
        return sorted(chunks, key=lambda item: (item["start_line"], item["end_line"]))

    def line_chunks(self, source_file: SourceFile, *, max_chunk_lines: int) -> Iterable[dict[str, Any]]:
        lines = source_file.content.splitlines()
        if not lines:
            return
        file_imports = self.generic_imports(source_file.language, source_file.content)
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
                "metadata": {
                    "parser": "line_chunker",
                    "imports": file_imports,
                    "dependencies": file_imports,
                    "signature": self.first_symbol_line(content),
                },
            }

    def python_module_facts(self, tree: ast.AST) -> PythonModuleFacts:
        imports: list[str] = []
        calls: list[str] = []
        mutations: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                module = "." * node.level + (node.module or "")
                imports.extend(f"{module}.{alias.name}".strip(".") for alias in node.names)
            elif isinstance(node, ast.Call):
                call = self.call_name(node.func)
                if call:
                    calls.append(call)
            elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                mutations.extend(self.assignment_targets(node))
        return PythonModuleFacts(
            imports=sorted(set(imports)),
            module_calls=sorted(set(calls)),
            module_mutations=sorted(set(mutations)),
        )

    def python_signature(self, node: ast.AST) -> str:
        if isinstance(node, ast.ClassDef):
            bases = [self.safe_unparse(base) for base in node.bases if self.safe_unparse(base)]
            return f"class {node.name}({', '.join(bases)})" if bases else f"class {node.name}"
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
            args = self.safe_unparse(node.args)
            returns = f" -> {self.safe_unparse(node.returns)}" if node.returns is not None else ""
            return f"{prefix} {node.name}({args}){returns}"
        return ""

    def python_calls(self, node: ast.AST) -> list[str]:
        calls: set[str] = set()
        for child in ast.walk(node):
            if isinstance(child, ast.Call):
                call = self.call_name(child.func)
                if call:
                    calls.add(call)
        return sorted(calls)

    def python_mutations(self, node: ast.AST) -> list[str]:
        mutations: set[str] = set()
        for child in ast.walk(node):
            if isinstance(child, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                mutations.update(self.assignment_targets(child))
            elif isinstance(child, ast.For):
                mutations.update(self.target_names(child.target))
            elif isinstance(child, ast.With):
                for item in child.items:
                    if item.optional_vars is not None:
                        mutations.update(self.target_names(item.optional_vars))
        return sorted(mutations)

    def assignment_targets(self, node: ast.AST) -> list[str]:
        if isinstance(node, ast.Assign):
            return [name for target in node.targets for name in self.target_names(target)]
        if isinstance(node, ast.AnnAssign):
            return self.target_names(node.target)
        if isinstance(node, ast.AugAssign):
            return self.target_names(node.target)
        return []

    def target_names(self, target: ast.AST) -> list[str]:
        if isinstance(target, ast.Name):
            return [target.id]
        if isinstance(target, ast.Attribute):
            owner = self.safe_unparse(target.value)
            return [f"{owner}.{target.attr}" if owner else target.attr]
        if isinstance(target, (ast.Tuple, ast.List)):
            return [name for item in target.elts for name in self.target_names(item)]
        return []

    def call_name(self, node: ast.AST) -> str | None:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            parent = self.call_name(node.value)
            return f"{parent}.{node.attr}" if parent else node.attr
        if isinstance(node, ast.Call):
            return self.call_name(node.func)
        return None

    def safe_unparse(self, node: ast.AST | None) -> str:
        if node is None:
            return ""
        try:
            return ast.unparse(node)
        except Exception:
            return ""

    def generic_imports(self, language: str, content: str) -> list[str]:
        pattern = IMPORT_REGEX_BY_LANGUAGE.get(language)
        if pattern is None:
            return []
        imports: set[str] = set()
        for match in pattern.finditer(content):
            for group in match.groups():
                if group:
                    imports.add(group.strip())
        return sorted(imports)

    def first_symbol_line(self, content: str) -> str:
        for line in content.splitlines():
            if SYMBOL_REGEX.match(line):
                return line.strip()
        return ""

    def chunk_payload(
        self,
        organization_id: str,
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
            "id": stable_uuid(
                organization_id, project_id, source_file.relative_path, symbol_name or "",
                start_line, end_line, chunk_hash, CHUNKER_VERSION,
            ),
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

