"""Token-aware data mixing controller for Mythos training corpora."""

from __future__ import annotations

import argparse
import json
import random
import time
from pathlib import Path
from typing import Any

from pydantic import Field, model_validator

from src.mythos.learning.quality import iter_jsonl, stable_hash
from src.mythos.model_ops.tokenizer_pipeline import ShardBuildConfig, ShardManifest, build_token_shards, load_tokenizer
from src.mythos.shared.schemas import StrictModel


class MixExperiment(StrictModel):
    name: str
    ratios: dict[str, float]

    @model_validator(mode="after")
    def validate_ratios(self) -> "MixExperiment":
        total = sum(float(value) for value in self.ratios.values())
        if total <= 0:
            raise ValueError("mix ratios must sum to a positive value")
        return self


class CurriculumStage(StrictModel):
    name: str
    step_fraction_end: float = Field(gt=0.0, le=1.0)
    ratios: dict[str, float]


class MixConfig(StrictModel):
    seed: int = 1337
    tokenizer_path: str | None = None
    max_rows: int | None = Field(default=None, ge=1)
    experiments: list[MixExperiment]
    progressive_curriculum: list[CurriculumStage] = Field(default_factory=list)
    holdout_policies: list[str] = Field(default_factory=lambda: ["eval_holdout", "benchmark_holdout"])
    min_quality_score: float = Field(default=0.0, ge=0.0, le=1.0)


class MixBucketReport(StrictModel):
    bucket: str
    input_rows: int
    output_rows: int
    output_tokens: int
    target_ratio: float
    actual_ratio: float


class MixManifest(StrictModel):
    experiment: str
    input_paths: list[str]
    output_jsonl: str
    shard_manifest: dict[str, Any] | None = None
    tokenizer_path: str | None = None
    total_rows: int
    total_tokens: int
    buckets: list[MixBucketReport]
    excluded_holdout_rows: int
    created_at_unix: float = Field(default_factory=time.time)


def load_mix_config(path: str | Path) -> MixConfig:
    return MixConfig.model_validate(json.loads(Path(path).read_text(encoding="utf-8")))


def _row_text(row: dict[str, Any]) -> str:
    return str(row.get("text") or row.get("content") or row.get("prompt") or "")


def classify_row(row: dict[str, Any]) -> str:
    metadata = row.get("metadata", {}) if isinstance(row.get("metadata"), dict) else {}
    policy = str(row.get("train_policy") or metadata.get("train_policy") or "").lower()
    if policy in {"eval_holdout", "benchmark_holdout"}:
        return "holdout"
    category = str(row.get("category") or metadata.get("category") or "").lower()
    quality = row.get("quality", {}) if isinstance(row.get("quality"), dict) else {}
    labels = {str(item).lower() for item in quality.get("labels", [])}
    text = _row_text(row).lower()
    if "agentic" in category or "agentic" in labels or "task_type" in row:
        return "agentic"
    if "cyber" in category or "security" in category or "defensive_security" in labels:
        return "cybersecurity"
    if "code" in category or "code" in labels or any(token in text for token in ("def ", "class ", "function ", "fn ", "package ")):
        return "code"
    return "general"


def _estimate_tokens(text: str, tokenizer: Any | None) -> int:
    if tokenizer is not None:
        return max(1, len(tokenizer.encode(text).ids))
    return max(1, len(text.split()))


def _load_rows(
    input_paths: list[str | Path],
    tokenizer: Any | None,
    holdout_policies: set[str],
    *,
    min_quality_score: float,
) -> tuple[dict[str, list[dict[str, Any]]], int]:
    buckets: dict[str, list[dict[str, Any]]] = {"general": [], "code": [], "cybersecurity": [], "agentic": []}
    excluded = 0
    seen: set[str] = set()
    for path in input_paths:
        for row in iter_jsonl(path):
            metadata = row.get("metadata", {}) if isinstance(row.get("metadata"), dict) else {}
            policy = str(row.get("train_policy") or metadata.get("train_policy") or "").lower()
            bucket = classify_row(row)
            if bucket == "holdout" or policy in holdout_policies:
                excluded += 1
                continue
            quality = row.get("quality", {}) if isinstance(row.get("quality"), dict) else {}
            training_gate = row.get("training_gate", {}) if isinstance(row.get("training_gate"), dict) else {}
            quality_score = max(float(quality.get("quality_score", 0.0)), float(training_gate.get("score", 0.0)))
            if quality_score < min_quality_score:
                excluded += 1
                continue
            text = _row_text(row)
            if not text:
                continue
            digest = str(row.get("content_hash") or stable_hash(text))
            if digest in seen:
                continue
            seen.add(digest)
            row = dict(row)
            row["content_hash"] = digest
            row["_mix_bucket"] = bucket
            row["_estimated_tokens"] = _estimate_tokens(text, tokenizer)
            buckets.setdefault(bucket, []).append(row)
    return buckets, excluded


def _normalize_ratios(ratios: dict[str, float]) -> dict[str, float]:
    total = sum(max(0.0, float(value)) for value in ratios.values()) or 1.0
    return {key: max(0.0, float(value)) / total for key, value in ratios.items()}


def _sample_mixed_rows(
    *,
    buckets: dict[str, list[dict[str, Any]]],
    ratios: dict[str, float],
    rng: random.Random,
    max_rows: int | None,
) -> list[dict[str, Any]]:
    normalized = _normalize_ratios(ratios)
    for rows in buckets.values():
        rng.shuffle(rows)
    cursors = {bucket: 0 for bucket in buckets}
    token_totals = {bucket: 0 for bucket in buckets}
    output: list[dict[str, Any]] = []
    total_available = sum(len(rows) for rows in buckets.values())
    limit = min(max_rows or total_available, total_available)
    active_buckets = [bucket for bucket, rows in buckets.items() if rows]
    while len(output) < limit and active_buckets:
        choices = [bucket for bucket in active_buckets if cursors[bucket] < len(buckets[bucket])]
        if not choices:
            break
        current_total_tokens = max(1, sum(token_totals.values()))
        deficits = []
        for candidate in choices:
            target_ratio = normalized.get(candidate, 0.0)
            actual_ratio = token_totals[candidate] / current_total_tokens
            deficits.append((target_ratio - actual_ratio, rng.random(), candidate))
        deficits.sort(reverse=True)
        bucket = deficits[0][2]
        row = buckets[bucket][cursors[bucket]]
        cursors[bucket] += 1
        token_totals[bucket] += int(row.get("_estimated_tokens", 1))
        output.append(row)
        active_buckets = [item for item in active_buckets if cursors[item] < len(buckets[item])]
    return output


def build_mix(
    *,
    input_paths: list[str | Path],
    config_path: str | Path,
    experiment: str,
    output_dir: str | Path,
    tokenizer_path: str | Path | None = None,
    shard_token_count: int = 1_000_000,
    sequence_length: int = 2048,
    validation_fraction: float = 0.01,
) -> MixManifest:
    config = load_mix_config(config_path)
    active_experiment = next((item for item in config.experiments if item.name == experiment), None)
    if active_experiment is None:
        raise ValueError(f"unknown mix experiment: {experiment}")
    active_tokenizer_path = str(tokenizer_path or config.tokenizer_path or "") or None
    tokenizer = load_tokenizer(active_tokenizer_path) if active_tokenizer_path else None
    buckets, excluded = _load_rows(
        input_paths,
        tokenizer,
        set(config.holdout_policies),
        min_quality_score=config.min_quality_score,
    )
    rng = random.Random(config.seed)
    mixed_rows = _sample_mixed_rows(
        buckets=buckets,
        ratios=active_experiment.ratios,
        rng=rng,
        max_rows=config.max_rows,
    )
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    output_jsonl = root / f"{experiment}.mixed.jsonl"
    bucket_stats: dict[str, dict[str, float]] = {
        bucket: {"input_rows": float(len(rows)), "output_rows": 0.0, "output_tokens": 0.0}
        for bucket, rows in buckets.items()
    }
    with output_jsonl.open("w", encoding="utf-8") as handle:
        for row in mixed_rows:
            bucket = str(row.pop("_mix_bucket"))
            tokens = int(row.pop("_estimated_tokens"))
            bucket_stats[bucket]["output_rows"] += 1
            bucket_stats[bucket]["output_tokens"] += tokens
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    total_tokens = int(sum(item["output_tokens"] for item in bucket_stats.values()))
    normalized = _normalize_ratios(active_experiment.ratios)
    reports = [
        MixBucketReport(
            bucket=bucket,
            input_rows=int(stats["input_rows"]),
            output_rows=int(stats["output_rows"]),
            output_tokens=int(stats["output_tokens"]),
            target_ratio=round(normalized.get(bucket, 0.0), 6),
            actual_ratio=round(stats["output_tokens"] / max(1, total_tokens), 6),
        )
        for bucket, stats in sorted(bucket_stats.items())
    ]
    shard_manifest: ShardManifest | None = None
    if active_tokenizer_path:
        shard_manifest = build_token_shards(
            input_paths=[output_jsonl],
            tokenizer_path=active_tokenizer_path,
            output_dir=root / "shards",
            config=ShardBuildConfig(
                shard_token_count=shard_token_count,
                sequence_length=sequence_length,
                validation_fraction=validation_fraction,
            ),
            dataset_id=f"mythos-mix-{experiment}",
        )
    manifest = MixManifest(
        experiment=experiment,
        input_paths=[str(path) for path in input_paths],
        output_jsonl=str(output_jsonl),
        shard_manifest=shard_manifest.model_dump() if shard_manifest else None,
        tokenizer_path=active_tokenizer_path,
        total_rows=len(mixed_rows),
        total_tokens=total_tokens,
        buckets=reports,
        excluded_holdout_rows=excluded,
    )
    (root / "mix_manifest.json").write_text(json.dumps(manifest.model_dump(), indent=2, sort_keys=True), encoding="utf-8")
    return manifest


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a Mythos token-ratio mixed corpus.")
    parser.add_argument("--inputs", nargs="+", required=True)
    parser.add_argument("--config", default="config/mix_ratios.json")
    parser.add_argument("--experiment", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--tokenizer-path")
    parser.add_argument("--shard-token-count", type=int, default=1_000_000)
    parser.add_argument("--sequence-length", type=int, default=2048)
    parser.add_argument("--validation-fraction", type=float, default=0.01)
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    manifest = build_mix(
        input_paths=args.inputs,
        config_path=args.config,
        experiment=args.experiment,
        output_dir=args.output_dir,
        tokenizer_path=args.tokenizer_path,
        shard_token_count=args.shard_token_count,
        sequence_length=args.sequence_length,
        validation_fraction=args.validation_fraction,
    )
    print(json.dumps(manifest.model_dump(), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
