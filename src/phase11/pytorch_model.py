#!/usr/bin/env python
"""Small PyTorch-native decoder-only transformer foundation.

This is not the final 7B-13B model. It is the local, controllable architecture
shell: tokenizer-compatible checkpoint format, generation loop, and clean module
boundaries that later scale to larger training runs.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path

import torch
import torch.nn as nn
import torch.nn.functional as F


@dataclass(frozen=True)
class DecoderConfig:
    vocab_size: int = 64000
    max_seq_len: int = 8192
    d_model: int = 512
    n_layers: int = 8
    n_heads: int = 8
    d_ff: int = 2048
    dropout: float = 0.0
    tie_embeddings: bool = True
    pad_token_id: int = 0
    eos_token_id: int = 0

    def validate(self) -> None:
        if self.vocab_size <= 0:
            raise ValueError("vocab_size must be positive")
        if self.max_seq_len <= 0:
            raise ValueError("max_seq_len must be positive")
        if self.d_model % self.n_heads != 0:
            raise ValueError("d_model must be divisible by n_heads")
        if self.n_layers <= 0 or self.n_heads <= 0 or self.d_ff <= 0:
            raise ValueError("n_layers, n_heads, and d_ff must be positive")


class TransformerBlock(nn.Module):
    def __init__(self, config: DecoderConfig) -> None:
        super().__init__()
        self.attn_norm = nn.LayerNorm(config.d_model)
        self.ffn_norm = nn.LayerNorm(config.d_model)
        self.attn = nn.MultiheadAttention(
            embed_dim=config.d_model,
            num_heads=config.n_heads,
            dropout=config.dropout,
            batch_first=True,
        )
        self.ffn = nn.Sequential(
            nn.Linear(config.d_model, config.d_ff),
            nn.GELU(),
            nn.Linear(config.d_ff, config.d_model),
            nn.Dropout(config.dropout),
        )
        self.dropout = nn.Dropout(config.dropout)

    def forward(self, hidden: torch.Tensor, causal_mask: torch.Tensor) -> torch.Tensor:
        attn_input = self.attn_norm(hidden)
        attn_output, _ = self.attn(
            attn_input,
            attn_input,
            attn_input,
            attn_mask=causal_mask,
            need_weights=False,
        )
        hidden = hidden + self.dropout(attn_output)
        hidden = hidden + self.ffn(self.ffn_norm(hidden))
        return hidden


class DecoderOnlyTransformer(nn.Module):
    def __init__(self, config: DecoderConfig) -> None:
        super().__init__()
        config.validate()
        self.config = config
        self.token_embedding = nn.Embedding(config.vocab_size, config.d_model)
        self.position_embedding = nn.Embedding(config.max_seq_len, config.d_model)
        self.blocks = nn.ModuleList([TransformerBlock(config) for _ in range(config.n_layers)])
        self.norm = nn.LayerNorm(config.d_model)
        self.lm_head = nn.Linear(config.d_model, config.vocab_size, bias=False)
        if config.tie_embeddings:
            self.lm_head.weight = self.token_embedding.weight
        self.apply(self._init_weights)

    def _init_weights(self, module: nn.Module) -> None:
        if isinstance(module, nn.Linear):
            nn.init.normal_(module.weight, mean=0.0, std=0.02)
            if module.bias is not None:
                nn.init.zeros_(module.bias)
        elif isinstance(module, nn.Embedding):
            nn.init.normal_(module.weight, mean=0.0, std=0.02)

    def forward(self, input_ids: torch.Tensor) -> torch.Tensor:
        if input_ids.ndim != 2:
            raise ValueError("input_ids must have shape [batch, seq]")
        batch, seq_len = input_ids.shape
        if seq_len > self.config.max_seq_len:
            raise ValueError(f"sequence length {seq_len} exceeds max_seq_len={self.config.max_seq_len}")
        positions = torch.arange(seq_len, device=input_ids.device).unsqueeze(0).expand(batch, seq_len)
        hidden = self.token_embedding(input_ids) + self.position_embedding(positions)
        causal_mask = torch.full((seq_len, seq_len), float("-inf"), device=input_ids.device)
        causal_mask = torch.triu(causal_mask, diagonal=1)
        for block in self.blocks:
            hidden = block(hidden, causal_mask)
        return self.lm_head(self.norm(hidden))

    @torch.no_grad()
    def generate(
        self,
        input_ids: torch.Tensor,
        *,
        max_new_tokens: int = 128,
        temperature: float = 0.8,
        top_p: float = 0.95,
        eos_token_id: int | None = None,
    ) -> torch.Tensor:
        self.eval()
        eos = self.config.eos_token_id if eos_token_id is None else eos_token_id
        output = input_ids
        for _ in range(max_new_tokens):
            context = output[:, -self.config.max_seq_len :]
            logits = self(context)[:, -1, :]
            if temperature <= 0:
                next_token = torch.argmax(logits, dim=-1, keepdim=True)
            else:
                probs = F.softmax(logits / max(temperature, 1e-6), dim=-1)
                filtered = nucleus_filter(probs, top_p)
                next_token = torch.multinomial(filtered, num_samples=1)
            output = torch.cat([output, next_token], dim=-1)
            if eos is not None and bool((next_token == eos).all()):
                break
        return output


def nucleus_filter(probs: torch.Tensor, top_p: float) -> torch.Tensor:
    if top_p >= 1.0:
        return probs
    sorted_probs, sorted_idx = torch.sort(probs, descending=True, dim=-1)
    cumulative = torch.cumsum(sorted_probs, dim=-1)
    remove = cumulative > top_p
    remove[..., 0] = False
    sorted_probs = sorted_probs.masked_fill(remove, 0.0)
    sorted_probs = sorted_probs / sorted_probs.sum(dim=-1, keepdim=True).clamp_min(1e-12)
    filtered = torch.zeros_like(probs)
    filtered.scatter_(dim=-1, index=sorted_idx, src=sorted_probs)
    return filtered


def save_checkpoint(model: DecoderOnlyTransformer, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    torch.save({"config": asdict(model.config), "state_dict": model.state_dict()}, path)


def load_checkpoint(path: Path, map_location: str | torch.device = "cpu") -> DecoderOnlyTransformer:
    payload = torch.load(path, map_location=map_location, weights_only=True)
    config = DecoderConfig(**payload["config"])
    model = DecoderOnlyTransformer(config)
    model.load_state_dict(payload["state_dict"])
    return model


def write_config(config: DecoderConfig, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(config), indent=2), encoding="utf-8")


def read_config(path: Path) -> DecoderConfig:
    return DecoderConfig(**json.loads(path.read_text(encoding="utf-8")))
