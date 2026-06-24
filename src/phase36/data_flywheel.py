#!/usr/bin/env python
"""Data flywheel from Phase 18 failures to Phase 3 queues and Phase 7 triggers."""

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

from src.phase29.dataset_review_gate import run_gate, write_report as write_review_report


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class FlywheelReport(StrictModel):
    run_id: str
    phase18_report: str
    phase3_queue_path: str
    phase7_trigger_path: str
    queued_for_rejection_sampling: int
    reviewed_candidates: int
    train_ready: int
    trigger_state: str
    phase7_command: list[str]
    recommendation: str
    created_at_unix: float = Field(default_factory=time.time)


def load_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


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


def build_phase3_queue_rows(phase18: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    promotion = phase18.get("promotion") if isinstance(phase18.get("promotion"), dict) else {}
    sft_path = Path(str(promotion.get("sft_path") or ""))
    for row in load_jsonl(sft_path):
        metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        rows.append(
            {
                "schema": "phase36.phase3_queue.v1",
                "source": "phase18_failure_candidate",
                "status": "queued_for_rejection_sampling",
                "prompt": row.get("prompt"),
                "candidate_chosen": row.get("chosen"),
                "metadata": {
                    **metadata,
                    "phase18_run_id": phase18.get("run_id"),
                    "review_required": True,
                    "target_pipeline": "phase3_rejection_sampling",
                },
            }
        )
    if rows:
        return rows
    for outcome in (((phase18.get("scorecard_run") or {}).get("outcomes")) or []):
        if outcome.get("status") == "ok":
            continue
        rows.append(
            {
                "schema": "phase36.phase3_queue.v1",
                "source": "phase18_scorecard_failure",
                "status": "queued_for_rejection_sampling",
                "prompt": f"Repair failed scorecard task {outcome.get('task_id')}: {outcome.get('message')}",
                "candidate_chosen": "",
                "metadata": {"outcome": outcome, "phase18_run_id": phase18.get("run_id"), "review_required": True},
            }
        )
    return rows


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + ("\n" if rows else ""), encoding="utf-8")


def build_phase7_command(train_ready_path: Path) -> list[str]:
    return [
        sys.executable,
        "src/phase7/grpo_training_loop.py",
        "--dataset-jsonl",
        str(train_ready_path),
        "--beta",
        "0.01",
        "--group-size",
        "8",
    ]


def run_flywheel(args: argparse.Namespace) -> FlywheelReport:
    phase18_path = args.phase18_report
    phase18 = load_json(phase18_path)
    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    phase3_queue = output_dir / f"{args.run_id}-phase3-rejection-queue.jsonl"
    queue_rows = build_phase3_queue_rows(phase18)
    write_jsonl(phase3_queue, queue_rows)

    candidate_inputs = []
    promotion = phase18.get("promotion") if isinstance(phase18.get("promotion"), dict) else {}
    if promotion.get("sft_path"):
        candidate_inputs.append(Path(str(promotion["sft_path"])))
    if not candidate_inputs:
        fallback = ROOT / "artifacts" / "phase18" / "phase18-qwen-local-smoke1-reviewed-sft-candidates.jsonl"
        if fallback.exists():
            candidate_inputs.append(fallback)
    review = run_gate(candidate_inputs, output_dir=output_dir, run_id=f"{args.run_id}-review", auto_approve_verifier=args.auto_approve_verifier)
    write_review_report(review, output_dir)

    trigger_path = output_dir / f"{args.run_id}-phase7-trigger.json"
    command = build_phase7_command(Path(review.train_ready_path))
    if review.train_ready <= 0:
        trigger_state = "blocked_waiting_for_reviewed_train_ready_rows"
        recommendation = "Review candidates in Phase 29; training remains blocked until rows are approved."
    elif args.execute_training:
        trigger_state = "execution_requested_but_manual_gpu_guard_required"
        recommendation = "Training command prepared. Execute on Linux CUDA after confirming dataset and GPU."
    else:
        trigger_state = "queued_for_phase7_training"
        recommendation = "Train-ready rows exist; run command on GPU host when ready."
    trigger_payload = {
        "run_id": args.run_id,
        "state": trigger_state,
        "train_ready_path": review.train_ready_path,
        "train_ready": review.train_ready,
        "command": command,
        "created_at_unix": time.time(),
    }
    trigger_path.write_text(json.dumps(trigger_payload, indent=2, ensure_ascii=False), encoding="utf-8")
    latest_path = output_dir / "data-flywheel-latest.json"
    report = FlywheelReport(
        run_id=args.run_id,
        phase18_report=str(phase18_path),
        phase3_queue_path=str(phase3_queue),
        phase7_trigger_path=str(trigger_path),
        queued_for_rejection_sampling=len(queue_rows),
        reviewed_candidates=review.reviewed,
        train_ready=review.train_ready,
        trigger_state=trigger_state,
        phase7_command=command,
        recommendation=recommendation,
    )
    latest_path.write_text(json.dumps(report.model_dump(), indent=2, ensure_ascii=False), encoding="utf-8")
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Phase 36 data flywheel.")
    parser.add_argument("--run-id", default=f"phase36-{int(time.time())}")
    parser.add_argument("--phase18-report", type=Path, default=ROOT / "artifacts" / "phase18" / "model-quality-latest.json")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "artifacts" / "phase36")
    parser.add_argument("--auto-approve-verifier", action="store_true")
    parser.add_argument("--execute-training", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    report = run_flywheel(args)
    print(json.dumps(report.model_dump(), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
