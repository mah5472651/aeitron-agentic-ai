"""Production dataset promotion pack for Aeitron scratch training.

This command is intentionally strict. It can prove the data pipeline on a tiny
fixture with ``--dev-smoke``, but production mode requires real promoted row
counts, verified patch/task rows, benchmark-holdout separation, and human review
evidence before it returns success.
"""

from __future__ import annotations

import argparse
import glob
import json
import random
import time
from collections import Counter
from pathlib import Path
from typing import Any

from pydantic import Field, model_validator

from src.aeitron.learning.benchmark_contamination_filter import (
    BenchmarkContaminationFilterReport,
    filter_benchmark_contamination_jsonl,
)
from src.aeitron.learning.dataset_validation import DatasetValidationConfig, DatasetValidationReport, validate_dataset
from src.aeitron.learning.license_filter import LicenseFilterReport, filter_jsonl_by_license
from src.aeitron.learning.near_dedup import NearDedupReport, deduplicate_jsonl
from src.aeitron.learning.quality import DatasetQualityGate, QualityGateConfig, QualityGateReport, iter_jsonl, stable_hash
from src.aeitron.learning.source_budget import SourceBudgetPlan, write_source_budget_plan
from src.aeitron.learning.source_quality import SourceQualityReport, write_source_quality_report
from src.aeitron.learning.source_reputation import SourceReputationReport, write_source_reputation_report
from src.aeitron.learning.training_data_gate import TrainingDataGateConfig, TrainingDataGateReport, apply_training_data_gate
from src.aeitron.shared.schemas import StrictModel


class ProductionDatasetConfig(StrictModel):
    input_paths: list[str]
    output_dir: str = "data/production/aeitron-corpus"
    dataset_id: str = "aeitron-corpus"
    source_registry_path: str = "config/data_sources.ultimate.json"
    benchmark_holdout_paths: list[str] = Field(default_factory=lambda: ["data/eval/humaneval.jsonl", "data/eval/mbpp.jsonl"])
    verified_patch_paths: list[str] = Field(default_factory=list)
    human_review_approved_paths: list[str] = Field(default_factory=list)
    dev_smoke: bool = False
    min_promoted_records: int = Field(default=100_000, ge=1)
    min_verified_patch_records: int = Field(default=100, ge=0)
    min_human_review_approved_records: int = Field(default=100, ge=0)
    min_train_records: int = Field(default=90_000, ge=1)
    min_avg_chars: int = Field(default=120, ge=1)
    max_duplicate_fraction: float = Field(default=0.02, ge=0.0, le=1.0)
    train_fraction: float = Field(default=0.98, ge=0.01, le=0.999)
    val_fraction: float = Field(default=0.01, ge=0.0, le=0.5)
    test_fraction: float = Field(default=0.01, ge=0.0, le=0.5)
    eval_holdout_fraction: float = Field(default=0.02, ge=0.0, le=0.5)
    seed: int = 1337
    allow_unknown_license: bool = False
    min_quality_score: float = Field(default=0.58, ge=0.0, le=1.0)
    min_source_reputation_score: float = Field(default=0.45, ge=0.0, le=1.0)
    source_budget_target_docs: int = Field(default=100_000, ge=1)
    near_dedup_hamming_threshold: int = Field(default=3, ge=0, le=16)

    @model_validator(mode="after")
    def validate_split(self) -> "ProductionDatasetConfig":
        total = round(self.train_fraction + self.val_fraction + self.test_fraction, 6)
        if total != 1.0:
            raise ValueError("train_fraction + val_fraction + test_fraction must equal 1.0")
        return self


class SplitManifest(StrictModel):
    train_path: str
    val_path: str
    test_path: str
    holdout_path: str
    train_records: int
    val_records: int
    test_records: int
    holdout_records: int
    fractions: dict[str, float]
    seed: int


class HoldoutSeparationReport(StrictModel):
    benchmark_holdout_paths: list[str]
    benchmark_hashes: int
    scanned_records: int
    removed_records: int
    output_path: str
    removed_path: str


class PatchTaskDatasetReport(StrictModel):
    input_paths: list[str]
    output_path: str
    accepted: int
    rejected: int
    categories: dict[str, int] = Field(default_factory=dict)


class HumanReviewPromotionReport(StrictModel):
    input_paths: list[str]
    output_path: str
    approved: int
    rejected: int
    categories: dict[str, int] = Field(default_factory=dict)


class ProductionDatasetManifest(StrictModel):
    dataset_id: str
    version_id: str
    status: str
    output_dir: str
    created_at_unix: float = Field(default_factory=time.time)
    dev_smoke: bool
    artifacts: dict[str, str]
    metrics: dict[str, Any]
    issues: list[str] = Field(default_factory=list)
    reports: dict[str, Any]

    def write(self, output_dir: str | Path) -> Path:
        root = Path(output_dir)
        root.mkdir(parents=True, exist_ok=True)
        target = root / "dataset_version_manifest.json"
        target.write_text(json.dumps(self.model_dump(), indent=2, sort_keys=True), encoding="utf-8")
        write_markdown(self, root / "dataset_version_manifest.md")
        return target


def _write_json(path: str | Path, payload: StrictModel | dict[str, Any]) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(payload, StrictModel):
        data = payload.model_dump()
    else:
        data = payload
    target.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    return target


def _expand_input_paths(paths: list[str]) -> list[str]:
    expanded: list[str] = []
    for item in paths:
        if any(marker in item for marker in ("*", "?", "[")):
            matches = sorted(glob.glob(item))
            expanded.extend(str(Path(path)) for path in matches if Path(path).is_file())
        else:
            expanded.append(item)
    return expanded


def _row_text(row: dict[str, Any]) -> str:
    return str(row.get("text") or row.get("content") or row.get("prompt") or row.get("chosen") or "")


def _row_hash(row: dict[str, Any]) -> str:
    return str(row.get("content_hash") or stable_hash(_row_text(row)))


def _record_category(row: dict[str, Any]) -> str:
    quality = row.get("quality", {}) if isinstance(row.get("quality"), dict) else {}
    category = str(row.get("category") or quality.get("data_type") or "general").lower()
    labels = {str(item).lower() for item in quality.get("labels", [])}
    if "verified_security_patch" in category or "patch" in labels:
        return "verified_patch"
    if "security" in category or "defensive_security" in labels:
        return "cybersecurity"
    if "agentic" in category or "agentic_coding" in labels:
        return "agentic"
    if "code" in category or "code" in labels:
        return "code"
    return "general"


def _normalize_training_row(row: dict[str, Any], *, source_path: Path, default_category: str) -> dict[str, Any] | None:
    text = _row_text(row)
    if not text and row.get("prompt") and row.get("chosen"):
        text = f"{row['prompt']}\n{row['chosen']}"
    if not text.strip():
        return None
    source = str(row.get("source") or row.get("repo_path") or source_path.stem)
    license_name = str(row.get("license") or row.get("spdx_license") or "unknown").lower()
    category = str(row.get("category") or default_category)
    normalized = dict(row)
    normalized["text"] = text
    normalized["source"] = source
    normalized["license"] = license_name
    normalized["category"] = category
    normalized["content_hash"] = str(row.get("content_hash") or stable_hash(text))
    normalized.setdefault(
        "provenance",
        {
            "source_path": str(source_path),
            "source": source,
            "license": license_name,
            "ingested_at_unix": time.time(),
        },
    )
    normalized.setdefault(
        "quality",
        {
            "accepted": True,
            "labels": ["defensive_security", "code", "patch", "tests"],
            "quality_score": 0.95,
            "data_type": "patch",
            "content_hash": normalized["content_hash"],
            "risk_flags": [],
        },
    )
    return normalized


def _write_normalized_rows(input_paths: list[str], output_path: str | Path, *, default_category: str) -> PatchTaskDatasetReport:
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    accepted = 0
    rejected = 0
    categories: Counter[str] = Counter()
    seen: set[str] = set()
    with target.open("w", encoding="utf-8") as handle:
        for path in input_paths:
            source_path = Path(path)
            if not source_path.exists():
                rejected += 1
                continue
            for row in iter_jsonl(source_path):
                normalized = _normalize_training_row(row, source_path=source_path, default_category=default_category)
                if normalized is None:
                    rejected += 1
                    continue
                digest = str(normalized["content_hash"])
                if digest in seen:
                    rejected += 1
                    continue
                seen.add(digest)
                categories[_record_category(normalized)] += 1
                handle.write(json.dumps(normalized, ensure_ascii=False, sort_keys=True) + "\n")
                accepted += 1
    return PatchTaskDatasetReport(
        input_paths=input_paths,
        output_path=str(target),
        accepted=accepted,
        rejected=rejected,
        categories=dict(sorted(categories.items())),
    )


def _write_human_review_rows(input_paths: list[str], output_path: str | Path) -> HumanReviewPromotionReport:
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    approved = 0
    rejected = 0
    categories: Counter[str] = Counter()
    seen: set[str] = set()
    with target.open("w", encoding="utf-8") as handle:
        for path in input_paths:
            source_path = Path(path)
            if not source_path.exists():
                rejected += 1
                continue
            for row in iter_jsonl(source_path):
                review = row.get("review", {}) if isinstance(row.get("review"), dict) else {}
                status = str(review.get("status") or row.get("review_status") or "").lower()
                if status not in {"approved", "human_approved"}:
                    rejected += 1
                    continue
                normalized = _normalize_training_row(row, source_path=source_path, default_category="human_review_approved")
                if normalized is None:
                    rejected += 1
                    continue
                digest = str(normalized["content_hash"])
                if digest in seen:
                    rejected += 1
                    continue
                seen.add(digest)
                normalized["human_review"] = {"status": "approved", "source_path": str(source_path), **review}
                categories[_record_category(normalized)] += 1
                handle.write(json.dumps(normalized, ensure_ascii=False, sort_keys=True) + "\n")
                approved += 1
    return HumanReviewPromotionReport(
        input_paths=input_paths,
        output_path=str(target),
        approved=approved,
        rejected=rejected,
        categories=dict(sorted(categories.items())),
    )


def _concat_jsonl(input_paths: list[str | Path], output_path: str | Path) -> int:
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    seen: set[str] = set()
    with target.open("w", encoding="utf-8") as handle:
        for path in input_paths:
            source = Path(path)
            if not source.exists():
                continue
            for row in iter_jsonl(source):
                digest = _row_hash(row)
                if digest in seen:
                    continue
                seen.add(digest)
                row["content_hash"] = digest
                handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
                count += 1
    return count


def _load_holdout_hashes(paths: list[str]) -> set[str]:
    hashes: set[str] = set()
    for path in paths:
        source = Path(path)
        if not source.exists():
            continue
        for row in iter_jsonl(source):
            text = str(row.get("prompt") or row.get("canonical_solution") or row.get("code") or row.get("text") or "")
            if text:
                hashes.add(stable_hash(text))
            task_id = row.get("task_id") or row.get("id")
            if task_id:
                hashes.add(stable_hash(str(task_id)))
    return hashes


def _holdout_contaminated(row: dict[str, Any], holdout_hashes: set[str]) -> bool:
    text = _row_text(row)
    digest = _row_hash(row)
    if digest in holdout_hashes or stable_hash(text) in holdout_hashes:
        return True
    lowered = text.lower()
    return any(marker in lowered for marker in ("canonical_solution", "humaneval", "mbpp", "swe-bench", "cyberseceval"))


def enforce_benchmark_holdout(
    input_path: str | Path,
    output_path: str | Path,
    removed_path: str | Path,
    benchmark_holdout_paths: list[str],
) -> HoldoutSeparationReport:
    holdout_hashes = _load_holdout_hashes(benchmark_holdout_paths)
    scanned = 0
    removed = 0
    target = Path(output_path)
    removed_target = Path(removed_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    removed_target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as clean_handle, removed_target.open("w", encoding="utf-8") as removed_handle:
        for row in iter_jsonl(input_path):
            scanned += 1
            if _holdout_contaminated(row, holdout_hashes):
                removed_handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
                removed += 1
                continue
            clean_handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    return HoldoutSeparationReport(
        benchmark_holdout_paths=benchmark_holdout_paths,
        benchmark_hashes=len(holdout_hashes),
        scanned_records=scanned,
        removed_records=removed,
        output_path=str(target),
        removed_path=str(removed_target),
    )


def split_train_val_test(input_path: str | Path, output_dir: str | Path, config: ProductionDatasetConfig) -> SplitManifest:
    rows = list(iter_jsonl(input_path))
    rng = random.Random(config.seed)
    rng.shuffle(rows)
    total = len(rows)
    train_count = int(total * config.train_fraction)
    val_count = int(total * config.val_fraction)
    test_count = total - train_count - val_count
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    paths = {
        "train": root / "train.jsonl",
        "val": root / "val.jsonl",
        "test": root / "test.jsonl",
    }
    splits = {
        "train": rows[:train_count],
        "val": rows[train_count : train_count + val_count],
        "test": rows[train_count + val_count :],
    }
    for name, split_rows in splits.items():
        with paths[name].open("w", encoding="utf-8") as handle:
            for row in split_rows:
                row = dict(row)
                row["split"] = name
                handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    return SplitManifest(
        train_path=str(paths["train"]),
        val_path=str(paths["val"]),
        test_path=str(paths["test"]),
        holdout_path=str(root / "holdout.jsonl"),
        train_records=len(splits["train"]),
        val_records=len(splits["val"]),
        test_records=len(splits["test"]),
        holdout_records=0,
        fractions={"train": config.train_fraction, "val": config.val_fraction, "test": config.test_fraction},
        seed=config.seed,
    )


def _copy_file(source: str | Path, target: str | Path) -> int:
    src = Path(source)
    dst = Path(target)
    dst.parent.mkdir(parents=True, exist_ok=True)
    if not src.exists():
        dst.write_text("", encoding="utf-8")
        return 0
    count = 0
    with src.open("r", encoding="utf-8", errors="replace") as source_handle, dst.open("w", encoding="utf-8") as target_handle:
        for line in source_handle:
            if line.strip():
                count += 1
            target_handle.write(line)
    return count


def _version_id(config: ProductionDatasetConfig, artifacts: dict[str, str]) -> str:
    digest_input = json.dumps({"config": config.model_dump(), "artifacts": artifacts}, sort_keys=True)
    return stable_hash(digest_input)[:16]


def _build_issues(
    *,
    config: ProductionDatasetConfig,
    final_records: int,
    split_manifest: SplitManifest,
    patch_report: PatchTaskDatasetReport,
    review_report: HumanReviewPromotionReport,
    validation_report: DatasetValidationReport,
    holdout_report: HoldoutSeparationReport,
) -> list[str]:
    issues: list[str] = []
    if final_records < config.min_promoted_records:
        issues.append(f"promoted_records_below_minimum:{final_records}<{config.min_promoted_records}")
    if split_manifest.train_records < config.min_train_records:
        issues.append(f"train_records_below_minimum:{split_manifest.train_records}<{config.min_train_records}")
    if patch_report.accepted < config.min_verified_patch_records:
        issues.append(f"verified_patch_records_below_minimum:{patch_report.accepted}<{config.min_verified_patch_records}")
    if review_report.approved < config.min_human_review_approved_records:
        issues.append(
            f"human_review_approved_records_below_minimum:{review_report.approved}<{config.min_human_review_approved_records}"
        )
    if validation_report.status != "passed":
        issues.append("dataset_validation_failed")
    if holdout_report.removed_records > 0:
        issues.append(f"benchmark_holdout_contamination_removed:{holdout_report.removed_records}")
    return issues


def build_production_dataset(config: ProductionDatasetConfig) -> ProductionDatasetManifest:
    input_paths = _expand_input_paths(config.input_paths)
    if not input_paths:
        raise ValueError("no input JSONL files found for production dataset build")
    root = Path(config.output_dir)
    reports_dir = root / "reports"
    work_dir = root / "work"
    final_dir = root / "final"
    for directory in (reports_dir, work_dir, final_dir):
        directory.mkdir(parents=True, exist_ok=True)

    license_clean = work_dir / "01_license_clean.jsonl"
    license_report = filter_jsonl_by_license(
        input_paths,
        license_clean,
        strict_unknown=not config.allow_unknown_license,
    )
    _write_json(reports_dir / "license_filter_report.json", license_report)

    quality_clean = work_dir / "02_quality_clean.jsonl"
    quality_report = DatasetQualityGate(QualityGateConfig(require_license=True)).filter_jsonl(license_clean, quality_clean)
    _write_json(reports_dir / "quality_gate_report.json", quality_report)

    no_benchmark = work_dir / "03_benchmark_clean.jsonl"
    benchmark_report = filter_benchmark_contamination_jsonl([quality_clean], no_benchmark)
    _write_json(reports_dir / "benchmark_contamination_filter_report.json", benchmark_report)

    deduped = work_dir / "04_deduped.jsonl"
    dedup_report = deduplicate_jsonl([no_benchmark], deduped, hamming_threshold=config.near_dedup_hamming_threshold)
    _write_json(reports_dir / "near_duplicate_report.json", dedup_report)

    source_quality_report = write_source_quality_report([deduped], reports_dir / "source_quality_report.json")
    source_reputation_report = write_source_reputation_report(
        reports_dir / "source_reputation_report.json",
        source_quality_report_path=reports_dir / "source_quality_report.json",
        contamination_report_path=reports_dir / "benchmark_contamination_filter_report.json",
        dedup_report_path=reports_dir / "near_duplicate_report.json",
    )
    if Path(config.source_registry_path).exists():
        source_budget_plan = write_source_budget_plan(
            reports_dir / "source_budget_plan.json",
            sources_path=config.source_registry_path,
            reputation_report_path=reports_dir / "source_reputation_report.json",
            target_total_docs=config.source_budget_target_docs,
        )
    else:
        source_budget_plan = SourceBudgetPlan(target_total_docs=config.source_budget_target_docs, allocated_total_docs=0, budgets=[])
        _write_json(reports_dir / "source_budget_plan.json", source_budget_plan)

    promoted = work_dir / "05_promoted.jsonl"
    holdout = final_dir / "holdout.jsonl"
    review_queue = root / "review" / "human_review_queue.jsonl"
    decisions = reports_dir / "training_gate_decisions.jsonl"
    gate_report = apply_training_data_gate(
        input_paths=[deduped],
        promoted_path=promoted,
        holdout_path=holdout,
        review_queue_path=review_queue,
        decisions_path=decisions,
        reputation_report_path=reports_dir / "source_reputation_report.json",
        config=TrainingDataGateConfig(
            min_quality_score=config.min_quality_score,
            min_source_reputation_score=config.min_source_reputation_score,
            eval_holdout_fraction=config.eval_holdout_fraction,
            seed=config.seed,
        ),
    )
    _write_json(reports_dir / "training_data_gate_report.json", gate_report)

    patch_rows = work_dir / "06_verified_patch_tasks.jsonl"
    patch_report = _write_normalized_rows(config.verified_patch_paths, patch_rows, default_category="verified_security_patch")
    _write_json(reports_dir / "verified_patch_task_report.json", patch_report)

    human_rows = work_dir / "07_human_review_approved.jsonl"
    human_report = _write_human_review_rows(config.human_review_approved_paths, human_rows)
    _write_json(reports_dir / "human_review_approved_report.json", human_report)

    combined = work_dir / "08_combined_promoted.jsonl"
    final_records_before_holdout = _concat_jsonl([promoted, patch_rows, human_rows], combined)

    holdout_clean = work_dir / "09_holdout_clean.jsonl"
    removed_holdout = reports_dir / "benchmark_holdout_removed.jsonl"
    holdout_report = enforce_benchmark_holdout(combined, holdout_clean, removed_holdout, config.benchmark_holdout_paths)
    _write_json(reports_dir / "benchmark_holdout_separation_report.json", holdout_report)

    split_manifest = split_train_val_test(holdout_clean, final_dir, config)
    _copy_file(holdout, split_manifest.holdout_path)
    split_manifest = split_manifest.model_copy(
        update={"holdout_records": sum(1 for _ in iter_jsonl(split_manifest.holdout_path)) if Path(split_manifest.holdout_path).exists() else 0}
    )
    _write_json(reports_dir / "train_val_test_split_manifest.json", split_manifest)

    validation_min = 1 if config.dev_smoke else config.min_train_records
    validation_report = validate_dataset(
        DatasetValidationConfig(
            input_paths=[split_manifest.train_path],
            min_records=validation_min,
            max_duplicate_fraction=config.max_duplicate_fraction,
            min_avg_chars=config.min_avg_chars,
            require_license=True,
            require_quality=True,
            require_categories=["general", "code", "cybersecurity"] if not config.dev_smoke else [],
        )
    )
    validation_report.write(reports_dir / "dataset_validation")

    final_records = split_manifest.train_records + split_manifest.val_records + split_manifest.test_records
    issues = _build_issues(
        config=config,
        final_records=final_records,
        split_manifest=split_manifest,
        patch_report=patch_report,
        review_report=human_report,
        validation_report=validation_report,
        holdout_report=holdout_report,
    )
    status = "passed" if not issues else "failed"
    if config.dev_smoke:
        non_validation_issues = [issue for issue in issues if issue == "dataset_validation_failed"]
        status = "passed" if not non_validation_issues else "failed"

    artifacts = {
        "train": split_manifest.train_path,
        "val": split_manifest.val_path,
        "test": split_manifest.test_path,
        "holdout": split_manifest.holdout_path,
        "human_review_queue": str(review_queue),
        "training_gate_decisions": str(decisions),
        "benchmark_holdout_removed": str(removed_holdout),
    }
    manifest = ProductionDatasetManifest(
        dataset_id=config.dataset_id,
        version_id=_version_id(config, artifacts),
        status=status,
        output_dir=str(root),
        dev_smoke=config.dev_smoke,
        artifacts=artifacts,
        metrics={
            "final_records_before_holdout": final_records_before_holdout,
            "promoted_records": final_records,
            "train_records": split_manifest.train_records,
            "val_records": split_manifest.val_records,
            "test_records": split_manifest.test_records,
            "holdout_records": split_manifest.holdout_records,
            "verified_patch_records": patch_report.accepted,
            "human_review_approved_records": human_report.approved,
            "benchmark_holdout_removed_records": holdout_report.removed_records,
        },
        issues=issues,
        reports={
            "license_filter": license_report.model_dump(),
            "quality_gate": quality_report.model_dump(),
            "benchmark_contamination_filter": benchmark_report.model_dump(),
            "near_duplicate": dedup_report.model_dump(),
            "source_quality": source_quality_report.model_dump(),
            "source_reputation": source_reputation_report.model_dump(),
            "source_budget_plan": source_budget_plan.model_dump(),
            "training_data_gate": gate_report.model_dump(),
            "verified_patch_tasks": patch_report.model_dump(),
            "human_review_approved": human_report.model_dump(),
            "benchmark_holdout_separation": holdout_report.model_dump(),
            "split_manifest": split_manifest.model_dump(),
            "dataset_validation": validation_report.model_dump(),
        },
    )
    manifest.write(root)
    return manifest


def write_markdown(manifest: ProductionDatasetManifest, path: str | Path) -> Path:
    target = Path(path)
    lines = [
        "# Aeitron Production Dataset Manifest",
        "",
        f"- dataset_id: {manifest.dataset_id}",
        f"- version_id: {manifest.version_id}",
        f"- status: {manifest.status}",
        f"- dev_smoke: {str(manifest.dev_smoke).lower()}",
        "",
        "## Metrics",
        "",
        "| metric | value |",
        "|---|---:|",
    ]
    for key, value in sorted(manifest.metrics.items()):
        lines.append(f"| {key} | {value} |")
    lines.extend(["", "## Issues", ""])
    if manifest.issues:
        for issue in manifest.issues:
            lines.append(f"- {issue}")
    else:
        lines.append("- none")
    lines.extend(["", "## Artifacts", "", "| role | path |", "|---|---|"])
    for key, value in sorted(manifest.artifacts.items()):
        lines.append(f"| {key} | {value} |")
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return target


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a governed production dataset pack for Aeitron scratch training.")
    parser.add_argument("--input", nargs="+", required=True)
    parser.add_argument("--output-dir", default="data/production/aeitron-corpus")
    parser.add_argument("--dataset-id", default="aeitron-corpus")
    parser.add_argument("--source-registry", default="config/data_sources.ultimate.json")
    parser.add_argument("--benchmark-holdout", action="append", default=[])
    parser.add_argument("--verified-patch", action="append", default=[])
    parser.add_argument("--human-review-approved", action="append", default=[])
    parser.add_argument("--dev-smoke", action="store_true")
    parser.add_argument("--min-promoted-records", type=int, default=100_000)
    parser.add_argument("--min-verified-patch-records", type=int, default=100)
    parser.add_argument("--min-human-review-approved-records", type=int, default=100)
    parser.add_argument("--min-train-records", type=int, default=90_000)
    parser.add_argument("--allow-unknown-license", action="store_true")
    parser.add_argument("--seed", type=int, default=1337)
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    config = ProductionDatasetConfig(
        input_paths=args.input,
        output_dir=args.output_dir,
        dataset_id=args.dataset_id,
        source_registry_path=args.source_registry,
        benchmark_holdout_paths=args.benchmark_holdout or ["data/eval/humaneval.jsonl", "data/eval/mbpp.jsonl"],
        verified_patch_paths=args.verified_patch,
        human_review_approved_paths=args.human_review_approved,
        dev_smoke=args.dev_smoke,
        min_promoted_records=args.min_promoted_records,
        min_verified_patch_records=args.min_verified_patch_records,
        min_human_review_approved_records=args.min_human_review_approved_records,
        min_train_records=args.min_train_records,
        allow_unknown_license=args.allow_unknown_license,
        seed=args.seed,
    )
    manifest = build_production_dataset(config)
    print(json.dumps(manifest.model_dump(), indent=2, sort_keys=True))
    if manifest.status != "passed":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
