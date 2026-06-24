#!/usr/bin/env python
"""Tree-sitter AST graph extractor and global call graph builder.

This Phase 1 ingestion script scans raw repositories, parses supported source
files with tree-sitter, extracts dense function-level metadata, and emits a
JSONL structural graph suitable for SFT, retrieval, and reasoning pipelines.

Supported languages:
- Python
- C
- C++
- Rust
- Bash / shell

The implementation is intentionally self-contained. It prefers the
`tree-sitter-language-pack` package, and can also use `tree-sitter-languages`
when available.
"""

from __future__ import annotations

import argparse
import ast
import concurrent.futures
import hashlib
import json
import os
import re
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator


SOURCE_EXTENSIONS = {
    ".py": "python",
    ".pyw": "python",
    ".c": "c",
    ".h": "c",
    ".cc": "cpp",
    ".cpp": "cpp",
    ".cxx": "cpp",
    ".hh": "cpp",
    ".hpp": "cpp",
    ".hxx": "cpp",
    ".rs": "rust",
    ".sh": "bash",
    ".bash": "bash",
    ".zsh": "bash",
}

TREE_SITTER_LANGUAGE_ALIASES = {
    "python": ["python"],
    "c": ["c"],
    "cpp": ["cpp", "c++"],
    "rust": ["rust"],
    "bash": ["bash", "shell"],
}

DEFAULT_EXCLUDE_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    "env",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    "node_modules",
    "target",
    "build",
    "dist",
    ".tox",
    ".idea",
    ".vscode",
}

FUNCTION_NODE_TYPES = {
    "python": {"function_definition", "decorated_definition"},
    "c": {"function_definition"},
    "cpp": {"function_definition", "template_declaration"},
    "rust": {"function_item"},
    "bash": {"function_definition"},
}

CALL_NODE_TYPES = {
    "python": {"call"},
    "c": {"call_expression"},
    "cpp": {"call_expression"},
    "rust": {"call_expression", "macro_invocation"},
    "bash": {"command"},
}

ASSIGNMENT_NODE_TYPES = {
    "python": {"assignment", "augmented_assignment", "named_expression"},
    "c": {"assignment_expression", "update_expression", "init_declarator"},
    "cpp": {"assignment_expression", "update_expression", "init_declarator"},
    "rust": {"assignment_expression", "compound_assignment_expr", "let_declaration"},
    "bash": {"variable_assignment", "declaration_command"},
}

DEPENDENCY_NODE_TYPES = {
    "python": {"import_statement", "import_from_statement"},
    "c": {"preproc_include"},
    "cpp": {"preproc_include", "namespace_alias_definition", "using_declaration"},
    "rust": {"use_declaration", "extern_crate_declaration"},
    "bash": {"command"},
}

CALL_KEYWORDS = {
    "if",
    "for",
    "while",
    "switch",
    "catch",
    "return",
    "sizeof",
    "alignof",
    "decltype",
    "new",
    "delete",
    "match",
    "fn",
    "let",
    "function",
    "do",
    "then",
    "case",
    "else",
    "elif",
    "fi",
    "echo",
    "cd",
    "export",
    "local",
    "readonly",
}

CALL_FALLBACK_RE = re.compile(r"(?<![\w.])(?P<name>[A-Za-z_~][\w:~.]*[!?]?)\s*\(")
ASSIGNMENT_RE = re.compile(
    r"""
    (?:
        (?P<lhs>[A-Za-z_][\w.]*)\s*(?:\+\+|--|[+\-*/%&|^]?=)
        |
        (?:(?:let|var|const|auto|int|char|float|double|bool|size_t|string|String)\s+)
        (?P<decl>[A-Za-z_][\w.]*)
    )
    """,
    re.VERBOSE,
)


@dataclass(frozen=True)
class ParameterInfo:
    name: str
    type: str | None = None
    default: str | None = None


@dataclass(frozen=True)
class DependencyInfo:
    name: str
    kind: str
    raw: str
    line: int


@dataclass(frozen=True)
class CallSite:
    name: str
    resolved: str | None
    scope: str
    line: int
    external: bool
    confidence: str


@dataclass(frozen=True)
class FunctionRecord:
    schema: str
    id: str
    repo: str
    file: str
    lang: str
    name: str
    qname: str
    kind: str
    span: list[int]
    byte_span: list[int]
    signature: str
    params: list[ParameterInfo]
    returns: str | None
    mutates: list[str]
    dependencies: list[DependencyInfo]
    calls: list[CallSite]
    ast_type: str
    ast_hash: str
    source_hash: str


@dataclass(frozen=True)
class FileParseResult:
    file: str
    language: str
    source_hash: str
    functions: list[dict[str, Any]]
    dependencies: list[DependencyInfo]
    parse_errors: list[str] = field(default_factory=list)


def stable_id(*parts: object) -> str:
    data = "\x1f".join(str(part) for part in parts)
    return hashlib.sha256(data.encode("utf-8", "surrogatepass")).hexdigest()[:32]


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def decode_source(data: bytes) -> str:
    return data.decode("utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")


def read_bytes(path: Path, max_file_mb: float) -> bytes | None:
    try:
        if path.stat().st_size > max_file_mb * 1024 * 1024:
            return None
        return path.read_bytes()
    except OSError:
        return None


def iter_source_files(repo: Path, exclude_dirs: set[str], max_file_mb: float) -> Iterator[Path]:
    for path in repo.rglob("*"):
        if not path.is_file():
            continue
        if any(part in exclude_dirs for part in path.parts):
            continue
        if path.suffix.lower() not in SOURCE_EXTENSIONS:
            continue
        try:
            if path.stat().st_size <= max_file_mb * 1024 * 1024:
                yield path
        except OSError:
            continue


def rel_path(repo: Path, path: Path) -> str:
    return path.relative_to(repo).as_posix()


def line_from_byte(source: bytes, byte_offset: int) -> int:
    return source.count(b"\n", 0, max(0, byte_offset)) + 1


def text_for_node(source: bytes, node: Any, limit: int | None = None) -> str:
    raw = source[node_start_byte(node) : node_end_byte(node)]
    if limit is not None:
        raw = raw[:limit]
    return decode_source(raw).strip()


def node_start_byte(node: Any) -> int:
    value = getattr(node, "start_byte")
    return int(value() if callable(value) else value)


def node_end_byte(node: Any) -> int:
    value = getattr(node, "end_byte")
    return int(value() if callable(value) else value)


def node_type(node: Any) -> str:
    value = getattr(node, "type", None) or getattr(node, "kind")
    return value() if callable(value) else value


def node_children(node: Any) -> list[Any]:
    children = getattr(node, "children", None)
    if children is not None:
        return list(children)
    child_count = getattr(node, "child_count", 0)
    child_count = child_count() if callable(child_count) else child_count
    return [node.child(index) for index in range(child_count)]


def iter_nodes(root: Any) -> Iterator[Any]:
    stack = [root]
    while stack:
        node = stack.pop()
        yield node
        stack.extend(reversed(node_children(node)))


def named_children(node: Any) -> list[Any]:
    named_child_count = getattr(node, "named_child_count", None)
    named_child = getattr(node, "named_child", None)
    if callable(named_child_count) and callable(named_child):
        return [named_child(index) for index in range(named_child_count())]
    output = []
    for child in node_children(node):
        is_named = getattr(child, "is_named", False)
        is_named = is_named() if callable(is_named) else is_named
        if is_named:
            output.append(child)
    return output


def child_by_field(node: Any, field_name: str) -> Any | None:
    try:
        return node.child_by_field_name(field_name)
    except Exception:
        return None


def first_named_descendant(node: Any, node_types: set[str]) -> Any | None:
    for candidate in iter_nodes(node):
        if node_type(candidate) in node_types:
            return candidate
    return None


def load_tree_sitter_parser(language: str) -> Any:
    """Load a parser from common tree-sitter Python language bundles."""

    aliases = TREE_SITTER_LANGUAGE_ALIASES[language]
    import_errors: list[str] = []

    try:
        from tree_sitter_language_pack import get_parser  # type: ignore

        for alias in aliases:
            try:
                return get_parser(alias)
            except Exception as exc:
                import_errors.append(f"tree_sitter_language_pack:{alias}:{exc}")
    except Exception as exc:
        import_errors.append(f"tree_sitter_language_pack import:{exc}")

    try:
        from tree_sitter import Parser  # type: ignore
        from tree_sitter_languages import get_language  # type: ignore

        for alias in aliases:
            try:
                parser = Parser()
                language_obj = get_language(alias)
                if hasattr(parser, "set_language"):
                    parser.set_language(language_obj)
                else:
                    parser.language = language_obj
                return parser
            except Exception as exc:
                import_errors.append(f"tree_sitter_languages:{alias}:{exc}")
    except Exception as exc:
        import_errors.append(f"tree_sitter_languages import:{exc}")

    details = "\n  ".join(import_errors)
    raise RuntimeError(
        "No usable tree-sitter language provider found. Install "
        "`tree-sitter-language-pack` or `tree-sitter-languages`.\n  "
        + details
    )


def parse_with_tree_sitter(language: str, source: bytes) -> Any:
    parser = load_tree_sitter_parser(language)
    try:
        tree = parser.parse(source)
    except TypeError:
        tree = parser.parse(decode_source(source))
    root_node = tree.root_node
    return root_node() if callable(root_node) else root_node


def function_name(language: str, source: bytes, node: Any) -> str | None:
    current_type = node_type(node)
    if current_type == "decorated_definition":
        inner = first_named_descendant(node, {"function_definition"})
        if inner is not None:
            return function_name(language, source, inner)

    direct_name = child_by_field(node, "name")
    if direct_name is not None:
        return text_for_node(source, direct_name)

    if language in {"c", "cpp"}:
        declarator = child_by_field(node, "declarator")
        if declarator is not None:
            identifiers = [
                text_for_node(source, item)
                for item in iter_nodes(declarator)
                if node_type(item) in {"identifier", "field_identifier", "qualified_identifier", "operator_name"}
            ]
            if identifiers:
                return identifiers[-1]

    if language == "bash":
        for item in named_children(node):
            if node_type(item) in {"word", "identifier", "command_name"}:
                return text_for_node(source, item)

    identifiers = [
        text_for_node(source, item)
        for item in iter_nodes(node)
        if node_type(item) in {"identifier", "field_identifier", "word"}
    ]
    return identifiers[0] if identifiers else None


def normalize_call_name(name: str) -> str:
    name = name.strip()
    name = re.sub(r"\s+", "", name)
    name = name.replace("self.", "").replace("cls.", "")
    if name.endswith("!"):
        return name[:-1]
    return name


def call_name(language: str, source: bytes, node: Any) -> str | None:
    current_type = node_type(node)
    if language == "bash" and current_type == "command":
        first = first_named_descendant(node, {"command_name", "word"})
        if first is None:
            return None
        raw = text_for_node(source, first)
        return None if raw in CALL_KEYWORDS or raw.startswith("$") else raw

    function_child = child_by_field(node, "function")
    if function_child is not None:
        raw = text_for_node(source, function_child, limit=256)
        return normalize_call_name(raw)

    for item in named_children(node):
        if node_type(item) in {
            "identifier",
            "field_identifier",
            "qualified_identifier",
            "scoped_identifier",
            "attribute",
        }:
            raw = text_for_node(source, item, limit=256)
            return normalize_call_name(raw)
    return None


def signature_text(language: str, source: bytes, node: Any) -> str:
    text = text_for_node(source, node, limit=4096)
    if language == "python":
        lines = []
        for line in text.splitlines():
            lines.append(line)
            if line.rstrip().endswith(":"):
                break
        return "\n".join(lines)
    body = child_by_field(node, "body")
    if body is not None and node_start_byte(body) > node_start_byte(node):
        return decode_source(source[node_start_byte(node) : node_start_byte(body)]).strip()
    opening = text.find("{")
    return text[:opening].strip() if opening >= 0 else text.splitlines()[0].strip()


def parse_python_signature(signature: str) -> tuple[list[ParameterInfo], str | None]:
    try:
        module = ast.parse(signature + "\n    pass\n")
        fn = next(
            item
            for item in ast.walk(module)
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
        )
    except Exception:
        return parse_signature_fallback(signature), None

    params: list[ParameterInfo] = []
    args = list(fn.args.posonlyargs) + list(fn.args.args)
    defaults = [None] * (len(args) - len(fn.args.defaults)) + list(fn.args.defaults)
    for arg, default in zip(args, defaults):
        params.append(
            ParameterInfo(
                name=arg.arg,
                type=ast.unparse(arg.annotation) if arg.annotation is not None else None,
                default=ast.unparse(default) if default is not None else None,
            )
        )
    if fn.args.vararg:
        params.append(
            ParameterInfo(
                name="*" + fn.args.vararg.arg,
                type=ast.unparse(fn.args.vararg.annotation)
                if fn.args.vararg.annotation is not None
                else None,
            )
        )
    for arg, default in zip(fn.args.kwonlyargs, fn.args.kw_defaults):
        params.append(
            ParameterInfo(
                name=arg.arg,
                type=ast.unparse(arg.annotation) if arg.annotation is not None else None,
                default=ast.unparse(default) if default is not None else None,
            )
        )
    if fn.args.kwarg:
        params.append(
            ParameterInfo(
                name="**" + fn.args.kwarg.arg,
                type=ast.unparse(fn.args.kwarg.annotation)
                if fn.args.kwarg.annotation is not None
                else None,
            )
        )
    returns = ast.unparse(fn.returns) if fn.returns is not None else None
    return params, returns


def parse_signature_fallback(signature: str) -> list[ParameterInfo]:
    match = re.search(r"\((?P<params>.*)\)", signature, flags=re.DOTALL)
    if not match:
        return []
    raw_params = match.group("params").strip()
    if not raw_params or raw_params == "void":
        return []
    params: list[ParameterInfo] = []
    for item in split_params(raw_params):
        cleaned = item.strip()
        if not cleaned:
            continue
        default = None
        if "=" in cleaned:
            cleaned, default = [part.strip() for part in cleaned.split("=", 1)]
        name_match = re.search(r"([A-Za-z_][\w]*)\s*(?:\[[^\]]*\])?$", cleaned)
        name = name_match.group(1) if name_match else cleaned
        type_text = cleaned[: name_match.start(1)].strip() if name_match else None
        params.append(ParameterInfo(name=name, type=type_text or None, default=default))
    return params


def split_params(raw_params: str) -> list[str]:
    params: list[str] = []
    depth = 0
    start = 0
    for index, char in enumerate(raw_params):
        if char in "(<[":
            depth += 1
        elif char in ")>]":
            depth = max(0, depth - 1)
        elif char == "," and depth == 0:
            params.append(raw_params[start:index])
            start = index + 1
    params.append(raw_params[start:])
    return params


def parse_return_type(language: str, signature: str, name: str) -> str | None:
    if language == "python":
        match = re.search(r"->\s*(?P<ret>[^:]+)", signature)
        return match.group("ret").strip() if match else None
    if language == "rust":
        match = re.search(r"->\s*(?P<ret>[^{where]+)", signature)
        return match.group("ret").strip() if match else None
    prefix = signature.split(name, 1)[0].strip()
    prefix = re.sub(r"\b(static|inline|extern|constexpr|virtual|explicit|friend)\b", "", prefix)
    return re.sub(r"\s+", " ", prefix).strip() or None


def extract_dependencies(language: str, source: bytes, root_or_function: Any) -> list[DependencyInfo]:
    deps: list[DependencyInfo] = []
    for node in iter_nodes(root_or_function):
        current_type = node_type(node)
        if current_type not in DEPENDENCY_NODE_TYPES.get(language, set()):
            continue
        raw = text_for_node(source, node, limit=512)
        if language == "bash" and current_type == "command":
            name = call_name(language, source, node)
            if name not in {"source", "."}:
                continue
            parts = raw.split()
            dep_name = parts[1] if len(parts) > 1 else raw
            kind = "source"
        elif language == "python":
            dep_name = raw
            kind = "import"
        elif language in {"c", "cpp"}:
            dep_name = raw.replace("#include", "").strip()
            kind = "include"
        elif language == "rust":
            dep_name = raw
            kind = "use"
        else:
            dep_name = raw
            kind = current_type
        deps.append(
            DependencyInfo(
                name=dep_name,
                kind=kind,
                raw=raw,
                line=line_from_byte(source, node_start_byte(node)),
            )
        )
    return unique_dependencies(deps)


def unique_dependencies(deps: list[DependencyInfo]) -> list[DependencyInfo]:
    seen: set[tuple[str, str, int]] = set()
    output: list[DependencyInfo] = []
    for dep in deps:
        key = (dep.kind, dep.name, dep.line)
        if key in seen:
            continue
        seen.add(key)
        output.append(dep)
    return output


def extract_mutations(language: str, source: bytes, function_node: Any) -> list[str]:
    mutated: set[str] = set()
    for node in iter_nodes(function_node):
        if node_type(node) in ASSIGNMENT_NODE_TYPES.get(language, set()):
            raw = text_for_node(source, node, limit=512)
            for match in ASSIGNMENT_RE.finditer(raw):
                name = match.group("lhs") or match.group("decl")
                if name and name not in CALL_KEYWORDS:
                    mutated.add(name)

    function_text = text_for_node(source, function_node)
    for match in ASSIGNMENT_RE.finditer(function_text):
        name = match.group("lhs") or match.group("decl")
        if name and name not in CALL_KEYWORDS:
            mutated.add(name)
    return sorted(mutated)


def extract_calls(language: str, source: bytes, function_node: Any) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()
    for node in iter_nodes(function_node):
        if node_type(node) not in CALL_NODE_TYPES.get(language, set()):
            continue
        name = call_name(language, source, node)
        if not name or name in CALL_KEYWORDS:
            continue
        line = line_from_byte(source, node_start_byte(node))
        key = (name, line)
        if key in seen:
            continue
        seen.add(key)
        calls.append({"name": name, "line": line})

    function_text = text_for_node(source, function_node)
    base_line = line_from_byte(source, node_start_byte(function_node))
    for match in CALL_FALLBACK_RE.finditer(function_text):
        name = normalize_call_name(match.group("name"))
        short = name.split(".")[-1].split("::")[-1]
        if short in CALL_KEYWORDS:
            continue
        line = base_line + function_text.count("\n", 0, match.start())
        key = (name, line)
        if key in seen:
            continue
        seen.add(key)
        calls.append({"name": name, "line": line})
    return sorted(calls, key=lambda item: (item["line"], item["name"]))


def is_function_node(language: str, node: Any) -> bool:
    current_type = node_type(node)
    if current_type not in FUNCTION_NODE_TYPES.get(language, set()):
        return False
    if current_type == "decorated_definition":
        return first_named_descendant(node, {"function_definition"}) is not None
    if current_type == "template_declaration":
        return first_named_descendant(node, {"function_definition"}) is not None
    return True


def normalize_function_node(node: Any) -> Any:
    if node_type(node) in {"decorated_definition", "template_declaration"}:
        inner = first_named_descendant(node, {"function_definition"})
        return inner or node
    return node


def function_kind(language: str, node: Any, signature: str) -> str:
    if language == "python" and signature.lstrip().startswith("async def"):
        return "async_function"
    if language == "rust" and "unsafe fn" in signature:
        return "unsafe_function"
    return "function"


def parse_file_worker(repo_str: str, file_str: str, max_file_mb: float) -> FileParseResult:
    repo = Path(repo_str)
    path = Path(file_str)
    relative_file = rel_path(repo, path)
    language = SOURCE_EXTENSIONS[path.suffix.lower()]
    source = read_bytes(path, max_file_mb)
    if source is None:
        return FileParseResult(
            file=relative_file,
            language=language,
            source_hash="",
            functions=[],
            dependencies=[],
            parse_errors=["skipped: unreadable or too large"],
        )

    source_hash = sha256_bytes(source)
    parse_errors: list[str] = []
    try:
        root = parse_with_tree_sitter(language, source)
    except Exception as exc:
        return FileParseResult(
            file=relative_file,
            language=language,
            source_hash=source_hash,
            functions=[],
            dependencies=[],
            parse_errors=[f"tree-sitter parse failed: {type(exc).__name__}: {exc}"],
        )

    file_deps = extract_dependencies(language, source, root)
    functions: list[dict[str, Any]] = []
    for candidate in iter_nodes(root):
        if not is_function_node(language, candidate):
            continue
        node = normalize_function_node(candidate)
        name = function_name(language, source, node)
        if not name or name in CALL_KEYWORDS:
            continue
        signature = signature_text(language, source, node)
        if language == "python":
            params, returns = parse_python_signature(signature)
        else:
            params = parse_signature_fallback(signature)
            returns = parse_return_type(language, signature, name)
        start_line = line_from_byte(source, node_start_byte(node))
        end_line = line_from_byte(source, node_end_byte(node))
        qname = f"{relative_file}::{name}"
        ast_hash = sha256_bytes(source[node_start_byte(node) : node_end_byte(node)])
        functions.append(
            {
                "id": stable_id(relative_file, qname, start_line, ast_hash),
                "file": relative_file,
                "language": language,
                "name": name,
                "qname": qname,
                "kind": function_kind(language, node, signature),
                "span": [start_line, end_line],
                "byte_span": [node_start_byte(node), node_end_byte(node)],
                "signature": signature,
                "params": [asdict(param) for param in params],
                "returns": returns,
                "mutates": extract_mutations(language, source, node),
                "dependencies": [asdict(dep) for dep in unique_dependencies(file_deps + extract_dependencies(language, source, node))],
                "raw_calls": extract_calls(language, source, node),
                "ast_type": node_type(node),
                "ast_hash": ast_hash,
                "source_hash": source_hash,
            }
        )
    return FileParseResult(
        file=relative_file,
        language=language,
        source_hash=source_hash,
        functions=functions,
        dependencies=file_deps,
        parse_errors=parse_errors,
    )


def build_symbol_index(file_results: list[FileParseResult]) -> tuple[dict[str, str], dict[str, list[str]]]:
    exact: dict[str, str] = {}
    short: dict[str, list[str]] = {}
    for result in file_results:
        for fn in result.functions:
            qname = str(fn["qname"])
            name = str(fn["name"])
            exact[qname] = qname
            exact[name] = qname if name not in exact else exact[name]
            short.setdefault(name.split("::")[-1].split(".")[-1], []).append(qname)
    return exact, short


def resolve_call(
    caller: dict[str, Any],
    call: dict[str, Any],
    exact_symbols: dict[str, str],
    short_symbols: dict[str, list[str]],
) -> CallSite:
    raw_name = str(call["name"])
    line = int(call["line"])
    normalized = normalize_call_name(raw_name)
    candidates = [
        normalized,
        normalized.split(".")[-1],
        normalized.split("::")[-1],
        f'{caller["file"]}::{normalized}',
        f'{caller["file"]}::{normalized.split(".")[-1].split("::")[-1]}',
    ]
    for candidate in candidates:
        if candidate in exact_symbols:
            return CallSite(
                name=raw_name,
                resolved=exact_symbols[candidate],
                scope="internal",
                line=line,
                external=False,
                confidence="high",
            )
    short_name = normalized.split(".")[-1].split("::")[-1]
    matches = short_symbols.get(short_name, [])
    if len(matches) == 1:
        return CallSite(
            name=raw_name,
            resolved=matches[0],
            scope="internal",
            line=line,
            external=False,
            confidence="medium",
        )
    if len(matches) > 1:
        return CallSite(
            name=raw_name,
            resolved=None,
            scope="ambiguous_internal",
            line=line,
            external=False,
            confidence="low",
        )
    return CallSite(
        name=raw_name,
        resolved=None,
        scope="external",
        line=line,
        external=True,
        confidence="low",
    )


def to_dependency(payload: dict[str, Any]) -> DependencyInfo:
    return DependencyInfo(
        name=str(payload["name"]),
        kind=str(payload["kind"]),
        raw=str(payload["raw"]),
        line=int(payload["line"]),
    )


def function_record_from_payload(
    repo_name: str,
    payload: dict[str, Any],
    calls: list[CallSite],
) -> FunctionRecord:
    return FunctionRecord(
        schema="phase1.ast_graph.function.v2",
        id=str(payload["id"]),
        repo=repo_name,
        file=str(payload["file"]),
        lang=str(payload["language"]),
        name=str(payload["name"]),
        qname=str(payload["qname"]),
        kind=str(payload["kind"]),
        span=list(payload["span"]),
        byte_span=list(payload["byte_span"]),
        signature=str(payload["signature"]),
        params=[ParameterInfo(**param) for param in payload["params"]],
        returns=payload["returns"],
        mutates=list(payload["mutates"]),
        dependencies=[to_dependency(dep) for dep in payload["dependencies"]],
        calls=calls,
        ast_type=str(payload["ast_type"]),
        ast_hash=str(payload["ast_hash"]),
        source_hash=str(payload["source_hash"]),
    )


def build_records(
    repo: Path,
    file_results: list[FileParseResult],
) -> tuple[list[FunctionRecord], dict[str, Any]]:
    exact_symbols, short_symbols = build_symbol_index(file_results)
    records: list[FunctionRecord] = []
    edge_count = 0
    external_edge_count = 0
    for result in file_results:
        for fn in result.functions:
            calls = [
                resolve_call(fn, call, exact_symbols, short_symbols)
                for call in fn.get("raw_calls", [])
            ]
            edge_count += len(calls)
            external_edge_count += sum(1 for call in calls if call.external)
            records.append(function_record_from_payload(repo.name, fn, calls))
    summary = {
        "schema": "phase1.ast_graph.summary.v2",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "repo": str(repo.resolve()),
        "files_scanned": len(file_results),
        "languages": count_languages(file_results),
        "functions": len(records),
        "call_edges": edge_count,
        "external_call_edges": external_edge_count,
        "parse_errors": [
            {"file": result.file, "errors": result.parse_errors}
            for result in file_results
            if result.parse_errors
        ],
    }
    return sorted(records, key=lambda item: (item.file, item.span[0], item.qname)), summary


def count_languages(file_results: list[FileParseResult]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for result in file_results:
        counts[result.language] = counts.get(result.language, 0) + 1
    return counts


def dense_record(record: FunctionRecord) -> dict[str, Any]:
    """Compact JSONL shape for large-scale metadata storage."""

    return {
        "s": record.schema,
        "id": record.id,
        "r": record.repo,
        "f": record.file,
        "l": record.lang,
        "n": record.name,
        "q": record.qname,
        "k": record.kind,
        "sp": record.span,
        "bs": record.byte_span,
        "sig": record.signature,
        "in": [asdict(param) for param in record.params],
        "out": record.returns,
        "mut": record.mutates,
        "dep": [asdict(dep) for dep in record.dependencies],
        "call": [asdict(call) for call in record.calls],
        "at": record.ast_type,
        "ah": record.ast_hash,
        "sh": record.source_hash,
    }


def write_jsonl(path: Path, records: Iterable[FunctionRecord]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(dense_record(record), sort_keys=True, ensure_ascii=False) + "\n")


def write_graph_json(path: Path, records: list[FunctionRecord], summary: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    nodes = [
        {
            "id": record.id,
            "qname": record.qname,
            "file": record.file,
            "language": record.lang,
            "span": record.span,
            "signature": record.signature,
        }
        for record in records
    ]
    edges = []
    for record in records:
        for call in record.calls:
            edges.append(
                {
                    "caller": record.qname,
                    "callee": call.resolved or call.name,
                    "file": record.file,
                    "line": call.line,
                    "external": call.external,
                    "confidence": call.confidence,
                }
            )
    payload = {"summary": summary, "nodes": nodes, "edges": edges}
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def write_mermaid(path: Path, records: list[FunctionRecord], max_edges: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["flowchart TD"]
    written = 0
    for record in records:
        for call in record.calls:
            if written >= max_edges:
                lines.append(f'  more["... truncated at {max_edges} edges ..."]')
                path.write_text("\n".join(lines) + "\n", encoding="utf-8")
                return
            caller = record.qname.replace('"', "'")
            callee = (call.resolved or call.name).replace('"', "'")
            caller_id = "n" + stable_id(caller)
            callee_id = "n" + stable_id(callee)
            lines.append(f'  {caller_id}["{caller}"] --> {callee_id}["{callee}"]')
            written += 1
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_ast_graph(
    repo: Path,
    exclude_dirs: set[str],
    max_file_mb: float,
    workers: int,
) -> tuple[list[FunctionRecord], dict[str, Any]]:
    files = list(iter_source_files(repo, exclude_dirs, max_file_mb))
    if not files:
        return [], {
            "schema": "phase1.ast_graph.summary.v2",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "repo": str(repo.resolve()),
            "files_scanned": 0,
            "languages": {},
            "functions": 0,
            "call_edges": 0,
            "external_call_edges": 0,
            "parse_errors": [],
        }

    worker_count = workers if workers > 0 else max(1, (os.cpu_count() or 2) - 1)
    file_results: list[FileParseResult] = []
    if worker_count == 1:
        file_results = [
            parse_file_worker(str(repo), str(path), max_file_mb)
            for path in files
        ]
        return build_records(repo, file_results)

    with concurrent.futures.ProcessPoolExecutor(max_workers=worker_count) as executor:
        futures = [
            executor.submit(parse_file_worker, str(repo), str(path), max_file_mb)
            for path in files
        ]
        for future in concurrent.futures.as_completed(futures):
            file_results.append(future.result())

    return build_records(repo, file_results)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Parallel tree-sitter AST graph and globally resolved call graph extractor.",
    )
    parser.add_argument("--repo", required=True, type=Path, help="Repository/corpus root to scan.")
    parser.add_argument("--out-jsonl", type=Path, help="Dense function metadata JSONL output.")
    parser.add_argument("--out-graph", type=Path, help="Optional expanded graph JSON output.")
    parser.add_argument("--out", type=Path, help="Backward-compatible alias for --out-graph.")
    parser.add_argument("--mermaid-out", type=Path, help="Optional Mermaid call graph output.")
    parser.add_argument("--max-mermaid-edges", type=int, default=500)
    parser.add_argument("--max-file-mb", type=float, default=8.0)
    parser.add_argument("--workers", type=int, default=0, help="Process workers. 0 means CPU count - 1.")
    parser.add_argument(
        "--exclude-dir",
        action="append",
        default=[],
        help="Directory name to exclude. Can be passed multiple times.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    repo = args.repo.resolve()
    if not repo.exists() or not repo.is_dir():
        raise SystemExit(f"Repository root does not exist or is not a directory: {repo}")

    out_graph = args.out_graph or args.out
    out_jsonl = args.out_jsonl
    if out_jsonl is None:
        if out_graph is not None:
            out_jsonl = out_graph.with_suffix(".jsonl")
        else:
            raise SystemExit("Provide --out-jsonl or --out-graph/--out.")

    exclude_dirs = DEFAULT_EXCLUDE_DIRS | set(args.exclude_dir)
    try:
        records, summary = build_ast_graph(
            repo=repo,
            exclude_dirs=exclude_dirs,
            max_file_mb=args.max_file_mb,
            workers=args.workers,
        )
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(2) from exc

    write_jsonl(out_jsonl, records)
    if out_graph is not None:
        write_graph_json(out_graph, records, summary)
    if args.mermaid_out:
        write_mermaid(args.mermaid_out, records, args.max_mermaid_edges)

    print(
        "ast graph written:",
        out_jsonl,
        f"files={summary['files_scanned']}",
        f"functions={summary['functions']}",
        f"edges={summary['call_edges']}",
        f"external_edges={summary['external_call_edges']}",
    )


if __name__ == "__main__":
    main()
