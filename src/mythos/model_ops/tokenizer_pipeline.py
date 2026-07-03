"""Tokenizer training and token sharding pipeline for Mythos scratch pretraining."""

from __future__ import annotations

import argparse
import json
import math
import random
import struct
import time
from pathlib import Path
from typing import Iterable

from pydantic import Field

from src.mythos.shared.schemas import StrictModel


SPECIAL_TOKENS = [
    "<|thought_start|>",
    "<|thought_end|>",
    "<|patch_start|>",
    "<|patch_end|>",
    "<|compile_error|>",
    "<|tool_call|>",
    "<|tool_result|>",
]


class TokenizerTrainConfig(StrictModel):
    vocab_size: int = Field(default=64_000, ge=1_000)
    min_frequency: int = Field(default=2, ge=1)
    special_tokens: list[str] = Field(default_factory=lambda: SPECIAL_TOKENS.copy())


class ShardBuildConfig(StrictModel):
    shard_token_count: int = Field(default=1_000_000, ge=128)
    sequence_length: int = Field(default=2048, ge=16)
    validation_fraction: float = Field(default=0.01, ge=0.0, le=0.5)
    seed: int = 1337


class ShardManifest(StrictModel):
    dataset_id: str
    tokenizer_path: str
    output_dir: str
    train_shards: list[str]
    val_shards: list[str]
    train_tokens: int
    val_tokens: int
    sequence_length: int
    created_at_unix: float = Field(default_factory=time.time)


def iter_texts(paths: list[str | Path]) -> Iterable[str]:
    for path in paths:
        source = Path(path)
        if source.suffix == ".jsonl":
            for line in source.read_text(encoding="utf-8-sig", errors="replace").splitlines():
                if not line.strip():
                    continue
                row = json.loads(line)
                text = str(row.get("text") or row.get("content") or "")
                if text:
                    yield text
        else:
            yield source.read_text(encoding="utf-8", errors="replace")


def train_bpe_tokenizer(input_paths: list[str | Path], output_path: str | Path, config: TokenizerTrainConfig | None = None) -> Path:
    from tokenizers import Tokenizer
    from tokenizers.decoders import ByteLevel as ByteLevelDecoder
    from tokenizers.models import BPE
    from tokenizers.pre_tokenizers import ByteLevel
    from tokenizers.trainers import BpeTrainer

    active = config or TokenizerTrainConfig()
    tokenizer = Tokenizer(BPE(unk_token="<unk>"))
    tokenizer.pre_tokenizer = ByteLevel(add_prefix_space=False)
    tokenizer.decoder = ByteLevelDecoder()
    trainer = BpeTrainer(
        vocab_size=active.vocab_size,
        min_frequency=active.min_frequency,
        special_tokens=["<unk>", *active.special_tokens],
        show_progress=False,
    )
    tokenizer.train_from_iterator(iter_texts(input_paths), trainer=trainer)
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tokenizer.save(str(target))
    return target


def load_tokenizer(path: str | Path):
    from tokenizers import Tokenizer

    return Tokenizer.from_file(str(path))


def write_uint32_tokens(path: Path, tokens: list[int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as handle:
        for token in tokens:
            handle.write(struct.pack("<I", int(token)))


def read_uint32_tokens(path: str | Path) -> list[int]:
    raw = Path(path).read_bytes()
    if len(raw) % 4 != 0:
        raise ValueError(f"token shard byte length is not divisible by 4: {path}")
    return [item[0] for item in struct.iter_unpack("<I", raw)]


def build_token_shards(
    *,
    input_paths: list[str | Path],
    tokenizer_path: str | Path,
    output_dir: str | Path,
    config: ShardBuildConfig | None = None,
    dataset_id: str = "mythos-corpus",
) -> ShardManifest:
    active = config or ShardBuildConfig()
    rng = random.Random(active.seed)
    tokenizer = load_tokenizer(tokenizer_path)
    root = Path(output_dir)
    train_dir = root / "train"
    val_dir = root / "val"
    train_shards: list[str] = []
    val_shards: list[str] = []
    train_tokens = val_tokens = 0
    train_buffer: list[int] = []
    val_buffer: list[int] = []

    def flush(buffer: list[int], split: str) -> None:
        nonlocal train_tokens, val_tokens
        if not buffer:
            return
        directory = train_dir if split == "train" else val_dir
        index = len(train_shards) if split == "train" else len(val_shards)
        path = directory / f"shard-{index:06d}.bin"
        write_uint32_tokens(path, buffer.copy())
        if split == "train":
            train_shards.append(str(path))
            train_tokens += len(buffer)
        else:
            val_shards.append(str(path))
            val_tokens += len(buffer)
        buffer.clear()

    for text in iter_texts(input_paths):
        token_ids = tokenizer.encode(text).ids
        if len(token_ids) < 2:
            continue
        buffer = val_buffer if rng.random() < active.validation_fraction else train_buffer
        buffer.extend(token_ids)
        while len(buffer) >= active.shard_token_count:
            chunk = buffer[: active.shard_token_count]
            del buffer[: active.shard_token_count]
            flush(chunk, "val" if buffer is val_buffer else "train")
    flush(train_buffer, "train")
    flush(val_buffer, "val")
    manifest = ShardManifest(
        dataset_id=dataset_id,
        tokenizer_path=str(tokenizer_path),
        output_dir=str(root),
        train_shards=train_shards,
        val_shards=val_shards,
        train_tokens=train_tokens,
        val_tokens=val_tokens,
        sequence_length=active.sequence_length,
    )
    (root / "manifest.json").write_text(json.dumps(manifest.model_dump(), indent=2, sort_keys=True), encoding="utf-8")
    return manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Train Mythos tokenizer and build token shards.")
    parser.add_argument("--input", nargs="+", required=True)
    parser.add_argument("--tokenizer-out", required=True)
    parser.add_argument("--shards-out", required=True)
    parser.add_argument("--vocab-size", type=int, default=64_000)
    parser.add_argument("--shard-token-count", type=int, default=1_000_000)
    parser.add_argument("--sequence-length", type=int, default=2048)
    parser.add_argument("--validation-fraction", type=float, default=0.01)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    tokenizer_path = train_bpe_tokenizer(args.input, args.tokenizer_out, TokenizerTrainConfig(vocab_size=args.vocab_size))
    manifest = build_token_shards(
        input_paths=args.input,
        tokenizer_path=tokenizer_path,
        output_dir=args.shards_out,
        config=ShardBuildConfig(
            shard_token_count=args.shard_token_count,
            sequence_length=args.sequence_length,
            validation_fraction=args.validation_fraction,
        ),
    )
    print(json.dumps(manifest.model_dump(), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
