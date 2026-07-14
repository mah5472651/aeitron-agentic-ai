"""Streaming token-shard dataloader for scratch pretraining."""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Iterator

from src.aeitron.model_ops.tokenizer_pipeline import ShardManifest, read_uint32_tokens


def load_manifest(path: str | Path) -> ShardManifest:
    return ShardManifest.model_validate(json.loads(Path(path).read_text(encoding="utf-8-sig")))


class TokenShardStream:
    def __init__(
        self,
        shard_paths: list[str],
        *,
        sequence_length: int,
        batch_size: int,
        seed: int = 1337,
        shuffle: bool = True,
    ) -> None:
        if not shard_paths:
            raise ValueError("at least one shard path is required")
        self.shard_paths = shard_paths
        self.sequence_length = sequence_length
        self.batch_size = batch_size
        self.seed = seed
        self.shuffle = shuffle

    def batches(self, *, epoch: int = 0) -> Iterator[list[list[int]]]:
        rng = random.Random(self.seed + epoch)
        paths = self.shard_paths.copy()
        if self.shuffle:
            rng.shuffle(paths)
        buffer: list[int] = []
        needed = self.batch_size * self.sequence_length
        for path in paths:
            tokens = read_uint32_tokens(path)
            if self.shuffle:
                offset = rng.randrange(max(1, min(len(tokens), self.sequence_length)))
                tokens = tokens[offset:] + tokens[:offset]
            buffer.extend(tokens)
            while len(buffer) >= needed:
                chunk = buffer[:needed]
                del buffer[:needed]
                yield [
                    chunk[index : index + self.sequence_length]
                    for index in range(0, needed, self.sequence_length)
                ]


def count_batches(shard_paths: list[str], *, sequence_length: int, batch_size: int) -> int:
    total_tokens = sum(len(read_uint32_tokens(path)) for path in shard_paths)
    return total_tokens // (sequence_length * batch_size)

