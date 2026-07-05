"""End-to-end Mythos data pipeline: crawl -> clean -> shard -> train."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import time
from typing import Any

import httpx
from pydantic import Field

from src.mythos.evaluation.checkpoint_eval import CheckpointEvalReport, evaluate_checkpoint
from src.mythos.learning.contamination import ContaminationDetector, load_patterns
from src.mythos.learning.data_engine import DataEngine, DataEngineConfig, FrontierStore, PostgresFrontierStore
from src.mythos.learning.dashboard import write_dashboard
from src.mythos.learning.feedback import BenchmarkFeedbackReport, write_feedback_report
from src.mythos.learning.quality_inspector import QualityInspectionReport, write_quality_report
from src.mythos.learning.review import ReviewReport, review_tasks
from src.mythos.learning.source_balancing import SourceBalanceReport, balance_clean_jsonl
from src.mythos.learning.source_registry import SourceRegistry, SourceRegistryReport
from src.mythos.learning.source_quality import SourceQualityReport, write_source_quality_report
from src.mythos.learning.storage import ObjectStoreConfig, create_object_store, upload_paths
from src.mythos.learning.task_extraction import TaskExtractionReport, extract_tasks
from src.mythos.learning.versioning import DatasetArtifact, DatasetLedger, DatasetVersionManifest, artifact_from_path, build_version_id
from src.mythos.model_ops.pretrain_loop import run_pretraining_loop
from src.mythos.model_ops.tokenizer_pipeline import (
    ShardBuildConfig,
    ShardManifest,
    TokenizerTrainConfig,
    build_token_shards,
    train_bpe_tokenizer,
)
from src.mythos.shared.schemas import StrictModel


class DataPipelineConfig(StrictModel):
    sources_path: str
    dataset_id: str = "mythos-defensive-coding-corpus"
    work_dir: str = "artifacts/mythos/data-pipeline"
    frontier_backend: str = "sqlite"
    postgres_dsn: str | None = None
    max_docs: int = Field(default=10_000, ge=1)
    workers: int = Field(default=8, ge=1, le=256)
    max_depth: int = Field(default=2, ge=0, le=20)
    delay_seconds: float = Field(default=1.0, ge=0.0)
    shard_rows: int = Field(default=10_000, ge=1)
    respect_robots: bool = True
    vocab_size: int = Field(default=64_000, ge=1_000)
    tokenizer_min_frequency: int = Field(default=2, ge=1)
    shard_token_count: int = Field(default=1_000_000, ge=128)
    sequence_length: int = Field(default=2048, ge=16)
    validation_fraction: float = Field(default=0.01, ge=0.0, le=0.5)
    skip_train: bool = False
    train_steps: int = Field(default=100, ge=1)
    train_device: str = "auto"
    train_batch_size: int = Field(default=2, ge=1)
    gradient_accumulation_steps: int = Field(default=1, ge=1)
    dtype: str = "bf16"
    validate_every: int = Field(default=25, ge=0)
    validation_batches: int = Field(default=4, ge=1)
    run_checkpoint_eval: bool = True
    early_stopping_patience: int = Field(default=0, ge=0)
    early_stopping_min_delta: float = Field(default=0.0, ge=0.0)
    balance_sources: bool = True
    max_source_fraction: float = Field(default=0.35, ge=0.05, le=1.0)
    min_source_rows: int = Field(default=25, ge=1)
    contamination_patterns_path: str | None = None
    block_contamination: bool = True
    extract_tasks: bool = True
    review_tasks: bool = True
    max_extracted_tasks: int = Field(default=50_000, ge=1)
    object_store_uri: str = "local://artifacts/mythos/object-store"
    object_store_endpoint_url: str | None = None
    upload_artifacts: bool = True


class DataPipelineReport(StrictModel):
    status: str
    dataset_id: str
    version_id: str
    work_dir: str
    source_registry: SourceRegistryReport
    crawl: dict[str, Any]
    contamination_report: dict[str, Any] | None
    quality_report: dict[str, Any] | None
    source_quality_report: dict[str, Any] | None
    task_report: dict[str, Any] | None
    review_report: dict[str, Any] | None
    feedback_report: dict[str, Any] | None
    source_balance_report: dict[str, Any] | None = None
    clean_files: list[str]
    training_files: list[str]
    tokenizer_path: str
    shard_manifest: dict[str, Any]
    version_manifest_path: str
    dashboard_path: str
    uploaded_objects: list[dict[str, Any]]
    training: dict[str, Any] | None = None
    checkpoint_eval: dict[str, Any] | None = None


class PipelineRunLock:
    def __init__(self, path: str | Path, *, stale_after_seconds: int = 24 * 60 * 60) -> None:
        self.path = Path(path)
        self.stale_after_seconds = stale_after_seconds
        self.fd: int | None = None

    def __enter__(self) -> "PipelineRunLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        now = time.time()
        if self.path.exists() and now - self.path.stat().st_mtime > self.stale_after_seconds:
            self.path.unlink()
        try:
            self.fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as exc:
            details = self.path.read_text(encoding="utf-8", errors="replace") if self.path.exists() else ""
            raise RuntimeError(
                f"data pipeline work_dir is already locked: {self.path}. "
                "Use a fresh --output-dir or wait for the running job to finish. "
                f"lock_details={details!r}"
            ) from exc
        payload = json.dumps({"pid": os.getpid(), "created_at_unix": now}, sort_keys=True) + "\n"
        os.write(self.fd, payload.encode("utf-8"))
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass


async def _build_store(config: DataPipelineConfig) -> Any:
    if config.frontier_backend == "postgres":
        if not config.postgres_dsn:
            raise ValueError("postgres_dsn is required when frontier_backend='postgres'")
        return await PostgresFrontierStore.create(config.postgres_dsn)
    if config.frontier_backend != "sqlite":
        raise ValueError("frontier_backend must be 'sqlite' or 'postgres'")
    return FrontierStore(Path(config.work_dir) / "frontier.sqlite3")


async def run_data_pipeline(config: DataPipelineConfig, *, client: httpx.AsyncClient | None = None) -> DataPipelineReport:
    root = Path(config.work_dir)
    raw_dir = root / "raw"
    clean_dir = root / "clean"
    tokenizer_path = root / "tokenizer" / "tokenizer.json"
    shards_dir = root / "shards"
    train_dir = root / "train"
    tasks_path = root / "tasks" / "tasks.jsonl"
    reports_dir = root / "reports"
    root.mkdir(parents=True, exist_ok=True)

    with PipelineRunLock(root / ".pipeline.lock"):
        return await _run_data_pipeline_locked(
            config,
            client=client,
            root=root,
            raw_dir=raw_dir,
            clean_dir=clean_dir,
            tokenizer_path=tokenizer_path,
            shards_dir=shards_dir,
            train_dir=train_dir,
            tasks_path=tasks_path,
            reports_dir=reports_dir,
        )


async def _run_data_pipeline_locked(
    config: DataPipelineConfig,
    *,
    client: httpx.AsyncClient | None,
    root: Path,
    raw_dir: Path,
    clean_dir: Path,
    tokenizer_path: Path,
    shards_dir: Path,
    train_dir: Path,
    tasks_path: Path,
    reports_dir: Path,
) -> DataPipelineReport:
    registry = SourceRegistry.from_file(config.sources_path)
    registry_report = registry.validate()
    store = await _build_store(config)
    engine_config = DataEngineConfig(
        frontier_backend=config.frontier_backend,
        postgres_dsn=config.postgres_dsn,
        frontier_path=str(root / "frontier.sqlite3"),
        output_dir=str(raw_dir),
        clean_output_dir=str(clean_dir),
        max_docs=config.max_docs,
        max_depth=config.max_depth,
        workers=config.workers,
        shard_rows=config.shard_rows,
        respect_robots=config.respect_robots,
        delay_seconds=config.delay_seconds,
    )
    engine = DataEngine(engine_config, store=store, owns_store=True)
    try:
        crawl_report = await engine.run(registry.to_sources(), client=client)
    finally:
        await engine.aclose()

    clean_files = sorted(str(path) for path in clean_dir.glob("clean-*.jsonl"))
    if not clean_files:
        raise RuntimeError("crawler produced zero clean shards; check sources, quality gate, robots policy, and allowlist")

    contamination_report = ContaminationDetector(load_patterns(config.contamination_patterns_path)).scan_jsonl(
        clean_files,
        block_on_hit=config.block_contamination,
    )
    contamination_path = reports_dir / "contamination_report.json"
    contamination_path.parent.mkdir(parents=True, exist_ok=True)
    contamination_path.write_text(json.dumps(contamination_report.model_dump(), indent=2, sort_keys=True), encoding="utf-8")
    if contamination_report.blocked:
        raise RuntimeError(f"contamination detector blocked dataset: {len(contamination_report.hits)} hits")

    quality_report_path = reports_dir / "quality_report.json"
    quality_report: QualityInspectionReport = write_quality_report(clean_files, quality_report_path)
    source_quality_path = reports_dir / "source_quality_report.json"
    source_quality_report: SourceQualityReport = write_source_quality_report(clean_files, source_quality_path)

    task_report: TaskExtractionReport | None = None
    review_report: ReviewReport | None = None
    approved_tasks_path = root / "tasks" / "approved_tasks.jsonl"
    if config.extract_tasks:
        task_report = extract_tasks(clean_files, tasks_path, max_tasks=config.max_extracted_tasks)
        if config.review_tasks:
            review_report = review_tasks(tasks_path, reports_dir / "task_review_decisions.jsonl", approved_tasks_path)
            (reports_dir / "task_review_report.json").write_text(json.dumps(review_report.model_dump(), indent=2, sort_keys=True), encoding="utf-8")
    feedback_path = reports_dir / "feedback_report.json"
    review_report_path = reports_dir / "task_review_report.json" if review_report is not None else None
    feedback_report: BenchmarkFeedbackReport = write_feedback_report(
        output_path=feedback_path,
        quality_report_path=quality_report_path,
        review_report_path=review_report_path,
    )
    source_balance_report: SourceBalanceReport | None = None
    training_files = clean_files
    balanced_clean_path = root / "balanced" / "balanced-clean-000000.jsonl"
    if config.balance_sources:
        source_balance_report = balance_clean_jsonl(
            input_paths=clean_files,
            output_path=balanced_clean_path,
            max_source_fraction=config.max_source_fraction,
            min_source_rows=config.min_source_rows,
        )
        (reports_dir / "source_balance_report.json").write_text(
            json.dumps(source_balance_report.model_dump(), indent=2, sort_keys=True),
            encoding="utf-8",
        )
        training_files = [str(balanced_clean_path)]

    trained_tokenizer = train_bpe_tokenizer(
        training_files,
        tokenizer_path,
        TokenizerTrainConfig(vocab_size=config.vocab_size, min_frequency=config.tokenizer_min_frequency),
    )
    manifest: ShardManifest = build_token_shards(
        input_paths=training_files,
        tokenizer_path=trained_tokenizer,
        output_dir=shards_dir,
        config=ShardBuildConfig(
            shard_token_count=config.shard_token_count,
            sequence_length=config.sequence_length,
            validation_fraction=config.validation_fraction,
        ),
        dataset_id=config.dataset_id,
    )
    training_report = None
    checkpoint_eval_report: CheckpointEvalReport | None = None
    if not config.skip_train:
        training_report = run_pretraining_loop(
            output_dir=train_dir,
            manifest=shards_dir / "manifest.json",
            device=config.train_device,
            steps=config.train_steps,
            batch_size=config.train_batch_size,
            sequence_length=config.sequence_length,
            gradient_accumulation_steps=config.gradient_accumulation_steps,
            dtype=config.dtype,
            checkpoint_every=max(1, config.train_steps),
            validate_every=config.validate_every,
            validation_batches=config.validation_batches,
            early_stopping_patience=config.early_stopping_patience,
            early_stopping_min_delta=config.early_stopping_min_delta,
            resume=True,
        )
        if config.run_checkpoint_eval:
            checkpoint_eval_report = evaluate_checkpoint(
                checkpoint_manifest_path=training_report.get("best_checkpoint_manifest") or training_report["checkpoint_manifest"],
                training_report=training_report,
                output_dir=reports_dir / "checkpoint_eval",
            )

    artifacts: list[DatasetArtifact] = []
    for path in clean_files:
        artifacts.append(artifact_from_path(path, role="clean_jsonl"))
    artifacts.append(artifact_from_path(trained_tokenizer, role="tokenizer"))
    artifacts.append(artifact_from_path(shards_dir / "manifest.json", role="shard_manifest"))
    artifacts.append(artifact_from_path(contamination_path, role="contamination_report"))
    artifacts.append(artifact_from_path(quality_report_path, role="quality_report"))
    artifacts.append(artifact_from_path(source_quality_path, role="source_quality_report"))
    artifacts.append(artifact_from_path(feedback_path, role="feedback_report"))
    if source_balance_report is not None:
        artifacts.append(artifact_from_path(reports_dir / "source_balance_report.json", role="source_balance_report"))
        artifacts.append(artifact_from_path(balanced_clean_path, role="balanced_clean_jsonl"))
    checkpoint_eval_path = reports_dir / "checkpoint_eval" / "checkpoint_eval_report.json"
    if checkpoint_eval_report is not None:
        artifacts.append(artifact_from_path(checkpoint_eval_path, role="checkpoint_eval_report"))
    if task_report is not None:
        artifacts.append(artifact_from_path(tasks_path, role="extracted_tasks"))
    if review_report is not None:
        artifacts.append(artifact_from_path(reports_dir / "task_review_report.json", role="task_review_report"))
        artifacts.append(artifact_from_path(approved_tasks_path, role="approved_tasks"))
    for shard_path in manifest.train_shards + manifest.val_shards:
        artifacts.append(artifact_from_path(shard_path, role="token_shard"))
    version_id = build_version_id(config.dataset_id, [artifact.sha256 for artifact in artifacts])
    uploaded_objects = []
    store = None
    if config.upload_artifacts:
        store = create_object_store(ObjectStoreConfig(uri=config.object_store_uri, endpoint_url=config.object_store_endpoint_url))
        upload_prefix = f"{config.dataset_id}/{version_id}"
        upload_candidates = clean_files + [
            str(trained_tokenizer),
            str(shards_dir / "manifest.json"),
            str(contamination_path),
            str(quality_report_path),
            str(source_quality_path),
            str(feedback_path),
        ]
        if task_report is not None:
            upload_candidates.append(str(tasks_path))
        if review_report is not None:
            upload_candidates.extend([str(reports_dir / "task_review_report.json"), str(approved_tasks_path)])
        if checkpoint_eval_report is not None:
            upload_candidates.append(str(checkpoint_eval_path))
        if source_balance_report is not None:
            upload_candidates.extend([str(reports_dir / "source_balance_report.json"), str(balanced_clean_path)])
        upload_candidates.extend(manifest.train_shards + manifest.val_shards)
        uploaded_objects = upload_paths(store, upload_candidates, prefix=upload_prefix)
    version_manifest = DatasetVersionManifest(
        dataset_id=config.dataset_id,
        version_id=version_id,
        source_registry=registry_report.model_dump(),
        crawl_report=crawl_report.model_dump(),
        contamination_report=contamination_report.model_dump(),
        quality_report=quality_report.model_dump(),
        source_quality_report=source_quality_report.model_dump(),
        task_report=task_report.model_dump() if task_report else None,
        review_report=review_report.model_dump() if review_report else None,
        feedback_report=feedback_report.model_dump(),
        checkpoint_eval_report=checkpoint_eval_report.model_dump() if checkpoint_eval_report else None,
        source_balance_report=source_balance_report.model_dump() if source_balance_report else None,
        tokenizer_path=str(trained_tokenizer),
        shard_manifest=manifest.model_dump(),
        artifacts=artifacts,
        uploaded_objects=uploaded_objects,
    )
    manifest_path = version_manifest.write(root / "versions" / f"{version_id}.json")
    DatasetLedger(root / "versions" / "ledger.jsonl").append(version_manifest)
    if store is not None:
        uploaded_objects.append(store.put_file(manifest_path, key=f"{config.dataset_id}/{version_id}/version_manifest.json"))

    report_payload = DataPipelineReport(
        status="complete",
        dataset_id=config.dataset_id,
        version_id=version_id,
        work_dir=str(root),
        source_registry=registry_report,
        crawl=crawl_report.model_dump(),
        contamination_report=contamination_report.model_dump(),
        quality_report=quality_report.model_dump(),
        source_quality_report=source_quality_report.model_dump(),
        task_report=task_report.model_dump() if task_report else None,
        review_report=review_report.model_dump() if review_report else None,
        feedback_report=feedback_report.model_dump(),
        source_balance_report=source_balance_report.model_dump() if source_balance_report else None,
        clean_files=clean_files,
        training_files=training_files,
        tokenizer_path=str(trained_tokenizer),
        shard_manifest=manifest.model_dump(),
        version_manifest_path=str(manifest_path),
        dashboard_path=str(root / "dashboard.html"),
        uploaded_objects=[item.model_dump() for item in uploaded_objects],
        training=training_report,
        checkpoint_eval=checkpoint_eval_report.model_dump() if checkpoint_eval_report else None,
    )
    report_path = reports_dir / "pipeline_report.json"
    report_path.write_text(json.dumps(report_payload.model_dump(), indent=2, sort_keys=True), encoding="utf-8")
    write_dashboard(report_payload.model_dump(), root / "dashboard.html")
    return report_payload


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Mythos crawl -> clean -> shard -> train pipeline.")
    parser.add_argument("--sources", required=True)
    parser.add_argument("--dataset-id", default="mythos-defensive-coding-corpus")
    parser.add_argument("--work-dir", default="artifacts/mythos/data-pipeline")
    parser.add_argument("--frontier-backend", choices=["sqlite", "postgres"], default="sqlite")
    parser.add_argument("--postgres-dsn")
    parser.add_argument("--max-docs", type=int, default=10_000)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--max-depth", type=int, default=2)
    parser.add_argument("--delay-seconds", type=float, default=1.0)
    parser.add_argument("--shard-rows", type=int, default=10_000)
    parser.add_argument("--ignore-robots", action="store_true")
    parser.add_argument("--vocab-size", type=int, default=64_000)
    parser.add_argument("--tokenizer-min-frequency", type=int, default=2)
    parser.add_argument("--shard-token-count", type=int, default=1_000_000)
    parser.add_argument("--sequence-length", type=int, default=2048)
    parser.add_argument("--validation-fraction", type=float, default=0.01)
    parser.add_argument("--skip-train", action="store_true")
    parser.add_argument("--train-steps", type=int, default=100)
    parser.add_argument("--train-device", default="auto")
    parser.add_argument("--train-batch-size", type=int, default=2)
    parser.add_argument("--gradient-accumulation-steps", type=int, default=1)
    parser.add_argument("--dtype", default="bf16", choices=["bf16", "fp16", "fp32"])
    parser.add_argument("--validate-every", type=int, default=25)
    parser.add_argument("--validation-batches", type=int, default=4)
    parser.add_argument("--no-checkpoint-eval", action="store_true")
    parser.add_argument("--early-stopping-patience", type=int, default=0)
    parser.add_argument("--early-stopping-min-delta", type=float, default=0.0)
    parser.add_argument("--no-source-balancing", action="store_true")
    parser.add_argument("--max-source-fraction", type=float, default=0.35)
    parser.add_argument("--min-source-rows", type=int, default=25)
    parser.add_argument("--contamination-patterns")
    parser.add_argument("--allow-contamination-hits", action="store_true")
    parser.add_argument("--no-task-extraction", action="store_true")
    parser.add_argument("--no-task-review", action="store_true")
    parser.add_argument("--max-extracted-tasks", type=int, default=50_000)
    parser.add_argument("--object-store-uri", default="local://artifacts/mythos/object-store")
    parser.add_argument("--object-store-endpoint-url")
    parser.add_argument("--no-upload", action="store_true")
    return parser.parse_args()


def config_from_args(args: argparse.Namespace) -> DataPipelineConfig:
    return DataPipelineConfig(
        sources_path=args.sources,
        dataset_id=args.dataset_id,
        work_dir=args.work_dir,
        frontier_backend=args.frontier_backend,
        postgres_dsn=args.postgres_dsn,
        max_docs=args.max_docs,
        workers=args.workers,
        max_depth=args.max_depth,
        delay_seconds=args.delay_seconds,
        shard_rows=args.shard_rows,
        respect_robots=not args.ignore_robots,
        vocab_size=args.vocab_size,
        tokenizer_min_frequency=args.tokenizer_min_frequency,
        shard_token_count=args.shard_token_count,
        sequence_length=args.sequence_length,
        validation_fraction=args.validation_fraction,
        skip_train=args.skip_train,
        train_steps=args.train_steps,
        train_device=args.train_device,
        train_batch_size=args.train_batch_size,
        gradient_accumulation_steps=args.gradient_accumulation_steps,
        dtype=args.dtype,
        validate_every=args.validate_every,
        validation_batches=args.validation_batches,
        run_checkpoint_eval=not args.no_checkpoint_eval,
        early_stopping_patience=args.early_stopping_patience,
        early_stopping_min_delta=args.early_stopping_min_delta,
        balance_sources=not args.no_source_balancing,
        max_source_fraction=args.max_source_fraction,
        min_source_rows=args.min_source_rows,
        contamination_patterns_path=args.contamination_patterns,
        block_contamination=not args.allow_contamination_hits,
        extract_tasks=not args.no_task_extraction,
        review_tasks=not args.no_task_review,
        max_extracted_tasks=args.max_extracted_tasks,
        object_store_uri=args.object_store_uri,
        object_store_endpoint_url=args.object_store_endpoint_url,
        upload_artifacts=not args.no_upload,
    )


def main() -> None:
    report = asyncio.run(run_data_pipeline(config_from_args(parse_args())))
    print(json.dumps(report.model_dump(), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
