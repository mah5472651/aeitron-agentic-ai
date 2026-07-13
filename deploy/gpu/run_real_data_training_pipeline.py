"""Run a real approved-source Mythos data -> GPU training -> eval job.

This entrypoint is intended for Kaggle/Colab smoke runs and single-node GPU
jobs. For production-scale collection, use the same pipeline with Postgres
frontier workers and object storage.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.mythos.learning.data_pipeline import DataPipelineConfig, run_data_pipeline  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run real approved-source crawl, shard, scratch training, and checkpoint eval.")
    parser.add_argument("--sources", default="config/data_sources.ultimate.json")
    parser.add_argument("--dataset-id", default="mythos-real-approved-corpus")
    parser.add_argument("--output-dir", "--work-dir", dest="output_dir", default="artifacts/mythos/real-data-training")
    parser.add_argument("--frontier-backend", choices=["sqlite", "postgres"], default="sqlite")
    parser.add_argument("--postgres-dsn")
    parser.add_argument("--max-docs", type=int, default=10_000)
    parser.add_argument(
        "--max-bytes-per-doc",
        type=int,
        default=300_000,
        help="Kaggle-safe document text cap. Increase on larger machines.",
    )
    parser.add_argument("--min-clean-records", "--target-records", dest="min_clean_records", type=int, default=10_000)
    parser.add_argument("--workers", type=int, default=16)
    parser.add_argument("--max-depth", type=int, default=2)
    parser.add_argument("--delay-seconds", type=float, default=0.5)
    parser.add_argument("--vocab-size", type=int, default=64_000)
    parser.add_argument("--shard-token-count", type=int, default=1_000_000)
    parser.add_argument("--sequence-length", type=int, default=128)
    parser.add_argument("--validation-fraction", type=float, default=0.02)
    parser.add_argument("--train-steps", "--steps", dest="train_steps", type=int, default=1_000)
    parser.add_argument("--train-batch-size", "--batch-size", dest="train_batch_size", type=int, default=4)
    parser.add_argument("--gradient-accumulation-steps", type=int, default=8)
    parser.add_argument("--dtype", choices=["bf16", "fp16", "fp32"], default="fp16")
    parser.add_argument("--device", choices=["auto", "cpu", "cuda"], default="cuda")
    parser.add_argument("--validate-every", "--validation-interval", dest="validate_every", type=int, default=25)
    parser.add_argument("--validation-batches", type=int, default=4)
    parser.add_argument("--early-stopping-patience", type=int, default=8)
    parser.add_argument("--early-stopping-min-delta", type=float, default=0.0)
    parser.add_argument("--no-source-balancing", action="store_true")
    parser.add_argument("--max-source-fraction", type=float, default=0.35)
    parser.add_argument("--min-source-rows", type=int, default=25)
    parser.add_argument("--object-store-uri", default="local://artifacts/mythos/object-store")
    parser.add_argument("--object-store-endpoint-url")
    parser.add_argument("--skip-train", action="store_true")
    parser.add_argument("--progress-path")
    parser.add_argument("--no-progress-stdout", action="store_true")
    parser.add_argument("--progress-every-docs", type=int, default=10)
    parser.add_argument("--progress-every-steps", type=int, default=10)
    return parser.parse_args()


def clean_record_count(report: dict[str, object]) -> int:
    crawl = report.get("crawl")
    if isinstance(crawl, dict):
        return int(crawl.get("accepted", 0))
    return 0


async def run(args: argparse.Namespace) -> dict[str, object]:
    progress_path = args.progress_path or str(Path(args.output_dir) / "progress.jsonl")
    report = await run_data_pipeline(
        DataPipelineConfig(
            sources_path=args.sources,
            dataset_id=args.dataset_id,
            work_dir=args.output_dir,
            frontier_backend=args.frontier_backend,
            postgres_dsn=args.postgres_dsn,
            max_docs=args.max_docs,
            max_bytes_per_doc=args.max_bytes_per_doc,
            workers=args.workers,
            max_depth=args.max_depth,
            delay_seconds=args.delay_seconds,
            shard_rows=10_000,
            vocab_size=args.vocab_size,
            tokenizer_min_frequency=2,
            shard_token_count=args.shard_token_count,
            sequence_length=args.sequence_length,
            validation_fraction=args.validation_fraction,
            skip_train=args.skip_train,
            train_steps=args.train_steps,
            train_device=args.device,
            train_batch_size=args.train_batch_size,
            gradient_accumulation_steps=args.gradient_accumulation_steps,
            dtype=args.dtype,
            validate_every=min(args.validate_every, args.train_steps) if args.validate_every > 0 else 0,
            validation_batches=args.validation_batches,
            early_stopping_patience=args.early_stopping_patience,
            early_stopping_min_delta=args.early_stopping_min_delta,
            balance_sources=not args.no_source_balancing,
            max_source_fraction=args.max_source_fraction,
            min_source_rows=args.min_source_rows,
            run_checkpoint_eval=not args.skip_train,
            object_store_uri=args.object_store_uri,
            object_store_endpoint_url=args.object_store_endpoint_url,
            upload_artifacts=True,
            progress_path=progress_path,
            progress_to_stdout=not args.no_progress_stdout,
            progress_every_docs=args.progress_every_docs,
            progress_every_steps=args.progress_every_steps,
        )
    )
    payload = report.model_dump()
    accepted = clean_record_count(payload)
    if accepted < args.min_clean_records:
        payload["status"] = "blocked"
        payload["block_reason"] = (
            f"accepted clean records {accepted} below required minimum {args.min_clean_records}; "
            "add approved sources, increase --max-docs, or lower --min-clean-records for a smoke run"
        )
        Path(args.output_dir, "reports").mkdir(parents=True, exist_ok=True)
        Path(args.output_dir, "reports", "real_data_training_report.json").write_text(
            json.dumps(payload, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        return payload
    payload["status"] = "complete"
    payload["accepted_clean_records"] = accepted
    Path(args.output_dir, "reports").mkdir(parents=True, exist_ok=True)
    Path(args.output_dir, "reports", "real_data_training_report.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return payload


def main() -> None:
    args = parse_args()
    print(
        json.dumps(
            {
                "event": "mythos_real_data_training_start",
                "work_dir": args.output_dir,
                "progress_path": args.progress_path or str(Path(args.output_dir) / "progress.jsonl"),
                "progress_stdout": not args.no_progress_stdout,
                "train_steps": args.train_steps,
                "max_docs": args.max_docs,
                "min_clean_records": args.min_clean_records,
            },
            sort_keys=True,
        ),
        flush=True,
    )
    payload = asyncio.run(run(args))
    print(json.dumps(payload, indent=2, sort_keys=True))
    raise SystemExit(0 if payload.get("status") == "complete" else 1)


if __name__ == "__main__":
    main()

