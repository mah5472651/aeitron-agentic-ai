"""Minimal scratch pretraining loop for Mythos decoder models."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

from src.mythos.model_ops.foundation import CheckpointManifest
from src.mythos.model_ops.torch_decoder import MythosDecoderLM, ScratchDecoderConfig, require_torch, tiny_smoke_config

try:
    import torch
except ImportError:  # pragma: no cover
    torch = None  # type: ignore[assignment]


def load_token_file(path: str | Path, *, vocab_size: int) -> list[int]:
    source = Path(path)
    tokens: list[int] = []
    for piece in source.read_text(encoding="utf-8", errors="replace").split():
        try:
            tokens.append(int(piece) % vocab_size)
        except ValueError:
            tokens.append(abs(hash(piece)) % vocab_size)
    return tokens


def make_batch(tokens: list[int], *, batch_size: int, sequence_length: int, vocab_size: int, device: "torch.device") -> "torch.Tensor":
    if len(tokens) < batch_size * sequence_length:
        extra = torch.randint(0, vocab_size, (batch_size * sequence_length - len(tokens),), device=device).tolist()
        tokens = [*tokens, *extra]
    data = torch.tensor(tokens[: batch_size * sequence_length], dtype=torch.long, device=device)
    return data.view(batch_size, sequence_length)


def run_pretraining_loop(
    *,
    output_dir: str | Path,
    token_file: str | Path | None = None,
    device: str = "auto",
    steps: int = 10,
    batch_size: int = 2,
    sequence_length: int = 64,
    learning_rate: float = 1e-3,
) -> dict[str, Any]:
    require_torch()
    if steps < 1:
        raise ValueError("steps must be >= 1")
    selected = torch.device("cuda" if device == "auto" and torch.cuda.is_available() else ("cpu" if device == "auto" else device))
    config: ScratchDecoderConfig = tiny_smoke_config()
    if sequence_length > config.max_sequence_length:
        raise ValueError("sequence_length exceeds config max")
    model = MythosDecoderLM(config).to(selected)
    optimizer = torch.optim.AdamW(model.parameters(), lr=learning_rate, betas=(0.9, 0.95), weight_decay=0.1)
    tokens = load_token_file(token_file, vocab_size=config.vocab_size) if token_file else []
    losses: list[float] = []
    started = time.perf_counter()
    for step in range(steps):
        if tokens:
            batch = make_batch(tokens[step:] + tokens[:step], batch_size=batch_size, sequence_length=sequence_length, vocab_size=config.vocab_size, device=selected)
        else:
            batch = torch.randint(0, config.vocab_size, (batch_size, sequence_length), device=selected)
        optimizer.zero_grad(set_to_none=True)
        output = model(batch, labels=batch)
        if output.loss is None:
            raise RuntimeError("loss missing")
        output.loss.backward()
        torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
        optimizer.step()
        losses.append(float(output.loss.detach().cpu()))
    root = Path(output_dir).resolve()
    checkpoint_dir = root / "checkpoint"
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    torch.save({"model": model.state_dict(), "optimizer": optimizer.state_dict(), "config": config.model_dump(), "step": steps}, checkpoint_dir / "model.pt")
    manifest = CheckpointManifest.from_directory(
        architecture_name=config.name,
        run_id="scratch-pretrain-loop",
        step=steps,
        trained_tokens=steps * batch_size * sequence_length,
        checkpoint_dir=checkpoint_dir,
        metrics={"loss": losses[-1]},
    )
    manifest_path = manifest.write_atomic(root / "checkpoint_manifest.json")
    report = {
        "status": "passed",
        "scratch_only": True,
        "steps": steps,
        "device": str(selected),
        "losses": losses,
        "trained_tokens": steps * batch_size * sequence_length,
        "checkpoint_manifest": str(manifest_path),
        "duration_ms": round((time.perf_counter() - started) * 1000, 3),
    }
    (root / "pretrain_report.json").write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Mythos scratch pretraining loop.")
    parser.add_argument("--output-dir", default="artifacts/mythos/pretrain-loop")
    parser.add_argument("--token-file")
    parser.add_argument("--device", choices=["auto", "cpu", "cuda"], default="auto")
    parser.add_argument("--steps", type=int, default=10)
    parser.add_argument("--batch-size", type=int, default=2)
    parser.add_argument("--sequence-length", type=int, default=64)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    print(json.dumps(run_pretraining_loop(**vars(args)), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
