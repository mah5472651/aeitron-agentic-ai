"""Extract supervised/evaluation task candidates from clean corpus shards."""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from pydantic import Field

from src.mythos.learning.quality import iter_jsonl, stable_hash
from src.mythos.shared.schemas import StrictModel


CODE_FENCE_RE = re.compile(r"```([a-zA-Z0-9_+\-.#]*)\n(.*?)```", re.DOTALL)
SECURITY_TERMS = ("cve", "cwe", "vulnerability", "exploit", "injection", "overflow", "xss", "csrf", "auth")


class ExtractedTask(StrictModel):
    task_id: str
    task_type: str
    prompt: str
    source_url: str | None = None
    language: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class TaskExtractionReport(StrictModel):
    output_path: str
    scanned_rows: int
    extracted: int
    by_type: dict[str, int]
    created_at_unix: float = Field(default_factory=time.time)


def _task_type(text: str, language: str | None) -> str:
    lowered = text.lower()
    if any(term in lowered for term in SECURITY_TERMS):
        if "patch" in lowered or "fix" in lowered or "mitigation" in lowered:
            return "security_patch_generation"
        return "security_finding"
    if language or "def " in text or "class " in text or "function " in text:
        return "agentic_coding"
    return "technical_reasoning"


def _prompt_from_row(row: dict[str, Any], code: str | None = None, language: str | None = None) -> str:
    text = str(row.get("text") or row.get("content") or "")
    source = row.get("url") or "approved corpus"
    task_type = _task_type(text, language)
    if code:
        return (
            f"Using the approved source context from {source}, analyze and improve the following "
            f"{language or 'code'} artifact for {task_type}. Return a safe, defensive answer.\n\n{code[:8000]}"
        )
    return f"Summarize the defensive coding or security lesson from this approved source: {source}\n\n{text[:8000]}"


def extract_tasks(input_paths: list[str | Path], output_path: str | Path, *, max_tasks: int = 10_000) -> TaskExtractionReport:
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    scanned = extracted = 0
    by_type: dict[str, int] = {}
    seen: set[str] = set()
    with target.open("w", encoding="utf-8") as handle:
        for path in input_paths:
            for row in iter_jsonl(path):
                scanned += 1
                text = str(row.get("text") or row.get("content") or "")
                quality = row.get("quality", {})
                language = quality.get("language_hint") or row.get("language")
                candidates: list[tuple[str | None, str | None]] = []
                for match in CODE_FENCE_RE.finditer(text):
                    candidates.append((match.group(2).strip(), match.group(1).strip() or language))
                if not candidates:
                    candidates.append((None, language))
                for code, candidate_language in candidates[:3]:
                    prompt = _prompt_from_row(row, code=code, language=candidate_language)
                    digest = stable_hash(prompt)
                    if digest in seen:
                        continue
                    seen.add(digest)
                    task_type = _task_type(text if code is None else f"{text}\n{code}", candidate_language)
                    task = ExtractedTask(
                        task_id=f"task-{digest[:16]}",
                        task_type=task_type,
                        prompt=prompt,
                        source_url=row.get("url"),
                        language=candidate_language,
                        metadata={
                            "source": row.get("source"),
                            "license": row.get("license"),
                            "content_hash": row.get("content_hash") or quality.get("content_hash"),
                        },
                    )
                    handle.write(json.dumps(task.model_dump(), ensure_ascii=False, sort_keys=True) + "\n")
                    extracted += 1
                    by_type[task_type] = by_type.get(task_type, 0) + 1
                    if extracted >= max_tasks:
                        return TaskExtractionReport(output_path=str(target), scanned_rows=scanned, extracted=extracted, by_type=by_type)
    return TaskExtractionReport(output_path=str(target), scanned_rows=scanned, extracted=extracted, by_type=by_type)
