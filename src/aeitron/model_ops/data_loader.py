"""Streaming token-shard dataloader for scratch pretraining."""

from __future__ import annotations

import json
import queue
import random
import threading
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
        prefetch_batches: int = 0,
    ) -> None:
        if not shard_paths:
            raise ValueError("at least one shard path is required")
        self.shard_paths = shard_paths
        self.sequence_length = sequence_length
        self.batch_size = batch_size
        self.seed = seed
        self.shuffle = shuffle
        if prefetch_batches < 0 or prefetch_batches > 128:
            raise ValueError("prefetch_batches must be between 0 and 128")
        self.prefetch_batches = prefetch_batches

    def _batches(self, *, epoch: int = 0) -> Iterator[list[list[int]]]:
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

    def batches(self, *, epoch: int = 0) -> Iterator[list[list[int]]]:
        if self.prefetch_batches == 0:
            yield from self._batches(epoch=epoch)
            return

        sentinel = object()
        buffered: queue.Queue[object] = queue.Queue(maxsize=self.prefetch_batches)
        stop = threading.Event()

        def put(item: object) -> bool:
            while not stop.is_set():
                try:
                    buffered.put(item, timeout=0.25)
                    return True
                except queue.Full:
                    continue
            return False

        def produce() -> None:
            try:
                for batch in self._batches(epoch=epoch):
                    if not put(batch):
                        return
            except BaseException as exc:  # propagated to the training thread
                put(exc)
            finally:
                put(sentinel)

        worker = threading.Thread(target=produce, name=f"aeitron-shard-prefetch-{epoch}", daemon=True)
        worker.start()
        try:
            while True:
                item = buffered.get()
                if item is sentinel:
                    break
                if isinstance(item, BaseException):
                    raise RuntimeError("token shard prefetch failed") from item
                yield item  # type: ignore[misc]
        finally:
            stop.set()
            worker.join(timeout=1.0)


def count_batches(shard_paths: list[str], *, sequence_length: int, batch_size: int) -> int:
    total_tokens = sum(len(read_uint32_tokens(path)) for path in shard_paths)
    return total_tokens // (sequence_length * batch_size)

