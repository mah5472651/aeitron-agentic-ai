"""Exact and near-duplicate removal for large JSONL corpora."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
from pathlib import Path

from pydantic import Field

from src.aeitron.learning.quality import iter_jsonl, stable_hash
from src.aeitron.shared.schemas import StrictModel


TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|0x[0-9A-Fa-f]+|\d+")


class NearDedupReport(StrictModel):
    input_paths: list[str]
    output_path: str
    accepted: int
    exact_duplicates: int
    near_duplicates: int
    hamming_threshold: int
    created_at_unix: float = Field(default_factory=time.time)


def _fingerprint_tokens(text: str) -> list[str]:
    tokens = TOKEN_RE.findall(text.lower())
    if not tokens:
        return text.lower().split()
    return tokens


def simhash64(text: str) -> int:
    weights = [0] * 64
    for token in _fingerprint_tokens(text):
        digest = int.from_bytes(hashlib.blake2b(token.encode("utf-8", "replace"), digest_size=8).digest(), "big")
        for bit in range(64):
            weights[bit] += 1 if digest & (1 << bit) else -1
    value = 0
    for bit, weight in enumerate(weights):
        if weight >= 0:
            value |= 1 << bit
    return value


def hamming_distance(left: int, right: int) -> int:
    return (left ^ right).bit_count()


def _is_near_duplicate(value: int, existing: list[int], threshold: int) -> bool:
    return any(hamming_distance(value, old) <= threshold for old in existing)


def deduplicate_jsonl(
    input_paths: list[str | Path],
    output_path: str | Path,
    *,
    hamming_threshold: int = 3,
) -> NearDedupReport:
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    exact_hashes: set[str] = set()
    simhashes: list[int] = []
    accepted = 0
    exact_duplicates = 0
    near_duplicates = 0
    with target.open("w", encoding="utf-8") as handle:
        for path in input_paths:
            for row in iter_jsonl(path):
                text = str(row.get("text") or row.get("content") or "")
                exact = str(row.get("content_hash") or stable_hash(text))
                if exact in exact_hashes:
                    exact_duplicates += 1
                    continue
                value = simhash64(text)
                if _is_near_duplicate(value, simhashes, hamming_threshold):
                    near_duplicates += 1
                    continue
                exact_hashes.add(exact)
                simhashes.append(value)
                row["content_hash"] = exact
                handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
                accepted += 1
    return NearDedupReport(
        input_paths=[str(path) for path in input_paths],
        output_path=str(target),
        accepted=accepted,
        exact_duplicates=exact_duplicates,
        near_duplicates=near_duplicates,
        hamming_threshold=hamming_threshold,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Remove exact and near duplicates from Aeitron JSONL shards.")
    parser.add_argument("--input", nargs="+", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--hamming-threshold", type=int, default=3)
    args = parser.parse_args()
    report = deduplicate_jsonl(args.input, args.output, hamming_threshold=args.hamming_threshold)
    print(json.dumps(report.model_dump(), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

