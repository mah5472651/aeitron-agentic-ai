"""Production tokenizer training wrapper for real Mythos corpora."""

from __future__ import annotations

import argparse
import json
import tempfile
import time
from pathlib import Path
from typing import Any

from pydantic import Field

from src.mythos.learning.quality import iter_jsonl
from src.mythos.model_ops.tokenizer_pipeline import (
    SPECIAL_TOKENS,
    ShardBuildConfig,
    ShardManifest,
    TokenizerTrainConfig,
    build_token_shards,
    load_tokenizer,
    train_bpe_tokenizer,
)
from src.mythos.shared.schemas import StrictModel


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
    dataset_id: str = "mythos-real-corpus"
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


def _corpus_stats(paths: list[str | Path]) -> tuple[int, int]:
    rows = 0
    chars = 0
    for path in paths:
        source = Path(path)
        if source.suffix == ".jsonl":
            for row in iter_jsonl(source):
                text = str(row.get("text") or row.get("content") or row.get("prompt") or "")
                if text:
                    rows += 1
                    chars += len(text)
        else:
            text = source.read_text(encoding="utf-8", errors="replace")
            rows += 1
            chars += len(text)
    return rows, chars


def _write_stress_file(root: Path) -> Path:
    target = root / "tokenizer_stress_samples.jsonl"
    target.write_text(json.dumps({"text": TOKENIZER_STRESS_TEXT}, sort_keys=True) + "\n", encoding="utf-8")
    return target


def train_real_corpus_tokenizer(config: RealCorpusTokenizerConfig) -> TokenizerAuditReport:
    root = Path(config.output_dir)
    root.mkdir(parents=True, exist_ok=True)
    tokenizer_path = root / "tokenizer" / "tokenizer.json"
    shards_dir = root / "shards"
    input_paths: list[str | Path] = [Path(path) for path in config.input_paths]
    if config.include_stress_samples:
        input_paths.append(_write_stress_file(root))

    trained = train_bpe_tokenizer(
        input_paths,
        tokenizer_path,
        TokenizerTrainConfig(
            vocab_size=config.vocab_size,
            min_frequency=config.min_frequency,
            special_tokens=SPECIAL_TOKENS,
        ),
    )
    manifest: ShardManifest = build_token_shards(
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
    source_rows, source_chars = _corpus_stats(config.input_paths)
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


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Train and audit a Mythos tokenizer on real corpus files.")
    parser.add_argument("--input", nargs="+", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--dataset-id", default="mythos-real-corpus")
    parser.add_argument("--vocab-size", type=int, default=64_000)
    parser.add_argument("--min-frequency", type=int, default=2)
    parser.add_argument("--shard-token-count", type=int, default=1_000_000)
    parser.add_argument("--sequence-length", type=int, default=2048)
    parser.add_argument("--validation-fraction", type=float, default=0.01)
    parser.add_argument("--no-stress-samples", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
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


if __name__ == "__main__":
    main()
