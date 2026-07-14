"""Tokenizer training and token sharding pipeline for Aeitron scratch pretraining."""

from __future__ import annotations

import argparse
import json
import math
import random
import struct
import time
from pathlib import Path
from typing import Any, Iterable

from pydantic import Field

from src.aeitron.shared.schemas import StrictModel


SPECIAL_TOKENS = [
    "<|thought_start|>",
    "<|thought_end|>",
    "<|call_graph_root|>",
    "<|patch_start|>",
    "<|patch_end|>",
    "<|compile_error|>",
    "<|exploit_success|>",
    "<|tool_call|>",
    "<|tool_result|>",
    "<|heap_alloc|>",
    "<|heap_free|>",
    "<|stack_frame|>",
    "<|memory_address|>",
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


TOKENIZER_STRESS_TEXT = """
<|thought_start|> inspect call graph <|thought_end|>
<|call_graph_root|> auth.validate -> db.query -> cache.set
<|compile_error|> gcc error: undefined reference at 0x7ffd00ff
<|heap_alloc|> malloc calloc realloc free <|heap_free|>
0x00 0x01 0x02 0x03 0x7f 0x80 0xff 0xdeadbeef 0x7ffd00ff
def python_block():
  two_space_indent = True
    four_space_indent = True
        eight_space_indent = True
fn rust_block() { println!("hello"); }
int main(void) { char buf[16]; return 0; }
#!/usr/bin/env bash
set -euo pipefail
"""


class RealCorpusTokenizerConfig(StrictModel):
    input_paths: list[str]
    output_dir: str
    dataset_id: str = "aeitron-real-corpus"
    vocab_size: int = Field(default=64_000, ge=1_000)
    min_frequency: int = Field(default=2, ge=1)
    shard_token_count: int = Field(default=1_000_000, ge=128)
    sequence_length: int = Field(default=2048, ge=16)
    validation_fraction: float = Field(default=0.01, ge=0.0, le=0.5)
    include_stress_samples: bool = True


class TokenizerAuditReport(StrictModel):
    status: str
    tokenizer_path: str
    shard_manifest_path: str
    vocab_size_requested: int
    vocab_size_actual: int
    special_tokens_missing: list[str]
    sample_token_counts: dict[str, int]
    source_rows: int
    source_chars: int
    shard_manifest: dict[str, Any]
    created_at_unix: float = Field(default_factory=time.time)


def iter_texts(paths: list[str | Path]) -> Iterable[str]:
    for path in paths:
        source = Path(path)
        if source.suffix == ".jsonl":
            with source.open("r", encoding="utf-8-sig", errors="replace") as handle:
                for line_number, line in enumerate(handle, start=1):
                    if not line.strip():
                        continue
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError as exc:
                        snippet = line[:240].replace("\n", "\\n")
                        raise ValueError(
                            f"invalid JSONL in {source} at line {line_number}: {exc.msg}; snippet={snippet!r}"
                        ) from exc
                    text = str(row.get("text") or row.get("content") or row.get("prompt") or "")
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
    dataset_id: str = "aeitron-corpus",
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


def corpus_stats(paths: list[str | Path]) -> tuple[int, int]:
    rows = 0
    chars = 0
    for path in paths:
        source = Path(path)
        if source.suffix == ".jsonl":
            with source.open("r", encoding="utf-8-sig", errors="replace") as handle:
                for line_number, line in enumerate(handle, start=1):
                    if not line.strip():
                        continue
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError as exc:
                        snippet = line[:240].replace("\n", "\\n")
                        raise ValueError(
                            f"invalid JSONL in {source} at line {line_number}: {exc.msg}; snippet={snippet!r}"
                        ) from exc
                    text = str(row.get("text") or row.get("content") or row.get("prompt") or "")
                    if text:
                        rows += 1
                        chars += len(text)
        else:
            text = source.read_text(encoding="utf-8", errors="replace")
            rows += 1
            chars += len(text)
    return rows, chars


def write_tokenizer_stress_file(root: str | Path) -> Path:
    target = Path(root) / "tokenizer_stress_samples.jsonl"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps({"text": TOKENIZER_STRESS_TEXT}, sort_keys=True) + "\n", encoding="utf-8")
    return target


def train_real_corpus_tokenizer(config: RealCorpusTokenizerConfig) -> TokenizerAuditReport:
    root = Path(config.output_dir)
    root.mkdir(parents=True, exist_ok=True)
    tokenizer_path = root / "tokenizer" / "tokenizer.json"
    shards_dir = root / "shards"
    input_paths: list[str | Path] = [Path(path) for path in config.input_paths]
    if config.include_stress_samples:
        input_paths.append(write_tokenizer_stress_file(root))

    trained = train_bpe_tokenizer(
        input_paths,
        tokenizer_path,
        TokenizerTrainConfig(
            vocab_size=config.vocab_size,
            min_frequency=config.min_frequency,
            special_tokens=SPECIAL_TOKENS,
        ),
    )
    manifest = build_token_shards(
        input_paths=input_paths,
        tokenizer_path=trained,
        output_dir=shards_dir,
        config=ShardBuildConfig(
            shard_token_count=config.shard_token_count,
            sequence_length=config.sequence_length,
            validation_fraction=config.validation_fraction,
        ),
        dataset_id=config.dataset_id,
    )
    tokenizer = load_tokenizer(trained)
    vocab = tokenizer.get_vocab()
    sample_texts = {
        "four_space_indent": "    if safe:\n        return value\n",
        "hex_dump": "0x00 0x7ffd00ff 0xdeadbeef 0xff",
        "compile_error": "<|compile_error|> undefined reference to main",
        "python_function": "def validate(value: str) -> str:\n    return value.strip()\n",
    }
    source_rows, source_chars = corpus_stats(config.input_paths)
    report = TokenizerAuditReport(
        status="passed" if not [token for token in SPECIAL_TOKENS if token not in vocab] else "failed",
        tokenizer_path=str(trained),
        shard_manifest_path=str(shards_dir / "manifest.json"),
        vocab_size_requested=config.vocab_size,
        vocab_size_actual=tokenizer.get_vocab_size(),
        special_tokens_missing=[token for token in SPECIAL_TOKENS if token not in vocab],
        sample_token_counts={name: len(tokenizer.encode(text).ids) for name, text in sample_texts.items()},
        source_rows=source_rows,
        source_chars=source_chars,
        shard_manifest=manifest.model_dump(),
    )
    (root / "tokenizer_audit_report.json").write_text(
        json.dumps(report.model_dump(), indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Train Aeitron tokenizer and build token shards.")
    parser.add_argument("--input", nargs="+", required=True)
    parser.add_argument("--tokenizer-out")
    parser.add_argument("--shards-out")
    parser.add_argument("--output-dir", help="Use with --real-corpus-audit to write tokenizer, shards, and audit report.")
    parser.add_argument("--dataset-id", default="aeitron-corpus")
    parser.add_argument("--vocab-size", type=int, default=64_000)
    parser.add_argument("--min-frequency", type=int, default=2)
    parser.add_argument("--shard-token-count", type=int, default=1_000_000)
    parser.add_argument("--sequence-length", type=int, default=2048)
    parser.add_argument("--validation-fraction", type=float, default=0.01)
    parser.add_argument("--real-corpus-audit", action="store_true")
    parser.add_argument("--no-stress-samples", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.real_corpus_audit:
        if not args.output_dir:
            raise SystemExit("--output-dir is required with --real-corpus-audit")
        report = train_real_corpus_tokenizer(
            RealCorpusTokenizerConfig(
                input_paths=[str(path) for path in args.input],
                output_dir=args.output_dir,
                dataset_id=args.dataset_id,
                vocab_size=args.vocab_size,
                min_frequency=args.min_frequency,
                shard_token_count=args.shard_token_count,
                sequence_length=args.sequence_length,
                validation_fraction=args.validation_fraction,
                include_stress_samples=not args.no_stress_samples,
            )
        )
        print(json.dumps(report.model_dump(), indent=2, sort_keys=True))
        if report.status != "passed":
            raise SystemExit(2)
        return
    if not args.tokenizer_out or not args.shards_out:
        raise SystemExit("--tokenizer-out and --shards-out are required unless --real-corpus-audit is used")
    tokenizer_path = train_bpe_tokenizer(
        args.input,
        args.tokenizer_out,
        TokenizerTrainConfig(vocab_size=args.vocab_size, min_frequency=args.min_frequency),
    )
    manifest = build_token_shards(
        input_paths=args.input,
        tokenizer_path=tokenizer_path,
        output_dir=args.shards_out,
        config=ShardBuildConfig(
            shard_token_count=args.shard_token_count,
            sequence_length=args.sequence_length,
            validation_fraction=args.validation_fraction,
        ),
        dataset_id=args.dataset_id,
    )
    print(json.dumps(manifest.model_dump(), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

