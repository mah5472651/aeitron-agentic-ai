"""Source balancing for training corpora.

The crawler intentionally keeps provenance. This module uses that provenance to
prevent a single source from dominating tokenizer/shard training.
"""

from __future__ import annotations

import json
import math
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

from pydantic import Field

from src.mythos.learning.quality import iter_jsonl
from src.mythos.shared.schemas import StrictModel


class SourceBalanceItem(StrictModel):
    source: str
    input_rows: int
    output_rows: int
    action: str
    avg_quality_score: float


class SourceBalanceReport(StrictModel):
    input_paths: list[str]
    output_path: str
    input_rows: int
    output_rows: int
    max_source_fraction: float
    sources: list[SourceBalanceItem]
    created_at_unix: float = Field(default_factory=time.time)


def _quality(row: dict[str, Any]) -> float:
    quality = row.get("quality")
    if isinstance(quality, dict):
        return float(quality.get("quality_score", 0.0))
    return 0.0


def _row_sort_key(row: dict[str, Any]) -> tuple[float, str, str]:
    return (
        -_quality(row),
        str(row.get("content_hash") or ""),
        str(row.get("url") or ""),
    )


def _cap_for_source(*, source_count: int, other_count: int, max_source_fraction: float, min_source_rows: int) -> int:
    if other_count <= 0:
        return source_count
    cap = math.floor((max_source_fraction / max(1e-9, 1.0 - max_source_fraction)) * other_count)
    return max(min_source_rows, min(source_count, cap))


def balance_clean_jsonl(
    *,
    input_paths: list[str | Path],
    output_path: str | Path,
    max_source_fraction: float = 0.35,
    min_source_rows: int = 25,
) -> SourceBalanceReport:
    if not 0.05 <= max_source_fraction <= 1.0:
        raise ValueError("max_source_fraction must be between 0.05 and 1.0")
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for path in input_paths:
        for row in iter_jsonl(path):
            source = str(row.get("source") or "unknown")
            groups[source].append(row)
    all_rows = sum(len(rows) for rows in groups.values())
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)

    selected: list[dict[str, Any]] = []
    items: list[SourceBalanceItem] = []
    for source, rows in sorted(groups.items()):
        rows = sorted(rows, key=_row_sort_key)
        other_count = all_rows - len(rows)
        cap = len(rows) if max_source_fraction >= 1.0 else _cap_for_source(
            source_count=len(rows),
            other_count=other_count,
            max_source_fraction=max_source_fraction,
            min_source_rows=min_source_rows,
        )
        chosen = rows[:cap]
        selected.extend(chosen)
        avg_quality = sum(_quality(row) for row in rows) / max(1, len(rows))
        items.append(
            SourceBalanceItem(
                source=source,
                input_rows=len(rows),
                output_rows=len(chosen),
                action="kept" if len(chosen) == len(rows) else "capped",
                avg_quality_score=round(avg_quality, 6),
            )
        )

    selected.sort(key=lambda row: (str(row.get("source") or ""), str(row.get("content_hash") or ""), str(row.get("url") or "")))
    with target.open("w", encoding="utf-8") as handle:
        for row in selected:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True))
            handle.write("\n")

    return SourceBalanceReport(
        input_paths=[str(path) for path in input_paths],
        output_path=str(target),
        input_rows=all_rows,
        output_rows=len(selected),
        max_source_fraction=max_source_fraction,
        sources=items,
    )
