"""Checkpoint-resumable scratch pretraining loop for Mythos decoder models."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

from src.mythos.model_ops.data_loader import TokenShardStream, count_batches, load_manifest
from src.mythos.model_ops.foundation import CheckpointManifest
from src.mythos.model_ops.tokenizer_pipeline import ShardBuildConfig, ShardManifest, build_token_shards, load_tokenizer, read_uint32_tokens
from src.mythos.model_ops.torch_decoder import MythosDecoderLM, ScratchDecoderConfig, require_torch, tiny_smoke_config

try:
    import torch
except ImportError:  # pragma: no cover
    torch = None  # type: ignore[assignment]


def select_device(requested: str) -> "torch.device":
    require_torch()
    if requested == "auto":
        return torch.device("cuda" if torch.cuda.is_available() else "cpu")
    if requested == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("CUDA requested but unavailable")
    return torch.device(requested)


def autocast_dtype(name: str) -> "torch.dtype":
    if name == "bf16":
        return torch.bfloat16
    if name == "fp16":
        return torch.float16
    return torch.float32


def latest_checkpoint(output_dir: str | Path) -> Path | None:
    root = Path(output_dir)
    candidates = sorted(root.glob("checkpoint-step-*/model.pt"))
    return candidates[-1] if candidates else None


def save_training_checkpoint(
    *,
    output_dir: Path,
    model: "MythosDecoderLM",
    optimizer: "torch.optim.Optimizer",
    config: ScratchDecoderConfig,
    step: int,
    trained_tokens: int,
    metrics: dict[str, float],
) -> Path:
    checkpoint_dir = output_dir / f"checkpoint-step-{step:08d}"
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    torch.save(
        {
            "model": model.state_dict(),
            "optimizer": optimizer.state_dict(),
            "config": config.model_dump(),
            "step": step,
            "trained_tokens": trained_tokens,
            "metrics": metrics,
        },
        checkpoint_dir / "model.pt",
    )
    (checkpoint_dir / "config.json").write_text(json.dumps(config.model_dump(), indent=2, sort_keys=True), encoding="utf-8")
    manifest = CheckpointManifest.from_directory(
        architecture_name=config.name,
        run_id="scratch-pretrain-loop",
        step=step,
        trained_tokens=trained_tokens,
        checkpoint_dir=checkpoint_dir,
        metrics=metrics,
    )
    return manifest.write_atomic(output_dir / "checkpoint_manifest.json")


def load_checkpoint(
    checkpoint_path: Path,
    *,
    model: "MythosDecoderLM",
    optimizer: "torch.optim.Optimizer",
    device: "torch.device",
) -> tuple[int, int]:
    payload = torch.load(checkpoint_path, map_location=device)
    model.load_state_dict(payload["model"])
    optimizer.load_state_dict(payload["optimizer"])
    return int(payload.get("step", 0)), int(payload.get("trained_tokens", 0))


def tensor_batch(batch: list[list[int]], *, device: "torch.device") -> "torch.Tensor":
    return torch.tensor(batch, dtype=torch.long, device=device)


def validate_training_shards(*, train_shards: list[str], sequence_length: int, batch_size: int) -> int:
    if not train_shards:
        raise ValueError("manifest has no training shards; provide a corpus that produces at least one train shard")
    required_tokens = sequence_length * batch_size
    available_batches = count_batches(train_shards, sequence_length=sequence_length, batch_size=batch_size)
    if available_batches < 1:
        total_tokens = sum(len(Path(path).read_bytes()) // 4 for path in train_shards)
        raise ValueError(
            "not enough training tokens for one batch: "
            f"train_tokens={total_tokens}, required_tokens={required_tokens} "
            f"(batch_size={batch_size} * sequence_length={sequence_length}). "
            "Use a larger corpus, reduce --batch-size, or reduce --sequence-length."
        )
    return available_batches


def max_token_id(shard_paths: list[str]) -> int:
    maximum = -1
    for path in shard_paths:
        tokens = read_uint32_tokens(path)
        if tokens:
            maximum = max(maximum, max(tokens))
    return maximum


def tokenizer_vocab_size(tokenizer_path: str | Path) -> int:
    return int(load_tokenizer(tokenizer_path).get_vocab_size(with_added_tokens=True))


def build_training_config(active_manifest: ShardManifest, *, sequence_length: int) -> ScratchDecoderConfig:
    base = tiny_smoke_config()
    vocab_size = base.vocab_size
    tokenizer_path = Path(active_manifest.tokenizer_path)
    if tokenizer_path.exists():
        vocab_size = max(vocab_size, tokenizer_vocab_size(tokenizer_path))
    highest_token_id = max_token_id(active_manifest.train_shards + active_manifest.val_shards)
    if highest_token_id >= vocab_size:
        vocab_size = highest_token_id + 1
    return base.model_copy(
        update={
            "vocab_size": vocab_size,
            "max_sequence_length": max(base.max_sequence_length, sequence_length),
        }
    )


@torch.no_grad() if torch is not None else (lambda fn: fn)
def validation_loss(
    *,
    model: "MythosDecoderLM",
    stream: TokenShardStream,
    device: "torch.device",
    max_batches: int,
    dtype: str,
) -> float:
    model.eval()
    losses: list[float] = []
    use_autocast = device.type == "cuda" and dtype in {"bf16", "fp16"}
    for index, batch in enumerate(stream.batches(epoch=0)):
        if index >= max_batches:
            break
        input_ids = tensor_batch(batch, device=device)
        with torch.autocast(device_type=device.type, dtype=autocast_dtype(dtype), enabled=use_autocast):
            output = model(input_ids, labels=input_ids)
        if output.loss is not None:
            losses.append(float(output.loss.detach().cpu()))
    model.train()
    return sum(losses) / max(1, len(losses))


def run_pretraining_loop(
    *,
    output_dir: str | Path,
    manifest: str | Path | None = None,
    token_file: str | Path | None = None,
    tokenizer_path: str | Path | None = None,
    device: str = "auto",
    steps: int = 100,
    batch_size: int = 2,
    sequence_length: int = 64,
    learning_rate: float = 1e-3,
    gradient_accumulation_steps: int = 1,
    dtype: str = "bf16",
    validate_every: int = 25,
    validation_batches: int = 4,
    checkpoint_every: int = 50,
    resume: bool = True,
) -> dict[str, Any]:
    require_torch()
    if steps < 1:
        raise ValueError("steps must be >= 1")
    if gradient_accumulation_steps < 1:
        raise ValueError("gradient_accumulation_steps must be >= 1")
    selected = select_device(device)
    root = Path(output_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    active_manifest_path: Path | None = Path(manifest).resolve() if manifest else None
    if active_manifest_path is None and token_file and tokenizer_path:
        active_manifest = build_token_shards(
            input_paths=[token_file],
            tokenizer_path=tokenizer_path,
            output_dir=root / "generated-shards",
            config=ShardBuildConfig(
                shard_token_count=max(128, batch_size * sequence_length * 8),
                sequence_length=sequence_length,
                validation_fraction=0.1,
            ),
        )
    elif active_manifest_path is not None:
        active_manifest = load_manifest(active_manifest_path)
    else:
        raise ValueError("provide --manifest, or both --token-file and --tokenizer-path")

    config = build_training_config(active_manifest, sequence_length=sequence_length)
    available_batches = validate_training_shards(
        train_shards=active_manifest.train_shards,
        sequence_length=sequence_length,
        batch_size=batch_size,
    )
    train_stream = TokenShardStream(
        active_manifest.train_shards,
        sequence_length=sequence_length,
        batch_size=batch_size,
        seed=1337,
        shuffle=True,
    )
    val_stream = (
        TokenShardStream(active_manifest.val_shards, sequence_length=sequence_length, batch_size=batch_size, seed=7331, shuffle=False)
        if active_manifest.val_shards
        else None
    )

    model = MythosDecoderLM(config).to(selected)
    optimizer = torch.optim.AdamW(model.parameters(), lr=learning_rate, betas=(0.9, 0.95), weight_decay=0.1)
    start_step = 0
    trained_tokens = 0
    if resume:
        checkpoint = latest_checkpoint(root)
        if checkpoint is not None:
            start_step, trained_tokens = load_checkpoint(checkpoint, model=model, optimizer=optimizer, device=selected)

    model.train()
    started = time.perf_counter()
    train_losses: list[float] = []
    val_losses: list[dict[str, float]] = []
    use_autocast = selected.type == "cuda" and dtype in {"bf16", "fp16"}
    optimizer.zero_grad(set_to_none=True)
    current_step = start_step
    epoch = 0
    while current_step < steps:
        progressed = False
        for batch in train_stream.batches(epoch=epoch):
            input_ids = tensor_batch(batch, device=selected)
            with torch.autocast(device_type=selected.type, dtype=autocast_dtype(dtype), enabled=use_autocast):
                output = model(input_ids, labels=input_ids)
                if output.loss is None:
                    raise RuntimeError("loss missing")
                loss = output.loss / gradient_accumulation_steps
            loss.backward()
            progressed = True
            if (current_step + 1) % gradient_accumulation_steps == 0:
                torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
                optimizer.step()
                optimizer.zero_grad(set_to_none=True)
            current_step += 1
            trained_tokens += batch_size * sequence_length
            train_losses.append(float(output.loss.detach().cpu()))

            if val_stream is not None and validate_every > 0 and current_step % validate_every == 0:
                val_losses.append(
                    {
                        "step": float(current_step),
                        "loss": validation_loss(
                            model=model,
                            stream=val_stream,
                            device=selected,
                            max_batches=validation_batches,
                            dtype=dtype,
                        ),
                    }
                )
            if checkpoint_every > 0 and current_step % checkpoint_every == 0:
                save_training_checkpoint(
                    output_dir=root,
                    model=model,
                    optimizer=optimizer,
                    config=config,
                    step=current_step,
                    trained_tokens=trained_tokens,
                    metrics={"train_loss": train_losses[-1], "val_loss": val_losses[-1]["loss"] if val_losses else -1.0},
                )
            if current_step >= steps:
                break
        if not progressed:
            raise RuntimeError(
                "no training batches were produced from shards after preflight validation; "
                f"available_batches={available_batches}"
            )
        epoch += 1

    manifest_path = save_training_checkpoint(
        output_dir=root,
        model=model,
        optimizer=optimizer,
        config=config,
        step=current_step,
        trained_tokens=trained_tokens,
        metrics={"train_loss": train_losses[-1], "val_loss": val_losses[-1]["loss"] if val_losses else -1.0},
    )
    report = {
        "status": "passed",
        "scratch_only": True,
        "steps": current_step,
        "start_step": start_step,
        "device": str(selected),
        "dtype": dtype,
        "gradient_accumulation_steps": gradient_accumulation_steps,
        "validate_every": validate_every,
        "validation_batches": validation_batches,
        "model_config": config.model_dump(),
        "train_losses": train_losses,
        "validation_losses": val_losses,
        "trained_tokens": trained_tokens,
        "checkpoint_manifest": str(manifest_path),
        "duration_ms": round((time.perf_counter() - started) * 1000, 3),
    }
    (root / "pretrain_report.json").write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Mythos scratch pretraining loop.")
    parser.add_argument("--output-dir", default="artifacts/mythos/pretrain-loop")
    parser.add_argument("--manifest")
    parser.add_argument("--token-file")
    parser.add_argument("--tokenizer-path")
    parser.add_argument("--device", choices=["auto", "cpu", "cuda"], default="auto")
    parser.add_argument("--steps", type=int, default=100)
    parser.add_argument("--batch-size", type=int, default=2)
    parser.add_argument("--sequence-length", type=int, default=64)
    parser.add_argument("--learning-rate", type=float, default=1e-3)
    parser.add_argument("--gradient-accumulation-steps", type=int, default=1)
    parser.add_argument("--dtype", choices=["bf16", "fp16", "fp32"], default="bf16")
    parser.add_argument("--validate-every", type=int, default=25)
    parser.add_argument("--validation-batches", type=int, default=4)
    parser.add_argument("--checkpoint-every", type=int, default=50)
    parser.add_argument("--no-resume", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    report = run_pretraining_loop(
        output_dir=args.output_dir,
        manifest=args.manifest,
        token_file=args.token_file,
        tokenizer_path=args.tokenizer_path,
        device=args.device,
        steps=args.steps,
        batch_size=args.batch_size,
        sequence_length=args.sequence_length,
        learning_rate=args.learning_rate,
        gradient_accumulation_steps=args.gradient_accumulation_steps,
        dtype=args.dtype,
        validate_every=args.validate_every,
        validation_batches=args.validation_batches,
        checkpoint_every=args.checkpoint_every,
        resume=not args.no_resume,
    )
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
