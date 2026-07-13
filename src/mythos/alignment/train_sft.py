"""Native PyTorch SFT loop for Mythos scratch checkpoints."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

from pydantic import Field

from src.mythos.alignment.common import SFTExample, encode_text, load_checkpoint_model, load_jsonl_models, load_policy, load_tokenizer_required, prompt_response_text, select_device
from src.mythos.model_ops.pretrain_loop import save_training_checkpoint
from src.mythos.shared.schemas import StrictModel

try:
    import torch
except ImportError:  # pragma: no cover
    torch = None  # type: ignore[assignment]


class AlignmentTrainingReport(StrictModel):
    status: str
    mode: str
    checkpoint_manifest: str
    output_dir: str
    steps: int
    losses: list[float]
    trained_examples: int
    duration_ms: float
    created_at_unix: float = Field(default_factory=time.time)


def train_sft(
    *,
    checkpoint_manifest: str | Path,
    dataset: str | Path,
    output_dir: str | Path,
    tokenizer_path: str | Path | None = None,
    policy_path: str | Path = "config/alignment_policy.json",
    steps: int = 10,
    device: str = "auto",
    learning_rate: float | None = None,
) -> AlignmentTrainingReport:
    if torch is None:
        raise RuntimeError("torch is required for SFT training")
    selected = select_device(device)
    tokenizer = load_tokenizer_required(tokenizer_path)
    model, manifest, _payload = load_checkpoint_model(checkpoint_manifest, device=selected)
    policy = load_policy(policy_path)
    lr = float(learning_rate or policy.get("learning_rate", 5e-5))
    examples = [item for item in load_jsonl_models(dataset, SFTExample)]  # type: ignore[list-item]
    if not examples:
        raise ValueError("SFT dataset has no valid examples")
    optimizer = torch.optim.AdamW(model.parameters(), lr=lr, betas=(0.9, 0.95), weight_decay=0.01)
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    model.train()
    losses: list[float] = []
    started = time.perf_counter()
    for step in range(steps):
        example = examples[step % len(examples)]
        text = prompt_response_text(example.prompt, example.response)
        ids = encode_text(tokenizer, text, max_length=model.config.max_sequence_length)
        if len(ids) < 2:
            continue
        input_ids = torch.tensor([ids], dtype=torch.long, device=selected)
        output = model(input_ids, labels=input_ids)
        if output.loss is None:
            continue
        optimizer.zero_grad(set_to_none=True)
        output.loss.backward()
        torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
        optimizer.step()
        losses.append(float(output.loss.detach().cpu()))
    if not losses:
        raise RuntimeError("SFT loop produced no trainable batches")
    manifest_path = save_training_checkpoint(
        output_dir=root,
        model=model,
        optimizer=optimizer,
        config=model.config,
        step=manifest.step + len(losses),
        trained_tokens=manifest.trained_tokens,
        metrics={"sft_loss": losses[-1], "sft_loss_best": min(losses)},
        manifest_filename="checkpoint_manifest.json",
    )
    report = AlignmentTrainingReport(
        status="passed",
        mode="sft",
        checkpoint_manifest=str(manifest_path),
        output_dir=str(root),
        steps=len(losses),
        losses=losses,
        trained_examples=len(examples),
        duration_ms=(time.perf_counter() - started) * 1000,
    )
    (root / "alignment_training_report.json").write_text(json.dumps(report.model_dump(), indent=2, sort_keys=True), encoding="utf-8")
    return report


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Train Mythos scratch checkpoint with native SFT.")
    parser.add_argument("--checkpoint-manifest", required=True)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--tokenizer-path")
    parser.add_argument("--policy", default="config/alignment_policy.json")
    parser.add_argument("--steps", type=int, default=10)
    parser.add_argument("--device", default="auto")
    parser.add_argument("--learning-rate", type=float)
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    report = train_sft(
        checkpoint_manifest=args.checkpoint_manifest,
        dataset=args.dataset,
        output_dir=args.output_dir,
        tokenizer_path=args.tokenizer_path,
        policy_path=args.policy,
        steps=args.steps,
        device=args.device,
        learning_rate=args.learning_rate,
    )
    print(json.dumps(report.model_dump(), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
