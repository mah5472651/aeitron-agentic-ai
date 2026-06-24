#!/usr/bin/env python
"""Tokenizer loading layer for Phase 11 local and future trained models."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol


class TokenizerAdapter(Protocol):
    name: str
    vocab_size: int
    eos_token_id: int

    def encode(self, text: str) -> list[int]:
        raise NotImplementedError

    def decode(self, ids: list[int]) -> str:
        raise NotImplementedError


class SimpleCharTokenizer:
    """Tiny deterministic fallback tokenizer for untrained CPU smoke tests."""

    name = "simple-char"

    def __init__(self, vocab_size: int = 256) -> None:
        if vocab_size < 2:
            raise ValueError("vocab_size must be >= 2")
        self.vocab_size = vocab_size
        self.eos_token_id = 0

    def encode(self, text: str) -> list[int]:
        return [min(ord(char), self.vocab_size - 1) for char in text]

    def decode(self, ids: list[int]) -> str:
        chars: list[str] = []
        for token_id in ids:
            value = int(token_id)
            if value == self.eos_token_id:
                continue
            chars.append(chr(max(0, min(255, value))))
        return "".join(chars)


class HFTokenizerAdapter:
    """Adapter around Hugging Face's raw tokenizers engine.

    This intentionally imports `tokenizers` lazily, so local API work and smoke
    tests still run on machines where the final training tokenizer is not built.
    """

    name = "huggingface-tokenizers"

    def __init__(self, tokenizer_path: str | Path) -> None:
        try:
            from tokenizers import Tokenizer
        except ImportError as exc:  # pragma: no cover - depends on optional package
            raise RuntimeError("Install `tokenizers` to load a Hugging Face tokenizer artifact.") from exc

        path = Path(tokenizer_path)
        if not path.exists() or not path.is_file():
            raise FileNotFoundError(f"tokenizer file does not exist: {path}")
        self.path = path
        self.tokenizer = Tokenizer.from_file(str(path))
        self.vocab_size = int(self.tokenizer.get_vocab_size())
        self.eos_token_id = self._detect_eos_id()

    def _detect_eos_id(self) -> int:
        for token in ("<|endoftext|>", "<|eos|>", "</s>", "[EOS]", "<eos>"):
            token_id = self.tokenizer.token_to_id(token)
            if token_id is not None:
                return int(token_id)
        return 0

    def encode(self, text: str) -> list[int]:
        return list(self.tokenizer.encode(text).ids)

    def decode(self, ids: list[int]) -> str:
        return self.tokenizer.decode([int(token_id) for token_id in ids], skip_special_tokens=False)


def load_tokenizer(tokenizer_path: str | Path | None = None, *, fallback_vocab_size: int = 256) -> TokenizerAdapter:
    if tokenizer_path:
        path = Path(tokenizer_path)
        if path.exists():
            return HFTokenizerAdapter(path)
    return SimpleCharTokenizer(vocab_size=fallback_vocab_size)
