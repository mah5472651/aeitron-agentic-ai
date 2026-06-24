#!/usr/bin/env python
"""Review gate for SFT/GRPO candidates."""

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


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class ReviewDecision(StrictModel):
    row_id: str
    status: str
    reason: str
    prompt: str
    chosen: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class ReviewGateReport(StrictModel):
    run_id: str
    reviewed: int
    train_ready: int
    needs_review: int
    rejected: int
    decisions: list[dict[str, Any]]
    train_ready_path: str
    created_at_unix: float = Field(default_factory=time.time)


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def decide(row: dict[str, Any], *, auto_approve_verifier: bool) -> ReviewDecision:
    prompt = str(row.get("prompt") or "")
    chosen = str(row.get("chosen") or "")
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    review_status = str(metadata.get("review_status") or "")
    row_id = str(metadata.get("task_id") or metadata.get("record_id") or f"row-{abs(hash(prompt + chosen))}")
    if len(prompt) < 20 or len(chosen) < 40:
        status, reason = "rejected", "prompt/chosen too short"
    elif "candidate_needs" in review_status:
        status, reason = "needs_human_review", "candidate requires explicit review"
    elif auto_approve_verifier and metadata.get("verifier_status") == "ok":
        status, reason = "train_ready", "verifier approved"
    elif metadata.get("review_status") in {"human_approved", "verifier_approved", "train_ready"}:
        status, reason = "train_ready", "approved metadata"
    else:
        status, reason = "needs_human_review", "no approval metadata"
    return ReviewDecision(row_id=row_id, status=status, reason=reason, prompt=prompt, chosen=chosen, metadata=metadata)


def run_gate(paths: list[Path], *, output_dir: Path, run_id: str, auto_approve_verifier: bool = False) -> ReviewGateReport:
    decisions = [decide(row, auto_approve_verifier=auto_approve_verifier) for path in paths for row in load_jsonl(path)]
    output_dir.mkdir(parents=True, exist_ok=True)
    train_ready_path = output_dir / f"{run_id}-train-ready.jsonl"
    ready_rows = [{"prompt": item.prompt, "chosen": item.chosen, "metadata": {**item.metadata, "review_status": "train_ready"}} for item in decisions if item.status == "train_ready"]
    train_ready_path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in ready_rows) + ("\n" if ready_rows else ""), encoding="utf-8")
    return ReviewGateReport(
        run_id=run_id,
        reviewed=len(decisions),
        train_ready=len(ready_rows),
        needs_review=sum(1 for item in decisions if item.status == "needs_human_review"),
        rejected=sum(1 for item in decisions if item.status == "rejected"),
        decisions=[item.model_dump() for item in decisions[:500]],
        train_ready_path=str(train_ready_path),
    )


def write_report(report: ReviewGateReport, output_dir: Path) -> tuple[Path, Path]:
    json_path = output_dir / f"{report.run_id}.json"
    latest_path = output_dir / "dataset-review-latest.json"
    json_path.write_text(json.dumps(report.model_dump(), indent=2, ensure_ascii=False), encoding="utf-8")
    latest_path.write_text(json.dumps(report.model_dump(), indent=2, ensure_ascii=False), encoding="utf-8")
    return json_path, latest_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run dataset review gate.")
    parser.add_argument("--input", action="append", default=[])
    parser.add_argument("--run-id", default=f"phase29-{int(time.time())}")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "artifacts" / "phase29")
    parser.add_argument("--auto-approve-verifier", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    default_inputs = [
        ROOT / "artifacts" / "phase18" / "phase18-qwen-local-smoke1-reviewed-sft-candidates.jsonl",
        ROOT / "artifacts" / "phase16" / "scorecard_failures_sft.jsonl",
    ]
    paths = [Path(item) for item in args.input] if args.input else [path for path in default_inputs if path.exists()]
    report = run_gate(paths, output_dir=args.output_dir, run_id=args.run_id, auto_approve_verifier=args.auto_approve_verifier)
    json_path, _ = write_report(report, args.output_dir)
    print(json.dumps({"run_id": report.run_id, "reviewed": report.reviewed, "train_ready": report.train_ready, "needs_review": report.needs_review, "json": str(json_path)}, indent=2))


if __name__ == "__main__":
    main()
