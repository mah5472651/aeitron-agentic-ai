#!/usr/bin/env python
"""Long-context packer combining workspace memory and experience memory."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.phase11.memory_engine import WorkspaceMemoryEngine
from src.phase25.experience_retrieval import ExperienceRetriever


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class PackedContextReport(StrictModel):
    run_id: str
    query: str
    workspace: str
    token_budget: int
    estimated_tokens: int
    sections: list[dict[str, Any]]
    context: str
    created_at_unix: float = Field(default_factory=time.time)


class LongContextPacker:
    def pack(self, *, workspace: str | Path, query: str, token_budget: int = 32000, max_items: int = 32) -> PackedContextReport:
        memory = WorkspaceMemoryEngine(workspace)
        context_pack = memory.retrieve(query, token_budget=max(512, int(token_budget * 0.75)), max_items=max_items)
        experience = ExperienceRetriever().retrieve(query, limit=8)
        sections: list[dict[str, Any]] = []
        text_parts = [f"# Packed Context\nQuery: {query}\n"]
        if experience.records:
            sections.append({"kind": "experience", "count": len(experience.records)})
            text_parts.append("## Experience Memory\n" + experience.context_block)
        for item in context_pack.items:
            sections.append({"kind": item.kind, "source": item.source, "score": item.score, "tokens": max(1, len(item.content) // 4)})
            text_parts.append(f"## {item.source}\n{item.content[:6000]}")
        context = "\n\n".join(text_parts)
        if len(context) // 4 > token_budget:
            context = context[: token_budget * 4]
        return PackedContextReport(
            run_id=f"phase31-{int(time.time())}",
            query=query,
            workspace=str(workspace),
            token_budget=token_budget,
            estimated_tokens=max(1, len(context) // 4),
            sections=sections,
            context=context,
        )


def write_report(report: PackedContextReport, output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / f"{report.run_id}.json"
    latest_path = output_dir / "long-context-latest.json"
    json_path.write_text(json.dumps(report.model_dump(), indent=2, ensure_ascii=False), encoding="utf-8")
    latest_path.write_text(json.dumps(report.model_dump(), indent=2, ensure_ascii=False), encoding="utf-8")
    return json_path, latest_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Pack long context for planning.")
    parser.add_argument("--workspace", default=str(ROOT))
    parser.add_argument("--query", default="agentic coding security verifier memory")
    parser.add_argument("--token-budget", type=int, default=32000)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "artifacts" / "phase31")
    args = parser.parse_args()
    report = LongContextPacker().pack(workspace=args.workspace, query=args.query, token_budget=args.token_budget)
    json_path, _ = write_report(report, args.output_dir)
    print(json.dumps({"run_id": report.run_id, "estimated_tokens": report.estimated_tokens, "sections": len(report.sections), "json": str(json_path)}, indent=2))


if __name__ == "__main__":
    main()

