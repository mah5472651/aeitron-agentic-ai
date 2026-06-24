#!/usr/bin/env python
"""Long-context workspace memory and prompt expansion engine."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from src.phase11.persistent_memory import MemoryRecord, PersistentMemoryGateway
from src.phase11.schemas import ContextItem, ContextPack


SOURCE_EXTENSIONS = {
    ".py",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
    ".c",
    ".h",
    ".cpp",
    ".hpp",
    ".rs",
    ".go",
    ".java",
    ".sh",
    ".ps1",
    ".md",
    ".txt",
    ".json",
    ".yaml",
    ".yml",
    ".toml",
}

IGNORE_DIRS = {
    ".git",
    ".venv",
    "venv",
    "env",
    "__pycache__",
    "node_modules",
    "target",
    "build",
    "dist",
    ".pytest_cache",
    ".mypy_cache",
}

SYMBOL_RE = re.compile(
    r"^\s*(?:async\s+def|def|class|function|const|let|var|fn|pub\s+fn|struct|enum)\s+([A-Za-z_][\w]*)",
    re.MULTILINE,
)
WORD_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")


@dataclass(frozen=True)
class FileMemory:
    path: str
    content: str
    sha256: str
    symbols: list[str]
    estimated_tokens: int


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def stable_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def safe_workspace(path: str | Path) -> Path:
    root = Path(path).resolve()
    if not root.exists() or not root.is_dir():
        raise ValueError(f"workspace must be an existing directory: {root}")
    return root


def iter_source_files(root: Path, max_files: int = 2000) -> Iterable[Path]:
    count = 0
    for path in root.rglob("*"):
        if count >= max_files:
            return
        if path.is_dir():
            continue
        if any(part in IGNORE_DIRS for part in path.parts):
            continue
        if path.suffix.lower() not in SOURCE_EXTENSIONS:
            continue
        count += 1
        yield path


class WorkspaceMemoryEngine:
    """Builds a compact, retrievable memory view over a code workspace."""

    def __init__(
        self,
        workspace: str | Path,
        *,
        max_file_bytes: int = 256_000,
        max_files: int = 2000,
        callgraph_jsonl: str | Path | None = None,
    ) -> None:
        self.root = safe_workspace(workspace)
        self.max_file_bytes = max_file_bytes
        self.max_files = max_files
        self.callgraph_jsonl = Path(callgraph_jsonl) if callgraph_jsonl else None
        self.files: list[FileMemory] = []
        self.callgraph_records: list[dict] = []

    def refresh(self) -> None:
        self.files = []
        for path in iter_source_files(self.root, self.max_files):
            try:
                raw = path.read_bytes()[: self.max_file_bytes + 1]
            except OSError:
                continue
            if len(raw) > self.max_file_bytes:
                raw = raw[: self.max_file_bytes]
            text = raw.decode("utf-8", errors="replace")
            relative = path.relative_to(self.root).as_posix()
            self.files.append(
                FileMemory(
                    path=relative,
                    content=text,
                    sha256=stable_hash(text),
                    symbols=sorted(set(SYMBOL_RE.findall(text))),
                    estimated_tokens=estimate_tokens(text),
                )
            )
        self.callgraph_records = self._load_callgraph_records()

    def _load_callgraph_records(self) -> list[dict]:
        if not self.callgraph_jsonl or not self.callgraph_jsonl.exists():
            default = self.root / "artifacts" / "mvp" / "ast_graph.jsonl"
            if not default.exists():
                return []
            source = default
        else:
            source = self.callgraph_jsonl
        records = []
        try:
            for line in source.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    records.append(json.loads(line))
        except (OSError, json.JSONDecodeError):
            return []
        return records[:1000]

    def summarize_workspace(self) -> str:
        if not self.files:
            self.refresh()
        language_counts: dict[str, int] = {}
        for file in self.files:
            suffix = Path(file.path).suffix.lower() or "<none>"
            language_counts[suffix] = language_counts.get(suffix, 0) + 1
        top_symbols = []
        for file in self.files[:200]:
            for symbol in file.symbols[:8]:
                top_symbols.append(f"{symbol} ({file.path})")
        return (
            f"Workspace root: {self.root}\n"
            f"Files indexed: {len(self.files)}\n"
            f"File types: {dict(sorted(language_counts.items()))}\n"
            f"Known symbols: {', '.join(top_symbols[:40]) or 'none'}"
        )

    def expand_intent(self, prompt: str) -> str:
        compact = " ".join(prompt.strip().split())
        lower = compact.lower()
        if len(compact) < 80:
            if any(marker in lower for marker in ("fix", "bug", "error", "crash")):
                mode = "debug and repair"
            elif any(marker in lower for marker in ("security", "vulnerab", "exploit", "cve")):
                mode = "security analysis and safe patch"
            elif any(marker in lower for marker in ("build", "make", "create", "implement")):
                mode = "implementation planning and coding"
            else:
                mode = "intent clarification through repo context"
            return (
                f"User gave a short prompt. Expand it as a {mode} task. "
                "Infer likely files, inspect project conventions, produce a minimal correct plan, "
                "run verification, and only ask a question if the goal is genuinely ambiguous. "
                f"Original prompt: {compact}"
            )
        return compact

    def retrieve(self, query: str, *, token_budget: int = 12000, max_items: int = 24) -> ContextPack:
        if not self.files:
            self.refresh()
        expanded = self.expand_intent(query)
        query_terms = set(term.lower() for term in WORD_RE.findall(expanded))
        scored: list[ContextItem] = []
        for file in self.files:
            content_terms = set(term.lower() for term in WORD_RE.findall(file.content[:20000]))
            symbol_terms = set(symbol.lower() for symbol in file.symbols)
            overlap = len(query_terms & content_terms) + 3 * len(query_terms & symbol_terms)
            path_score = sum(2 for term in query_terms if term in file.path.lower())
            score = float(overlap + path_score)
            if score <= 0 and file.path.lower() in {"readme.md", "pyproject.toml", "requirements.txt"}:
                score = 0.5
            if score <= 0:
                continue
            snippet = self._snippet(file.content, query_terms)
            scored.append(
                ContextItem(
                    source=file.path,
                    title=f"{file.path} symbols={','.join(file.symbols[:8])}",
                    content=snippet,
                    score=score,
                    kind="source",
                    metadata={
                        "sha256": file.sha256,
                        "estimated_tokens": estimate_tokens(snippet),
                        "symbols": file.symbols[:24],
                    },
                )
            )
        scored.sort(key=lambda item: item.score, reverse=True)
        if not scored:
            for file in self.files[:max_items]:
                snippet = file.content[: min(4000, len(file.content))]
                scored.append(
                    ContextItem(
                        source=file.path,
                        title=f"{file.path} symbols={','.join(file.symbols[:8])}",
                        content=snippet,
                        score=0.1,
                        kind="source_fallback",
                        metadata={
                            "sha256": file.sha256,
                            "estimated_tokens": estimate_tokens(snippet),
                            "symbols": file.symbols[:24],
                        },
                    )
                )
        selected: list[ContextItem] = []
        total = estimate_tokens(expanded)
        for candidate in scored[: max_items * 3]:
            cost = estimate_tokens(candidate.content)
            if total + cost > token_budget:
                continue
            selected.append(candidate)
            total += cost
            if len(selected) >= max_items:
                break
        if self.callgraph_records and total < token_budget:
            selected.extend(self._callgraph_items(query_terms, token_budget - total)[:4])
            total = estimate_tokens(expanded + "\n".join(item.content for item in selected))
        return ContextPack(
            query=query,
            expanded_intent=expanded,
            items=selected,
            token_budget=token_budget,
            estimated_tokens=total,
            workspace_root=str(self.root),
        )

    def export_memory_records(
        self,
        *,
        gateway: PersistentMemoryGateway | None = None,
        max_records: int = 500,
    ) -> list[MemoryRecord]:
        if not self.files:
            self.refresh()
        memory_gateway = gateway or PersistentMemoryGateway(workspace=str(self.root))
        records: list[MemoryRecord] = []
        for file in self.files[:max_records]:
            records.append(
                memory_gateway.build_record(
                    source=file.path,
                    content=file.content[:12000],
                    metadata={
                        "sha256": file.sha256,
                        "symbols": file.symbols[:64],
                        "estimated_tokens": file.estimated_tokens,
                        "kind": "source_file",
                    },
                )
            )
        for index, record in enumerate(self.callgraph_records[: max(0, max_records - len(records))]):
            records.append(
                memory_gateway.build_record(
                    source=f"callgraph:{index}",
                    content=json.dumps(record, ensure_ascii=False)[:12000],
                    metadata={"kind": "callgraph_record"},
                )
            )
        return records

    async def index_persistent(
        self,
        *,
        gateway: PersistentMemoryGateway | None = None,
        max_records: int = 500,
    ) -> dict:
        memory_gateway = gateway or PersistentMemoryGateway(workspace=str(self.root))
        await memory_gateway.initialize()
        records = self.export_memory_records(gateway=memory_gateway, max_records=max_records)
        result = await memory_gateway.upsert(records)
        result["records_prepared"] = len(records)
        return result

    def _snippet(self, content: str, query_terms: set[str], window: int = 6000) -> str:
        lower = content.lower()
        positions = [lower.find(term) for term in query_terms if lower.find(term) >= 0]
        if not positions:
            return content[:window]
        center = max(0, min(positions) - window // 4)
        return content[center : center + window]

    def _callgraph_items(self, query_terms: set[str], token_budget: int) -> list[ContextItem]:
        items: list[ContextItem] = []
        used = 0
        for record in self.callgraph_records:
            text = json.dumps(record, ensure_ascii=False)
            if not any(term in text.lower() for term in query_terms):
                continue
            cost = estimate_tokens(text)
            if used + cost > token_budget:
                break
            used += cost
            items.append(
                ContextItem(
                    source="callgraph",
                    title=str(record.get("name") or record.get("fn") or "callgraph-record"),
                    content=text[:6000],
                    score=3.0,
                    kind="callgraph",
                )
            )
        return items
