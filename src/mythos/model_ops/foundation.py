"""Scratch-model foundation contracts for Mythos.

This module is intentionally not a fine-tuning wrapper. It defines the stable
contracts Mythos will use for tokenizer assets, decoder-only architecture
profiles, pretraining plans, and checkpoint manifests before GPU training code
is attached.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, model_validator

from src.mythos.shared.schemas import StrictModel


class TokenizerContract(StrictModel):
    tokenizer_type: Literal["bpe", "unigram", "sentencepiece"] = "bpe"
    vocab_size: int = Field(default=64_000, ge=8_000, le=512_000)
    tokenizer_path: str | None = None
    special_tokens: list[str] = Field(
        default_factory=lambda: [
            "<|thought_start|>",
            "<|thought_end|>",
            "<|patch_start|>",
            "<|patch_end|>",
            "<|compile_error|>",
            "<|tool_call|>",
            "<|tool_result|>",
        ]
    )

    def required_asset_paths(self) -> list[Path]:
        return [Path(self.tokenizer_path)] if self.tokenizer_path else []


class DecoderArchitectureSpec(StrictModel):
    name: str
    family: Literal["mythos_decoder"] = "mythos_decoder"
    parameter_target_billions: float = Field(gt=0)
    vocab_size: int = Field(default=64_000, ge=8_000)
    context_length: int = Field(default=32_768, ge=2_048)
    hidden_size: int = Field(ge=512)
    num_layers: int = Field(ge=4)
    num_attention_heads: int = Field(ge=1)
    num_key_value_heads: int = Field(ge=1)
    intermediate_size: int = Field(ge=1024)
    rope_theta: float = Field(default=1_000_000.0, gt=0)
    norm_eps: float = Field(default=1e-6, gt=0)
    tie_word_embeddings: bool = False
    activation: Literal["silu", "gelu"] = "silu"
    attention_impl: Literal["flash_attention_2", "sdpa", "eager"] = "flash_attention_2"
    dtype: Literal["bf16", "fp16", "fp32"] = "bf16"

    @model_validator(mode="after")
    def validate_heads(self) -> "DecoderArchitectureSpec":
        if self.hidden_size % self.num_attention_heads != 0:
            raise ValueError("hidden_size must be divisible by num_attention_heads")
        if self.num_attention_heads % self.num_key_value_heads != 0:
            raise ValueError("num_attention_heads must be divisible by num_key_value_heads")
        return self

    def estimate_parameters(self) -> dict[str, Any]:
        embedding = self.vocab_size * self.hidden_size
        attention_per_layer = (
            self.hidden_size * self.hidden_size
            + 2 * self.hidden_size * self.hidden_size * self.num_key_value_heads / self.num_attention_heads
            + self.hidden_size * self.hidden_size
        )
        mlp_per_layer = 3 * self.hidden_size * self.intermediate_size
        norm_per_layer = 2 * self.hidden_size
        transformer = int(self.num_layers * (attention_per_layer + mlp_per_layer + norm_per_layer))
        lm_head = 0 if self.tie_word_embeddings else embedding
        total = int(embedding + transformer + lm_head)
        return {
            "embedding": int(embedding),
            "transformer": transformer,
            "lm_head": int(lm_head),
            "total": total,
            "total_billions": round(total / 1_000_000_000, 3),
            "target_billions": self.parameter_target_billions,
            "delta_billions": round((total / 1_000_000_000) - self.parameter_target_billions, 3),
        }


class TrainingDataContract(StrictModel):
    manifest_path: str
    token_count_estimate: int = Field(ge=0)
    domains: list[str] = Field(default_factory=list)
    contamination_checked: bool = False
    license_checked: bool = False
    pii_scrubbed: bool = False

    def required_asset_paths(self) -> list[Path]:
        return [Path(self.manifest_path)]


class ParallelismPlan(StrictModel):
    tensor_parallel: int = Field(default=1, ge=1)
    pipeline_parallel: int = Field(default=1, ge=1)
    data_parallel: int = Field(default=1, ge=1)
    sequence_parallel: bool = True
    zero_stage: int = Field(default=2, ge=0, le=3)

    @property
    def world_size(self) -> int:
        return self.tensor_parallel * self.pipeline_parallel * self.data_parallel


class PretrainingRunSpec(StrictModel):
    run_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    architecture: DecoderArchitectureSpec
    tokenizer: TokenizerContract = Field(default_factory=TokenizerContract)
    data: TrainingDataContract
    parallelism: ParallelismPlan = Field(default_factory=ParallelismPlan)
    sequence_length: int = Field(default=32_768, ge=2_048)
    global_batch_tokens: int = Field(default=4_194_304, ge=1)
    learning_rate: float = Field(default=3e-4, gt=0)
    min_learning_rate: float = Field(default=3e-5, gt=0)
    warmup_tokens: int = Field(default=10_000_000_000, ge=0)
    target_train_tokens: int = Field(default=1_000_000_000_000, ge=1)
    checkpoint_interval_tokens: int = Field(default=10_000_000_000, ge=1)
    output_dir: str = "artifacts/aeitron/pretraining"

    @model_validator(mode="after")
    def validate_training_shape(self) -> "PretrainingRunSpec":
        if self.sequence_length > self.architecture.context_length:
            raise ValueError("sequence_length cannot exceed architecture.context_length")
        if self.min_learning_rate > self.learning_rate:
            raise ValueError("min_learning_rate cannot exceed learning_rate")
        return self

    def readiness_report(self) -> dict[str, Any]:
        missing_assets = []
        for asset in [*self.tokenizer.required_asset_paths(), *self.data.required_asset_paths()]:
            if not asset.exists():
                missing_assets.append(str(asset))
        policy_failures = []
        if not self.data.contamination_checked:
            policy_failures.append("data contamination check is not complete")
        if not self.data.license_checked:
            policy_failures.append("data license check is not complete")
        if not self.data.pii_scrubbed:
            policy_failures.append("data PII scrub is not complete")
        return {
            "run_id": self.run_id,
            "ready": not missing_assets and not policy_failures,
            "missing_assets": missing_assets,
            "policy_failures": policy_failures,
            "parameter_estimate": self.architecture.estimate_parameters(),
            "world_size": self.parallelism.world_size,
            "scratch_training": True,
            "external_model_training": False,
            "scratch_only": True,
        }


class CheckpointManifest(StrictModel):
    checkpoint_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    architecture_name: str
    run_id: str
    step: int = Field(ge=0)
    trained_tokens: int = Field(ge=0)
    checkpoint_dir: str
    files: list[dict[str, Any]] = Field(default_factory=list)
    metrics: dict[str, float] = Field(default_factory=dict)
    created_at_unix: float = Field(default_factory=time.time)

    @classmethod
    def from_directory(
        cls,
        *,
        architecture_name: str,
        run_id: str,
        step: int,
        trained_tokens: int,
        checkpoint_dir: str | Path,
        metrics: dict[str, float] | None = None,
    ) -> "CheckpointManifest":
        root = Path(checkpoint_dir).resolve()
        files = []
        if root.exists():
            for path in sorted(item for item in root.rglob("*") if item.is_file()):
                files.append(
                    {
                        "path": path.relative_to(root).as_posix(),
                        "size_bytes": path.stat().st_size,
                        "sha256": sha256_file(path),
                    }
                )
        return cls(
            architecture_name=architecture_name,
            run_id=run_id,
            step=step,
            trained_tokens=trained_tokens,
            checkpoint_dir=str(root),
            files=files,
            metrics=metrics or {},
        )

    def write_atomic(self, path: str | Path) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(self.model_dump(), indent=2, sort_keys=True)
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=target.parent, delete=False) as handle:
            handle.write(payload)
            handle.write("\n")
            temp_name = handle.name
        os.replace(temp_name, target)
        return target


def sha256_file(path: Path, *, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def architecture_presets() -> dict[str, DecoderArchitectureSpec]:
    return {
        "aeitron-7b": DecoderArchitectureSpec(
            name="aeitron-7b",
            parameter_target_billions=7.0,
            hidden_size=4096,
            num_layers=32,
            num_attention_heads=32,
            num_key_value_heads=8,
            intermediate_size=11008,
            context_length=65_536,
        ),
        "aeitron-32b": DecoderArchitectureSpec(
            name="aeitron-32b",
            parameter_target_billions=32.0,
            hidden_size=6656,
            num_layers=64,
            num_attention_heads=64,
            num_key_value_heads=8,
            intermediate_size=17920,
            context_length=131_072,
        ),
        "aeitron-70b": DecoderArchitectureSpec(
            name="aeitron-70b",
            parameter_target_billions=70.0,
            hidden_size=8192,
            num_layers=80,
            num_attention_heads=64,
            num_key_value_heads=8,
            intermediate_size=28672,
            context_length=131_072,
        ),
        "aeitron-100b": DecoderArchitectureSpec(
            name="aeitron-100b",
            parameter_target_billions=100.0,
            hidden_size=10240,
            num_layers=88,
            num_attention_heads=80,
            num_key_value_heads=8,
            intermediate_size=32768,
            context_length=131_072,
        ),
    }


def foundation_status() -> dict[str, Any]:
    presets = architecture_presets()
    return {
        "scratch_first": True,
        "scratch_only": True,
        "external_model_training": False,
        "presets": {name: spec.estimate_parameters() for name, spec in presets.items()},
        "required_before_training": [
            "tokenizer asset",
            "deduplicated dataset manifest",
            "license and PII gates",
            "contamination gate",
            "GPU cluster plan",
            "checkpoint storage plan",
            "evaluation baseline",
        ],
    }
