"""Shared alignment schemas and checkpoint helpers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from src.mythos.model_ops.foundation import CheckpointManifest
from src.mythos.model_ops.tokenizer_pipeline import load_tokenizer
from src.mythos.model_ops.torch_decoder import MythosDecoderLM, ScratchDecoderConfig, load_trusted_checkpoint, require_torch
from src.mythos.shared.schemas import StrictModel

try:
    import torch
except ImportError:  # pragma: no cover
    torch = None  # type: ignore[assignment]


SafetyLabel = Literal["harmful", "helpful_defensive", "neutral"]


class SFTExample(StrictModel):
    prompt: str = Field(min_length=1)
    response: str = Field(min_length=1)
    category: str = "general"
    safety_label: SafetyLabel = "neutral"
    source: str = "unknown"
    metadata: dict[str, Any] = Field(default_factory=dict)


class PreferencePair(StrictModel):
    prompt: str = Field(min_length=1)
    chosen: str = Field(min_length=1)
    rejected: str = Field(min_length=1)
    category: str = "general"
    safety_label: SafetyLabel = "neutral"
    source: str = "unknown"
    metadata: dict[str, Any] = Field(default_factory=dict)


def select_device(requested: str):
    require_torch()
    if requested == "auto":
        return torch.device("cuda" if torch.cuda.is_available() else "cpu")
    if requested == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("CUDA requested but unavailable")
    return torch.device(requested)


def load_checkpoint_model(manifest_path: str | Path, *, device: Any) -> tuple[MythosDecoderLM, CheckpointManifest, dict[str, Any]]:
    manifest = CheckpointManifest.model_validate(json.loads(Path(manifest_path).read_text(encoding="utf-8-sig")))
    checkpoint_path = Path(manifest.checkpoint_dir) / "model.pt"
    if not checkpoint_path.exists():
        raise FileNotFoundError(f"checkpoint model file not found: {checkpoint_path}")
    payload = load_trusted_checkpoint(checkpoint_path, map_location=device)
    config = ScratchDecoderConfig.model_validate(payload["config"])
    model = MythosDecoderLM(config).to(device)
    model.load_state_dict(payload["model"])
    return model, manifest, payload


def encode_text(tokenizer: Any, text: str, *, max_length: int) -> list[int]:
    ids = tokenizer.encode(text).ids
    if not ids:
        ids = [0]
    return ids[-max_length:]


def prompt_response_text(prompt: str, response: str) -> str:
    return f"User:\n{prompt}\n\nAssistant:\n{response}"


def load_jsonl_models(path: str | Path, model: type[StrictModel]) -> list[StrictModel]:
    rows = []
    with Path(path).open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                rows.append(model.model_validate(json.loads(line)))
            except Exception as exc:
                raise ValueError(f"invalid alignment JSONL row in {path} at line {line_number}: {exc}") from exc
    return rows


def save_jsonl(path: str | Path, rows: list[StrictModel]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row.model_dump(), ensure_ascii=False, sort_keys=True) + "\n")


def load_policy(path: str | Path = "config/alignment_policy.json") -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def load_tokenizer_required(path: str | Path | None):
    if not path:
        raise ValueError("--tokenizer-path is required for scratch checkpoint alignment training")
    return load_tokenizer(path)
