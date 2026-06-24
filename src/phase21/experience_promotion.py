#!/usr/bin/env python
"""Promote failures, verifier output, and runtime outcomes into experience memory."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.phase11.persistent_memory import PersistentMemoryGateway
from src.phase16.experience_memory import ExperienceMemoryStore, ExperienceRecord, record_id


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class PromotionReport(StrictModel):
    run_id: str
    source_paths: list[str]
    appended_jsonl: int
    external_upsert: dict[str, Any]
    records: list[dict[str, Any]]
    status: str
    recommendation: str
    duration_ms: float
    created_at_unix: float = Field(default_factory=time.time)


class ExperiencePromoter:
    def __init__(
        self,
        *,
        store: ExperienceMemoryStore | None = None,
        workspace: str = "",
        postgres_dsn: str | None = None,
        qdrant_url: str | None = None,
        redis_url: str | None = None,
    ) -> None:
        self.store = store or ExperienceMemoryStore(ROOT / "artifacts" / "phase21" / "experience_memory.jsonl")
        self.workspace = workspace or str(ROOT)
        self.gateway = PersistentMemoryGateway(
            workspace=self.workspace,
            postgres_dsn=postgres_dsn,
            qdrant_url=qdrant_url,
            redis_url=redis_url,
            qdrant_collection="phase21_experience_memory",
        )

    async def promote_paths(self, paths: list[Path], *, run_id: str) -> PromotionReport:
        started = time.time()
        records: list[ExperienceRecord] = []
        for path in paths:
            if not path.exists():
                continue
            records.extend(self._records_from_payload(path, self._load_json(path)))
        appended = self.store.append_many(records)
        external_result: dict[str, Any] = {"skipped": True}
        if records:
            await self.gateway.initialize()
            memory_records = [
                self.gateway.build_record(
                    source=f"experience/{record.record_id}",
                    content=self._record_to_training_trace(record),
                    metadata={
                        "kind": "experience_memory",
                        "record_id": record.record_id,
                        "category": record.category,
                        "confidence": record.confidence,
                        "tags": record.tags,
                    },
                )
                for record in records
            ]
            external_result = await self.gateway.upsert(memory_records)
            await self.gateway.aclose()
        status = "complete" if appended or records else "empty"
        return PromotionReport(
            run_id=run_id,
            source_paths=[str(path) for path in paths],
            appended_jsonl=appended,
            external_upsert=external_result,
            records=[record.model_dump() for record in records[:200]],
            status=status,
            recommendation=(
                "Experience promoted. Retrieve these records during planning and use reviewed rows for SFT/GRPO."
                if records
                else "No promotable failures/outcomes found in supplied reports."
            ),
            duration_ms=(time.time() - started) * 1000,
        )

    def _load_json(self, path: Path) -> dict[str, Any]:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def _records_from_payload(self, path: Path, payload: dict[str, Any]) -> list[ExperienceRecord]:
        if "failure_analysis" in payload and "scorecard_run" in payload:
            return self._from_phase18(path, payload)
        if "checks" in payload and "findings" in payload:
            return self._from_phase19(path, payload)
        if "critic" in payload and "graph" in payload:
            return self._from_phase20(path, payload)
        return []

    def _from_phase18(self, path: Path, payload: dict[str, Any]) -> list[ExperienceRecord]:
        run_id = str(payload.get("run_id") or path.stem)
        outcomes = (((payload.get("scorecard_run") or {}).get("outcomes")) or [])
        records: list[ExperienceRecord] = []
        for outcome in outcomes:
            if outcome.get("status") == "ok":
                continue
            task_id = str(outcome.get("task_id") or "unknown")
            category = str(outcome.get("category") or "unknown")
            failed_phase = str(outcome.get("failed_phase") or "unknown")
            issue_type = str(outcome.get("issue_type") or "unknown")
            records.append(
                ExperienceRecord(
                    record_id=record_id(run_id, task_id, failed_phase, issue_type),
                    source_run_id=run_id,
                    task_id=task_id,
                    category=category,
                    failure=f"{failed_phase}:{issue_type}: {outcome.get('message', '')}",
                    fix=str(outcome.get("recommendation") or "Add targeted examples and verifier checks."),
                    outcome="phase18_real_model_scorecard_failure",
                    confidence=max(0.0, min(1.0, 1.0 - float(outcome.get("score") or 0.0))),
                    tags=["phase18", category, failed_phase, issue_type],
                    metadata={"source_report": str(path), "outcome": outcome},
                )
            )
        return records

    def _from_phase19(self, path: Path, payload: dict[str, Any]) -> list[ExperienceRecord]:
        run_id = str(payload.get("run_id") or path.stem)
        records: list[ExperienceRecord] = []
        for finding in payload.get("findings") or []:
            source = str(finding.get("source") or "verifier")
            severity = str(finding.get("severity") or "unknown")
            title = str(finding.get("title") or "Verifier finding")
            task_id = f"{source}:{finding.get('file_path') or 'workspace'}:{finding.get('line') or 0}"
            records.append(
                ExperienceRecord(
                    record_id=record_id(run_id, task_id, title),
                    source_run_id=run_id,
                    task_id=task_id,
                    category="verifier_security",
                    failure=f"{severity}: {title}: {finding.get('evidence', '')}",
                    fix=str(finding.get("recommendation") or payload.get("recommendation") or "Patch verifier finding."),
                    outcome="phase19_verifier_finding",
                    confidence=0.9 if severity in {"high", "critical"} else 0.7,
                    tags=["phase19", source, severity],
                    metadata={"source_report": str(path), "finding": finding},
                )
            )
        return records

    def _from_phase20(self, path: Path, payload: dict[str, Any]) -> list[ExperienceRecord]:
        run_id = str(payload.get("run_id") or path.stem)
        status = str(payload.get("status") or "unknown")
        critic = payload.get("critic") or {}
        verifier = payload.get("verifier") or {}
        return [
            ExperienceRecord(
                record_id=record_id(run_id, status, critic.get("summary", "")),
                source_run_id=run_id,
                task_id=run_id,
                category="taskgraph_runtime",
                failure=str(critic.get("summary") or "No critic summary."),
                fix=str(payload.get("final_answer") or "Review role-agent output and verifier recommendation."),
                outcome=f"phase20_status:{status}; verifier:{verifier.get('status')}",
                confidence=float(critic.get("confidence") or 0.5),
                tags=["phase20", status],
                metadata={"source_report": str(path), "critic": critic, "verifier": verifier},
            )
        ]

    def _record_to_training_trace(self, record: ExperienceRecord) -> str:
        return (
            f"Failure:\n{record.failure}\n\n"
            f"Fix:\n{record.fix}\n\n"
            f"Outcome:\n{record.outcome}\n\n"
            f"Tags: {', '.join(record.tags)}"
        )


def write_report(report: PromotionReport, output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / f"{report.run_id}.json"
    latest_path = output_dir / "experience-promotion-latest.json"
    md_path = output_dir / f"{report.run_id}.md"
    json_path.write_text(json.dumps(report.model_dump(), indent=2, ensure_ascii=False), encoding="utf-8")
    latest_path.write_text(json.dumps(report.model_dump(), indent=2, ensure_ascii=False), encoding="utf-8")
    lines = [
        "# Phase 21 Experience Promotion",
        "",
        f"- Run ID: `{report.run_id}`",
        f"- Status: `{report.status}`",
        f"- Appended JSONL: `{report.appended_jsonl}`",
        f"- Recommendation: {report.recommendation}",
    ]
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, md_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Promote reports into Phase 21 experience memory.")
    parser.add_argument("--run-id", default=f"phase21-{int(time.time())}")
    parser.add_argument("--workspace", default=str(ROOT))
    parser.add_argument("--report", action="append", default=[])
    parser.add_argument("--output-dir", type=Path, default=ROOT / "artifacts" / "phase21")
    parser.add_argument("--postgres-dsn")
    parser.add_argument("--qdrant-url")
    parser.add_argument("--redis-url")
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


async def async_main() -> int:
    args = parse_args()
    default_reports = [
        ROOT / "artifacts" / "phase18" / "model-quality-latest.json",
        ROOT / "artifacts" / "phase19" / "verifier-latest.json",
        ROOT / "artifacts" / "phase20" / "taskgraph-runtime-latest.json",
    ]
    paths = [Path(item) for item in args.report] if args.report else [path for path in default_reports if path.exists()]
    report = await ExperiencePromoter(
        workspace=args.workspace,
        postgres_dsn=args.postgres_dsn,
        qdrant_url=args.qdrant_url,
        redis_url=args.redis_url,
    ).promote_paths(paths, run_id=args.run_id)
    json_path, md_path = write_report(report, args.output_dir)
    print(json.dumps({"run_id": report.run_id, "status": report.status, "appended": report.appended_jsonl, "json": str(json_path), "markdown": str(md_path)}, indent=2))
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(async_main()))


if __name__ == "__main__":
    main()
