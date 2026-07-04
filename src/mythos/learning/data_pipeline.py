"""End-to-end Mythos data pipeline: crawl -> clean -> shard -> train."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

import httpx
from pydantic import Field

from src.mythos.learning.data_engine import DataEngine, DataEngineConfig, FrontierStore, PostgresFrontierStore
from src.mythos.learning.source_registry import SourceRegistry, SourceRegistryReport
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


class DataPipelineReport(StrictModel):
    status: str
    work_dir: str
    source_registry: SourceRegistryReport
    crawl: dict[str, Any]
    clean_files: list[str]
    tokenizer_path: str
    shard_manifest: dict[str, Any]
    training: dict[str, Any] | None = None


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
    root.mkdir(parents=True, exist_ok=True)

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

    trained_tokenizer = train_bpe_tokenizer(
        clean_files,
        tokenizer_path,
        TokenizerTrainConfig(vocab_size=config.vocab_size, min_frequency=config.tokenizer_min_frequency),
    )
    manifest: ShardManifest = build_token_shards(
        input_paths=clean_files,
        tokenizer_path=trained_tokenizer,
        output_dir=shards_dir,
        config=ShardBuildConfig(
            shard_token_count=config.shard_token_count,
            sequence_length=config.sequence_length,
            validation_fraction=config.validation_fraction,
        ),
        dataset_id="mythos-defensive-coding-corpus",
    )
    training_report = None
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
            validate_every=0,
            resume=True,
        )

    return DataPipelineReport(
        status="complete",
        work_dir=str(root),
        source_registry=registry_report,
        crawl=crawl_report.model_dump(),
        clean_files=clean_files,
        tokenizer_path=str(trained_tokenizer),
        shard_manifest=manifest.model_dump(),
        training=training_report,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Mythos crawl -> clean -> shard -> train pipeline.")
    parser.add_argument("--sources", required=True)
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
    return parser.parse_args()


def config_from_args(args: argparse.Namespace) -> DataPipelineConfig:
    return DataPipelineConfig(
        sources_path=args.sources,
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
    )


def main() -> None:
    report = asyncio.run(run_data_pipeline(config_from_args(parse_args())))
    print(json.dumps(report.model_dump(), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
