#!/usr/bin/env python
"""PostgreSQL-backed regression tracking, reports, and webhooks."""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

import httpx

from src.phase9.models import BenchmarkResult


CREATE_SQL = """
CREATE TABLE IF NOT EXISTS evaluation_runs (
    id BIGSERIAL PRIMARY KEY,
    run_id TEXT NOT NULL,
    timestamp DOUBLE PRECISION NOT NULL,
    benchmark TEXT NOT NULL,
    score DOUBLE PRECISION NOT NULL,
    delta DOUBLE PRECISION NOT NULL,
    metrics JSONB NOT NULL,
    sample_results JSONB NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_evaluation_runs_benchmark_time
ON evaluation_runs (benchmark, timestamp DESC);
"""


class RegressionTracker:
    def __init__(
        self,
        postgres_dsn: str | None = None,
        jsonl_fallback: Path | None = None,
        alert_webhook: str | None = None,
        regression_threshold: float = 0.02,
    ) -> None:
        self.postgres_dsn = postgres_dsn
        self.jsonl_fallback = jsonl_fallback
        self.alert_webhook = alert_webhook
        self.regression_threshold = regression_threshold

    async def init_db(self) -> None:
        if not self.postgres_dsn:
            return
        import asyncpg

        conn = await asyncpg.connect(self.postgres_dsn)
        try:
            await conn.execute(CREATE_SQL)
        finally:
            await conn.close()

    async def previous_scores(self, benchmark: str, limit: int = 5) -> list[dict[str, Any]]:
        if self.postgres_dsn:
            import asyncpg

            conn = await asyncpg.connect(self.postgres_dsn)
            try:
                rows = await conn.fetch(
                    "SELECT run_id, timestamp, benchmark, score, metrics FROM evaluation_runs WHERE benchmark=$1 ORDER BY timestamp DESC LIMIT $2",
                    benchmark,
                    limit,
                )
                return [dict(row) for row in rows]
            finally:
                await conn.close()
        if not self.jsonl_fallback or not self.jsonl_fallback.exists():
            return []
        rows = [json.loads(line) for line in self.jsonl_fallback.read_text(encoding="utf-8").splitlines() if line.strip()]
        return [row for row in reversed(rows) if row["benchmark"] == benchmark][:limit]

    async def save_result(self, result: BenchmarkResult) -> float:
        previous = await self.previous_scores(result.benchmark, limit=1)
        if previous:
            previous_score = float(previous[0]["score"])
            delta = result.score - previous_score
        else:
            delta = 0.0
        if self.postgres_dsn:
            import asyncpg

            conn = await asyncpg.connect(self.postgres_dsn)
            try:
                await conn.execute(
                    """
                    INSERT INTO evaluation_runs (run_id, timestamp, benchmark, score, delta, metrics, sample_results)
                    VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7::jsonb)
                    """,
                    result.run_id,
                    result.timestamp,
                    result.benchmark,
                    result.score,
                    delta,
                    json.dumps(result.metrics),
                    json.dumps([asdict(item) for item in result.sample_results]),
                )
            finally:
                await conn.close()
        if self.jsonl_fallback:
            self.jsonl_fallback.parent.mkdir(parents=True, exist_ok=True)
            record = result.to_record()
            record["delta"] = delta
            with self.jsonl_fallback.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        if delta < -self.regression_threshold:
            await self.alert(result, delta)
        return delta

    async def alert(self, result: BenchmarkResult, delta: float) -> None:
        if not self.alert_webhook:
            return
        message = {
            "text": f"Evaluation regression: {result.benchmark} dropped {delta * 100:.2f}% on run {result.run_id}. Current score={result.score:.4f}."
        }
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(self.alert_webhook, json=message)

    async def write_markdown_report(self, results: list[BenchmarkResult], output_path: Path) -> None:
        lines = ["# Phase 9 Evaluation Report", ""]
        for result in results:
            previous = await self.previous_scores(result.benchmark, limit=5)
            delta = result.score - (float(previous[0]["score"]) if previous else result.score)
            lines.extend(
                [
                    f"## {result.benchmark}",
                    "",
                    f"- Run ID: `{result.run_id}`",
                    f"- Score: `{result.score:.4f}`",
                    f"- Delta vs previous: `{delta:+.4f}`",
                    f"- Samples: `{len(result.sample_results)}`",
                    "",
                    "| Metric | Value |",
                    "| --- | ---: |",
                ]
            )
            for key, value in sorted(result.metrics.items()):
                lines.append(f"| {key} | {value:.4f} |")
            lines.extend(["", "| Previous Run | Score |", "| --- | ---: |"])
            for row in previous:
                lines.append(f"| `{row['run_id']}` | {float(row['score']):.4f} |")
            lines.append("")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text("\n".join(lines), encoding="utf-8")
