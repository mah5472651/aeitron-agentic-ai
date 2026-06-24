#!/usr/bin/env python
"""Custom 64k code-optimized BPE tokenizer trainer.

This script uses Hugging Face's low-level `tokenizers` engine directly. It is
optimized for raw source code, AST/call-graph metadata, terminal logs, compiler
errors, exploit traces, hex dumps, memory addresses, and indentation-heavy
programs.

The default and enforced target vocabulary size is exactly 64,000 tokens.
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
from pathlib import Path
from typing import Iterable, Iterator

from tokenizers import Tokenizer, decoders, models, normalizers, pre_tokenizers, trainers


EXACT_VOCAB_SIZE = 64_000

CODE_AND_LOG_EXTENSIONS = {
    ".py",
    ".pyw",
    ".c",
    ".h",
    ".cc",
    ".cpp",
    ".cxx",
    ".hh",
    ".hpp",
    ".hxx",
    ".rs",
    ".sh",
    ".bash",
    ".zsh",
    ".toml",
    ".yaml",
    ".yml",
    ".json",
    ".jsonl",
    ".md",
    ".txt",
    ".log",
    ".stderr",
    ".stdout",
    ".trace",
    ".diff",
    ".patch",
    ".mmd",
}

DEFAULT_EXCLUDE_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    "env",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    "node_modules",
    "target",
    "build",
    "dist",
    ".tox",
}

CORE_SPECIAL_TOKENS = [
    "[PAD]",
    "[UNK]",
    "[BOS]",
    "[EOS]",
    "[MASK]",
    "<|repo|>",
    "<|file|>",
    "<|path|>",
    "<|code|>",
    "<|stdout|>",
    "<|stderr|>",
    "<|traceback|>",
    "<|patch|>",
    "<|cmd|>",
    "<|thought_start|>",
    "<|thought_end|>",
    "<|call_graph_root|>",
    "<|compile_error|>",
    "<|exploit_success|>",
]

MEMORY_SPECIAL_TOKENS = [
    "<|heap_alloc|>",
    "<|heap_free|>",
    "<|heap_realloc|>",
    "<|heap_new|>",
    "<|heap_delete|>",
    "<|stack_alloc|>",
    "<|addr32|>",
    "<|addr64|>",
    "<|hex_dump|>",
    "<|malloc|>",
    "<|calloc|>",
    "<|realloc|>",
    "<|free|>",
    "<|memcpy|>",
    "<|memmove|>",
    "<|use_after_free|>",
    "<|double_free|>",
    "<|heap_overflow|>",
]

HEX_BYTE_TOKENS = [f"0x{value:02x}" for value in range(256)] + [
    f"0x{value:02X}" for value in range(256)
]

HEAP_BYTE_MARKERS = [f"<|heap_byte_{value:02x}|>" for value in range(256)]

SPECIAL_TOKENS = (
    CORE_SPECIAL_TOKENS
    + MEMORY_SPECIAL_TOKENS
    + HEX_BYTE_TOKENS
    + HEAP_BYTE_MARKERS
)


def normalize_newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def read_text(path: Path, max_file_mb: float) -> str | None:
    try:
        if path.stat().st_size > max_file_mb * 1024 * 1024:
            return None
        raw = path.read_bytes()
    except OSError:
        return None
    try:
        return normalize_newlines(raw.decode("utf-8"))
    except UnicodeDecodeError:
        return normalize_newlines(raw.decode("utf-8", errors="replace"))


def iter_corpus_files(
    inputs: list[Path],
    exclude_dirs: set[str],
    max_file_mb: float,
) -> Iterator[Path]:
    for input_path in inputs:
        if input_path.is_file():
            try:
                if (
                    input_path.suffix.lower() in CODE_AND_LOG_EXTENSIONS
                    and input_path.stat().st_size <= max_file_mb * 1024 * 1024
                ):
                    yield input_path
            except OSError:
                continue
            continue
        for path in input_path.rglob("*"):
            if not path.is_file():
                continue
            if any(part in exclude_dirs for part in path.parts):
                continue
            if path.suffix.lower() not in CODE_AND_LOG_EXTENSIONS:
                continue
            try:
                if path.stat().st_size <= max_file_mb * 1024 * 1024:
                    yield path
            except OSError:
                continue


def chunk_text(text: str, chunk_chars: int) -> Iterator[str]:
    if len(text) <= chunk_chars:
        yield text
        return
    start = 0
    while start < len(text):
        end = min(len(text), start + chunk_chars)
        newline = text.rfind("\n", start, end)
        if newline > start + chunk_chars // 2:
            end = newline + 1
        yield text[start:end]
        start = end


def indentation_seed_samples(repetitions: int) -> Iterator[str]:
    blocks = [2, 4, 8, 12, 16, 24, 32, 40, 48, 64]
    templates = [
        "if condition:\n{sp}return value\n",
        "for item in items:\n{sp}if item:\n{sp2}yield item\n",
        "fn compute(value: usize) -> usize {{\n{sp}let next = value + 1;\n{sp}next\n}}\n",
        "switch (tag) {{\n{sp}case 1:\n{sp2}return handle(tag);\n}}\n",
        "case \"$target\" in\n{sp}*) echo \"$target\" ;;\nesac\n",
    ]
    for _ in range(repetitions):
        for width in blocks:
            sp = " " * width
            sp2 = " " * (width * 2)
            for template in templates:
                yield template.format(sp=sp, sp2=sp2)


def hex_seed_samples(repetitions: int) -> Iterator[str]:
    lower_dump = " ".join(f"0x{value:02x}" for value in range(256))
    upper_dump = " ".join(f"0x{value:02X}" for value in range(256))
    compact_dump = "".join(f"{value:02x}" for value in range(256))
    addresses = [
        "0x00000000",
        "0x0000000000000000",
        "0x7ffdbeefcafe",
        "0x7fffffffdc20",
        "0xffff88801234abcd",
        "0xDEADBEEF",
        "0xCAFEBABE",
        "0x4141414141414141",
    ]
    for _ in range(repetitions):
        yield f"<|hex_dump|>\n{lower_dump}\n{upper_dump}\n{compact_dump}\n"
        for address in addresses:
            yield f"ptr={address} base={address}+0x10 rip={address} rbp={address}\n"
        yield " ".join(addresses) + "\n"


def memory_seed_samples(repetitions: int) -> Iterator[str]:
    samples = [
        "<|heap_alloc|> void *p = malloc(0x40); memset(p, 0x00, 0x40);\n",
        "<|heap_free|> free(p); p = NULL;\n",
        "<|heap_realloc|> p = realloc(p, 0x80);\n",
        "<|heap_new|> auto *node = new Node(value);\n<|heap_delete|> delete node;\n",
        "<|malloc|> chunk = malloc(size); <|free|> free(chunk);\n",
        "<|calloc|> buf = calloc(count, sizeof(uint8_t));\n",
        "<|memcpy|> memcpy(dst, src, 0x100); <|heap_overflow|>\n",
        "<|use_after_free|> read_after_free(ptr); <|double_free|> free(ptr); free(ptr);\n",
        "asan: heap-use-after-free on address 0x603000000040 at pc 0x000000401234\n",
        "==1234==ERROR: AddressSanitizer: heap-buffer-overflow on address 0x6020000000ff\n",
    ]
    marker_line = " ".join(HEAP_BYTE_MARKERS[:64])
    for _ in range(repetitions):
        yield from samples
        yield marker_line + "\n"


def domain_seed_samples(repetitions: int) -> Iterator[str]:
    yield from indentation_seed_samples(repetitions)
    yield from hex_seed_samples(repetitions)
    yield from memory_seed_samples(repetitions)
    control_sequence = " ".join(CORE_SPECIAL_TOKENS + MEMORY_SPECIAL_TOKENS)
    for _ in range(max(1, repetitions // 4)):
        yield f"{control_sequence}\n"
        yield "<|thought_start|> inspect call graph <|call_graph_root|> patch <|thought_end|>\n"
        yield "<|compile_error|> main.c:10:5: error: expected ';' before 'return'\n"
        yield "<|exploit_success|> payload reached controlled RIP 0x4141414141414141\n"


def corpus_iterator(
    files: Iterable[Path],
    chunk_chars: int,
    max_file_mb: float,
    seed_repetitions: int,
) -> Iterator[str]:
    if seed_repetitions > 0:
        yield from domain_seed_samples(seed_repetitions)
    for path in files:
        text = read_text(path, max_file_mb)
        if text is None:
            continue
        path_header = f"<|file|>{path.as_posix()}\n<|code|>\n"
        for chunk in chunk_text(path_header + text, chunk_chars):
            yield chunk


def build_tokenizer() -> Tokenizer:
    model = models.BPE(
        unk_token="[UNK]",  # nosec B106
        byte_fallback=True,
        fuse_unk=False,
        ignore_merges=False,
    )
    tokenizer = Tokenizer(model)
    tokenizer.normalizer = normalizers.Sequence(
        [
            normalizers.Replace("\r\n", "\n"),
            normalizers.Replace("\r", "\n"),
        ]
    )
    tokenizer.pre_tokenizer = pre_tokenizers.ByteLevel(add_prefix_space=False, use_regex=True)
    tokenizer.decoder = decoders.ByteLevel()
    return tokenizer


def train_tokenizer(args: argparse.Namespace) -> None:
    if args.vocab_size != EXACT_VOCAB_SIZE:
        raise SystemExit(f"This pipeline is fixed to exactly {EXACT_VOCAB_SIZE} tokens.")

    inputs = [path.resolve() for path in args.input]
    missing = [str(path) for path in inputs if not path.exists()]
    if missing:
        raise SystemExit(f"Input path(s) do not exist: {', '.join(missing)}")

    exclude_dirs = DEFAULT_EXCLUDE_DIRS | set(args.exclude_dir)
    files = list(iter_corpus_files(inputs, exclude_dirs, args.max_file_mb))
    if not files:
        raise SystemExit("No corpus files found. Add source/log files or adjust extensions/excludes.")
    if args.shuffle:
        random.Random(args.seed).shuffle(files)  # nosec B311

    tokenizer = build_tokenizer()
    trainer = trainers.BpeTrainer(
        vocab_size=EXACT_VOCAB_SIZE,
        min_frequency=args.min_frequency,
        show_progress=True,
        special_tokens=SPECIAL_TOKENS,
        initial_alphabet=pre_tokenizers.ByteLevel.alphabet(),
        continuing_subword_prefix="",
        end_of_word_suffix="",
    )
    iterator = corpus_iterator(
        files=files,
        chunk_chars=args.chunk_chars,
        max_file_mb=args.max_file_mb,
        seed_repetitions=args.seed_repetitions,
    )
    tokenizer.train_from_iterator(iterator, trainer=trainer, length=len(files) + args.seed_repetitions)
    ensure_exact_vocab_size(tokenizer, EXACT_VOCAB_SIZE)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    tokenizer_path = args.output_dir / "tokenizer.json"
    tokenizer.save(str(tokenizer_path))
    metadata = build_metadata(args, files, tokenizer)
    (args.output_dir / "tokenizer_metadata.json").write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    write_token_audit(args.output_dir / "token_audit.json", tokenizer)
    print(f"tokenizer written: {tokenizer_path}")
    print(f"vocab size: {tokenizer.get_vocab_size(with_added_tokens=True)}")
    print(f"files seen: {len(files)}")


def ensure_exact_vocab_size(tokenizer: Tokenizer, expected_size: int) -> None:
    current = tokenizer.get_vocab_size(with_added_tokens=True)
    if current > expected_size:
        raise SystemExit(f"Tokenizer vocab exceeded {expected_size}: {current}")
    if current < expected_size:
        needed = expected_size - current
        reserved = [f"<|reserved_{index:05d}|>" for index in range(needed)]
        tokenizer.add_special_tokens(reserved)
    final_size = tokenizer.get_vocab_size(with_added_tokens=True)
    if final_size != expected_size:
        raise SystemExit(f"Tokenizer vocab is {final_size}, expected exactly {expected_size}")


def build_metadata(args: argparse.Namespace, files: list[Path], tokenizer: Tokenizer) -> dict[str, object]:
    return {
        "schema_version": "phase1.code_bpe_tokenizer.v2",
        "vocab_size": tokenizer.get_vocab_size(with_added_tokens=True),
        "target_vocab_size": EXACT_VOCAB_SIZE,
        "min_frequency": args.min_frequency,
        "files_seen": len(files),
        "seed_repetitions": args.seed_repetitions,
        "pre_tokenizer": "ByteLevel(add_prefix_space=False, use_regex=True)",
        "normalization": "CRLF/CR to LF only; case, spacing, and syntax preserved",
        "core_special_tokens": CORE_SPECIAL_TOKENS,
        "memory_special_tokens": MEMORY_SPECIAL_TOKENS,
        "hex_byte_tokens": {
            "count": len(HEX_BYTE_TOKENS),
            "examples": HEX_BYTE_TOKENS[:8] + HEX_BYTE_TOKENS[-8:],
        },
        "heap_byte_markers": {
            "count": len(HEAP_BYTE_MARKERS),
            "examples": HEAP_BYTE_MARKERS[:8],
        },
        "extensions": sorted(CODE_AND_LOG_EXTENSIONS),
        "optimization_notes": [
            "2/4/8+ space indentation samples are repeatedly injected before training.",
            "0x00..0xff literals are added as tokenizer special tokens to prevent byte-fragment splits.",
            "Memory allocation/free/realloc and sanitizer trace samples bias BPE merges toward heap workflows.",
            "64-bit and kernel-style address samples bias merges toward compact memory address encoding.",
            "Reserved tokens are added only if a small corpus cannot naturally fill the 64k target.",
        ],
    }


def write_token_audit(path: Path, tokenizer: Tokenizer) -> None:
    examples = [
        "        deeply_indented_call(0x7fffffffdc20, 0x00, 0xff)",
        "<|thought_start|> inspect <|call_graph_root|> <|thought_end|>",
        "<|compile_error|> error[E0382]: borrow of moved value",
        "<|heap_alloc|> p = malloc(0x40); <|heap_free|> free(p);",
        "0x00 0x01 0x02 0x7f 0x80 0xff 0x4141414141414141",
    ]
    payload = []
    for text in examples:
        encoding = tokenizer.encode(text)
        payload.append(
            {
                "text": text,
                "tokens": encoding.tokens,
                "ids": encoding.ids,
                "token_count": len(encoding.ids),
            }
        )
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def load_tokenizer(path: Path) -> Tokenizer:
    if not path.exists():
        raise SystemExit(f"Tokenizer does not exist: {path}")
    return Tokenizer.from_file(str(path))


def read_cli_text(args: argparse.Namespace) -> str:
    if args.text is not None:
        return args.text
    if args.file is not None:
        text = read_text(args.file, args.max_file_mb)
        if text is None:
            raise SystemExit(f"File is larger than max-file-mb: {args.file}")
        return text
    raise SystemExit("Provide --text or --file.")


def encode_text(args: argparse.Namespace) -> None:
    tokenizer = load_tokenizer(args.tokenizer)
    text = read_cli_text(args)
    encoding = tokenizer.encode(text)
    payload = {
        "ids": encoding.ids,
        "tokens": encoding.tokens,
        "token_count": len(encoding.ids),
        "char_count": len(text),
    }
    print(json.dumps(payload, indent=2, ensure_ascii=False))


def decode_ids(args: argparse.Namespace) -> None:
    tokenizer = load_tokenizer(args.tokenizer)
    ids = [int(item.strip()) for item in re.split(r"[,\s]+", args.ids.strip()) if item.strip()]
    print(tokenizer.decode(ids, skip_special_tokens=False))


def benchmark_tokenizer(args: argparse.Namespace) -> None:
    tokenizer = load_tokenizer(args.tokenizer)
    inputs = [path.resolve() for path in args.input]
    exclude_dirs = DEFAULT_EXCLUDE_DIRS | set(args.exclude_dir)
    files = list(iter_corpus_files(inputs, exclude_dirs, args.max_file_mb))
    total_chars = 0
    total_tokens = 0
    sampled_files = 0
    hex_fragments = 0

    for path in files[: args.limit]:
        text = read_text(path, args.max_file_mb)
        if text is None:
            continue
        encoding = tokenizer.encode(text)
        total_chars += len(text)
        total_tokens += len(encoding.ids)
        sampled_files += 1
        hex_fragments += count_hex_fragmentation(tokenizer, text)

    if sampled_files == 0:
        raise SystemExit("No files benchmarked.")
    chars_per_token = total_chars / max(total_tokens, 1)
    payload = {
        "files": sampled_files,
        "chars": total_chars,
        "tokens": total_tokens,
        "chars_per_token": round(chars_per_token, 4),
        "hex_fragmentation_events": hex_fragments,
    }
    print(json.dumps(payload, indent=2))


def count_hex_fragmentation(tokenizer: Tokenizer, text: str) -> int:
    events = 0
    for match in re.finditer(r"0x[0-9a-fA-F]{2,16}", text):
        tokens = tokenizer.encode(match.group(0)).tokens
        if len(tokens) > 4 and len(match.group(0)) <= 6:
            events += 1
    return events


def add_common_io_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--max-file-mb", type=float, default=32.0)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Train and inspect a 64k code-aware BPE tokenizer.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    train = subparsers.add_parser("train", help="Train tokenizer from source/log corpus.")
    train.add_argument("--input", required=True, nargs="+", type=Path, help="Input repo/corpus path(s).")
    train.add_argument("--output-dir", required=True, type=Path)
    train.add_argument("--vocab-size", type=int, default=EXACT_VOCAB_SIZE)
    train.add_argument("--min-frequency", type=int, default=2)
    train.add_argument("--chunk-chars", type=int, default=262_144)
    train.add_argument("--seed-repetitions", type=int, default=128)
    train.add_argument("--exclude-dir", action="append", default=[])
    train.add_argument("--shuffle", action="store_true")
    train.add_argument("--seed", type=int, default=1337)
    add_common_io_args(train)
    train.set_defaults(func=train_tokenizer)

    encode = subparsers.add_parser("encode", help="Encode text or a file.")
    encode.add_argument("--tokenizer", required=True, type=Path)
    encode.add_argument("--text")
    encode.add_argument("--file", type=Path)
    add_common_io_args(encode)
    encode.set_defaults(func=encode_text)

    decode = subparsers.add_parser("decode", help="Decode comma/space separated token ids.")
    decode.add_argument("--tokenizer", required=True, type=Path)
    decode.add_argument("--ids", required=True)
    decode.set_defaults(func=decode_ids)

    benchmark = subparsers.add_parser("benchmark", help="Measure chars-per-token on a corpus.")
    benchmark.add_argument("--tokenizer", required=True, type=Path)
    benchmark.add_argument("--input", required=True, nargs="+", type=Path)
    benchmark.add_argument("--exclude-dir", action="append", default=[])
    benchmark.add_argument("--limit", type=int, default=1000)
    add_common_io_args(benchmark)
    benchmark.set_defaults(func=benchmark_tokenizer)
    return parser.parse_args()


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
    args = parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
