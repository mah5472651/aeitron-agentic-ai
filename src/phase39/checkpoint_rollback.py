#!/usr/bin/env python
"""Phase 39 training checkpoint rollback and promotion gate.

GRPO/SFT can regress a model. This gate compares candidate evaluation metrics
against a baseline, promotes only non-regressing checkpoints, and writes a
rollback manifest that can restore the last known-good active checkpoint.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class MetricDecision(StrictModel):
    name: str
    baseline: float | None
    candidate: float | None
    delta: float | None
    threshold: float
    status: str


class CheckpointGateReport(StrictModel):
    run_id: str
    candidate_checkpoint: str
    active_checkpoint_before: str | None
    active_checkpoint_after: str | None
    decision: str
    reason: str
    metrics: list[MetricDecision]
    rollback_manifest: str | None
    registry_path: str
    created_at_unix: float = Field(default_factory=time.time)


@dataclass(frozen=True)
class GateConfig:
    registry_dir: Path = ROOT / "artifacts" / "phase39" / "registry"
    active_pointer: Path = ROOT / "artifacts" / "phase39" / "active_checkpoint.json"
    max_drop: float = 0.02
    required_metrics: tuple[str, ...] = ("overall_score", "pass_at_1", "security_score")
    dry_run: bool = False


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def nested_get(payload: dict[str, Any], candidates: list[str]) -> float | None:
    for name in candidates:
        value: Any = payload
        for part in name.split("."):
            if isinstance(value, dict) and part in value:
                value = value[part]
            else:
                value = None
                break
        if isinstance(value, (int, float)):
            return float(value)
    return None


def metric_aliases(name: str) -> list[str]:
    return {
        "overall_score": ["score", "metrics.overall_score", "candidate_score", "summary.overall_score"],
        "pass_at_1": ["pass_at_1", "metrics.pass_at_1", "metrics.pass@1", "human_eval.pass_at_1"],
        "pass_at_10": ["pass_at_10", "metrics.pass_at_10", "metrics.pass@10", "human_eval.pass_at_10"],
        "security_score": ["security_score", "metrics.security_detection_fix_score", "category_scores.security_reasoning", "security.score"],
        "sandbox_pass_rate": ["sandbox_pass_rate", "metrics.sandbox_test_pass_rate"],
    }.get(name, [name])


class CheckpointRollbackGate:
    def __init__(self, config: GateConfig | None = None) -> None:
        self.config = config or GateConfig()
        self.config.registry_dir.mkdir(parents=True, exist_ok=True)
        self.config.active_pointer.parent.mkdir(parents=True, exist_ok=True)

    def evaluate(
        self,
        *,
        candidate_checkpoint: Path,
        candidate_report: Path,
        baseline_report: Path | None,
        run_id: str,
        promote: bool,
    ) -> CheckpointGateReport:
        candidate_payload = load_json(candidate_report)
        baseline_payload = load_json(baseline_report) if baseline_report and baseline_report.exists() else {}
        active_before = self.active_checkpoint()
        baseline_for_metrics = baseline_payload or self._active_eval_payload()
        metric_decisions = self.compare_metrics(candidate_payload, baseline_for_metrics)
        regressions = [item for item in metric_decisions if item.status == "regressed"]
        missing_required = [item for item in metric_decisions if item.status == "missing"]
        if not candidate_checkpoint.exists():
            decision = "rejected"
            reason = f"candidate checkpoint does not exist: {candidate_checkpoint}"
        elif missing_required:
            decision = "rejected"
            reason = "required metric(s) missing: " + ", ".join(item.name for item in missing_required)
        elif regressions:
            decision = "rollback"
            reason = "metric regression exceeded threshold: " + ", ".join(
                f"{item.name} delta={item.delta:.4f}" for item in regressions if item.delta is not None
            )
        elif promote:
            decision = "promoted"
            reason = "candidate passed degradation gate and was promoted"
        else:
            decision = "approved_pending_promotion"
            reason = "candidate passed degradation gate; run with --promote to update active pointer"

        rollback_manifest: Path | None = None
        active_after = active_before
        if decision == "promoted" and not self.config.dry_run:
            rollback_manifest = self._write_rollback_manifest(run_id, active_before, candidate_checkpoint)
            active_after = str(candidate_checkpoint.resolve())
            self._write_active_pointer(candidate_checkpoint, candidate_report, run_id)
        elif decision == "rollback" and active_before and not self.config.dry_run:
            rollback_manifest = self._write_rollback_manifest(run_id, active_before, candidate_checkpoint)
            active_after = active_before

        report = CheckpointGateReport(
            run_id=run_id,
            candidate_checkpoint=str(candidate_checkpoint.resolve()),
            active_checkpoint_before=active_before,
            active_checkpoint_after=active_after,
            decision=decision,
            reason=reason,
            metrics=metric_decisions,
            rollback_manifest=str(rollback_manifest) if rollback_manifest else None,
            registry_path=str(self.config.registry_dir),
        )
        self._write_report(report)
        return report

    def rollback(self, manifest_path: Path, *, run_id: str) -> CheckpointGateReport:
        manifest = load_json(manifest_path)
        previous = manifest.get("active_checkpoint_before")
        candidate = manifest.get("candidate_checkpoint")
        if not previous:
            reason = "rollback manifest has no previous active checkpoint"
            decision = "rollback_unavailable"
            active_after = self.active_checkpoint()
        else:
            self._write_active_pointer(Path(previous), Path(manifest.get("baseline_report") or ""), run_id)
            reason = "active checkpoint pointer restored from rollback manifest"
            decision = "rolled_back"
            active_after = previous
        report = CheckpointGateReport(
            run_id=run_id,
            candidate_checkpoint=str(candidate or ""),
            active_checkpoint_before=manifest.get("active_checkpoint_before"),
            active_checkpoint_after=active_after,
            decision=decision,
            reason=reason,
            metrics=[],
            rollback_manifest=str(manifest_path.resolve()),
            registry_path=str(self.config.registry_dir),
        )
        self._write_report(report)
        return report

    def compare_metrics(self, candidate_payload: dict[str, Any], baseline_payload: dict[str, Any]) -> list[MetricDecision]:
        decisions: list[MetricDecision] = []
        for name in self.config.required_metrics:
            candidate_value = nested_get(candidate_payload, metric_aliases(name))
            baseline_value = nested_get(baseline_payload, metric_aliases(name))
            if candidate_value is None:
                decisions.append(
                    MetricDecision(name=name, baseline=baseline_value, candidate=None, delta=None, threshold=self.config.max_drop, status="missing")
                )
                continue
            if baseline_value is None:
                decisions.append(
                    MetricDecision(name=name, baseline=None, candidate=candidate_value, delta=None, threshold=self.config.max_drop, status="no_baseline")
                )
                continue
            delta = candidate_value - baseline_value
            status = "regressed" if delta < -self.config.max_drop else "passed"
            decisions.append(
                MetricDecision(name=name, baseline=baseline_value, candidate=candidate_value, delta=delta, threshold=self.config.max_drop, status=status)
            )
        return decisions

    def active_checkpoint(self) -> str | None:
        if not self.config.active_pointer.exists():
            return None
        try:
            payload = load_json(self.config.active_pointer)
        except (OSError, json.JSONDecodeError):
            return None
        checkpoint = payload.get("checkpoint")
        return str(checkpoint) if checkpoint else None

    def _active_eval_payload(self) -> dict[str, Any]:
        if not self.config.active_pointer.exists():
            return {}
        try:
            payload = load_json(self.config.active_pointer)
            report_path = payload.get("eval_report")
            if report_path and Path(report_path).exists():
                return load_json(Path(report_path))
        except (OSError, json.JSONDecodeError):
            return {}
        return {}

    def _write_active_pointer(self, checkpoint: Path, eval_report: Path, run_id: str) -> None:
        payload = {
            "checkpoint": str(checkpoint.resolve()),
            "eval_report": str(eval_report.resolve()) if str(eval_report) else None,
            "run_id": run_id,
            "updated_at_unix": time.time(),
        }
        self.config.active_pointer.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def _write_rollback_manifest(self, run_id: str, active_before: str | None, candidate_checkpoint: Path) -> Path:
        manifest_path = self.config.registry_dir / f"{run_id}-rollback-manifest.json"
        payload = {
            "run_id": run_id,
            "active_checkpoint_before": active_before,
            "candidate_checkpoint": str(candidate_checkpoint.resolve()),
            "active_pointer": str(self.config.active_pointer.resolve()),
            "created_at_unix": time.time(),
        }
        manifest_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return manifest_path

    def _write_report(self, report: CheckpointGateReport) -> None:
        report_path = self.config.registry_dir / f"{report.run_id}.json"
        latest_path = self.config.registry_dir.parent / "checkpoint-gate-latest.json"
        report_path.write_text(json.dumps(report.model_dump(), indent=2, ensure_ascii=False), encoding="utf-8")
        latest_path.write_text(json.dumps(report.model_dump(), indent=2, ensure_ascii=False), encoding="utf-8")


def parse_required_metrics(raw: str) -> tuple[str, ...]:
    values = tuple(item.strip() for item in raw.split(",") if item.strip())
    if not values:
        raise argparse.ArgumentTypeError("at least one metric is required")
    return values


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Phase 39 checkpoint rollback gate.")
    parser.add_argument("--candidate-checkpoint", type=Path, required=True)
    parser.add_argument("--candidate-report", type=Path, required=True)
    parser.add_argument("--baseline-report", type=Path)
    parser.add_argument("--run-id", default=f"phase39-{int(time.time())}")
    parser.add_argument("--registry-dir", type=Path, default=ROOT / "artifacts" / "phase39" / "registry")
    parser.add_argument("--active-pointer", type=Path, default=ROOT / "artifacts" / "phase39" / "active_checkpoint.json")
    parser.add_argument("--max-drop", type=float, default=0.02)
    parser.add_argument("--required-metrics", type=parse_required_metrics, default=("overall_score", "pass_at_1", "security_score"))
    parser.add_argument("--promote", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--rollback-manifest", type=Path)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    gate = CheckpointRollbackGate(
        GateConfig(
            registry_dir=args.registry_dir,
            active_pointer=args.active_pointer,
            max_drop=args.max_drop,
            required_metrics=args.required_metrics,
            dry_run=args.dry_run,
        )
    )
    if args.rollback_manifest:
        report = gate.rollback(args.rollback_manifest, run_id=args.run_id)
    else:
        report = gate.evaluate(
            candidate_checkpoint=args.candidate_checkpoint,
            candidate_report=args.candidate_report,
            baseline_report=args.baseline_report,
            run_id=args.run_id,
            promote=args.promote,
        )
    print(json.dumps(report.model_dump(), indent=2, ensure_ascii=False))
    if report.decision in {"rejected", "rollback", "rollback_unavailable"}:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
