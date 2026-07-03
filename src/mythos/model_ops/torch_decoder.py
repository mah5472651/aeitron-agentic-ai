"""PyTorch Mythos decoder-only language model.

This is the scratch model implementation path. Large production presets live in
`foundation.py`; this module provides the executable decoder family used for
GPU smoke tests and future scratch pretraining.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from pydantic import Field, model_validator

from src.mythos.shared.schemas import StrictModel

try:
    import torch
    import torch.nn as nn
    import torch.nn.functional as F
except ImportError:  # pragma: no cover - exercised only on missing torch installs.
    torch = None  # type: ignore[assignment]
    nn = None  # type: ignore[assignment]
    F = None  # type: ignore[assignment]


class ScratchDecoderConfig(StrictModel):
    name: str = "mythos-tiny-smoke"
    vocab_size: int = Field(default=4096, ge=256)
    max_sequence_length: int = Field(default=512, ge=16)
    hidden_size: int = Field(default=256, ge=64)
    num_layers: int = Field(default=4, ge=1)
    num_attention_heads: int = Field(default=4, ge=1)
    num_key_value_heads: int = Field(default=4, ge=1)
    intermediate_size: int = Field(default=1024, ge=128)
    rope_theta: float = Field(default=1_000_000.0, gt=0)
    norm_eps: float = Field(default=1e-6, gt=0)
    dropout: float = Field(default=0.0, ge=0.0, le=0.5)
    tie_word_embeddings: bool = True

    @model_validator(mode="after")
    def validate_shape(self) -> "ScratchDecoderConfig":
        if self.hidden_size % self.num_attention_heads != 0:
            raise ValueError("hidden_size must be divisible by num_attention_heads")
        if self.num_attention_heads % self.num_key_value_heads != 0:
            raise ValueError("num_attention_heads must be divisible by num_key_value_heads")
        return self

    @property
    def head_dim(self) -> int:
        return self.hidden_size // self.num_attention_heads

    def parameter_estimate(self) -> int:
        embedding = self.vocab_size * self.hidden_size
        q = self.hidden_size * self.hidden_size
        kv = 2 * self.hidden_size * self.head_dim * self.num_key_value_heads
        out = self.hidden_size * self.hidden_size
        mlp = 3 * self.hidden_size * self.intermediate_size
        norms = 2 * self.hidden_size
        transformer = self.num_layers * (q + kv + out + mlp + norms)
        lm_head = 0 if self.tie_word_embeddings else embedding
        return int(embedding + transformer + lm_head)


@dataclass
class DecoderForwardOutput:
    logits: "torch.Tensor"
    loss: "torch.Tensor | None" = None


def require_torch() -> None:
    if torch is None or nn is None or F is None:
        raise RuntimeError("torch is required for Mythos scratch decoder execution")


class RMSNorm(nn.Module):  # type: ignore[misc]
    def __init__(self, hidden_size: int, eps: float) -> None:
        require_torch()
        super().__init__()
        self.weight = nn.Parameter(torch.ones(hidden_size))
        self.eps = eps

    def forward(self, x: "torch.Tensor") -> "torch.Tensor":
        variance = x.float().pow(2).mean(dim=-1, keepdim=True)
        x = x * torch.rsqrt(variance + self.eps)
        return self.weight * x.to(dtype=self.weight.dtype)


def rotate_half(x: "torch.Tensor") -> "torch.Tensor":
    left, right = x[..., ::2], x[..., 1::2]
    return torch.stack((-right, left), dim=-1).flatten(-2)


def apply_rope(x: "torch.Tensor", cos: "torch.Tensor", sin: "torch.Tensor") -> "torch.Tensor":
    return (x * cos) + (rotate_half(x) * sin)


class RotaryEmbedding(nn.Module):  # type: ignore[misc]
    def __init__(self, dim: int, max_position: int, theta: float) -> None:
        require_torch()
        super().__init__()
        inv_freq = 1.0 / (theta ** (torch.arange(0, dim, 2).float() / dim))
        positions = torch.arange(max_position).float()
        freqs = torch.einsum("i,j->ij", positions, inv_freq)
        emb = torch.cat((freqs, freqs), dim=-1)
        self.register_buffer("cos", emb.cos()[None, None, :, :], persistent=False)
        self.register_buffer("sin", emb.sin()[None, None, :, :], persistent=False)

    def forward(self, q: "torch.Tensor", k: "torch.Tensor") -> tuple["torch.Tensor", "torch.Tensor"]:
        seq_len = q.size(-2)
        cos = self.cos[:, :, :seq_len, :].to(dtype=q.dtype, device=q.device)
        sin = self.sin[:, :, :seq_len, :].to(dtype=q.dtype, device=q.device)
        return apply_rope(q, cos, sin), apply_rope(k, cos, sin)


class CausalSelfAttention(nn.Module):  # type: ignore[misc]
    def __init__(self, config: ScratchDecoderConfig) -> None:
        require_torch()
        super().__init__()
        self.config = config
        self.q_proj = nn.Linear(config.hidden_size, config.hidden_size, bias=False)
        self.k_proj = nn.Linear(config.hidden_size, config.num_key_value_heads * config.head_dim, bias=False)
        self.v_proj = nn.Linear(config.hidden_size, config.num_key_value_heads * config.head_dim, bias=False)
        self.o_proj = nn.Linear(config.hidden_size, config.hidden_size, bias=False)
        self.rope = RotaryEmbedding(config.head_dim, config.max_sequence_length, config.rope_theta)
        self.dropout = config.dropout

    def forward(self, x: "torch.Tensor") -> "torch.Tensor":
        batch, seq_len, _ = x.shape
        q = self.q_proj(x).view(batch, seq_len, self.config.num_attention_heads, self.config.head_dim).transpose(1, 2)
        k = self.k_proj(x).view(batch, seq_len, self.config.num_key_value_heads, self.config.head_dim).transpose(1, 2)
        v = self.v_proj(x).view(batch, seq_len, self.config.num_key_value_heads, self.config.head_dim).transpose(1, 2)
        q, k = self.rope(q, k)
        repeat = self.config.num_attention_heads // self.config.num_key_value_heads
        if repeat > 1:
            k = k.repeat_interleave(repeat, dim=1)
            v = v.repeat_interleave(repeat, dim=1)
        attn = F.scaled_dot_product_attention(
            q,
            k,
            v,
            dropout_p=self.dropout if self.training else 0.0,
            is_causal=True,
        )
        attn = attn.transpose(1, 2).contiguous().view(batch, seq_len, self.config.hidden_size)
        return self.o_proj(attn)


class SwiGLU(nn.Module):  # type: ignore[misc]
    def __init__(self, config: ScratchDecoderConfig) -> None:
        require_torch()
        super().__init__()
        self.gate_proj = nn.Linear(config.hidden_size, config.intermediate_size, bias=False)
        self.up_proj = nn.Linear(config.hidden_size, config.intermediate_size, bias=False)
        self.down_proj = nn.Linear(config.intermediate_size, config.hidden_size, bias=False)

    def forward(self, x: "torch.Tensor") -> "torch.Tensor":
        return self.down_proj(F.silu(self.gate_proj(x)) * self.up_proj(x))


class DecoderBlock(nn.Module):  # type: ignore[misc]
    def __init__(self, config: ScratchDecoderConfig) -> None:
        require_torch()
        super().__init__()
        self.input_norm = RMSNorm(config.hidden_size, config.norm_eps)
        self.attention = CausalSelfAttention(config)
        self.post_attention_norm = RMSNorm(config.hidden_size, config.norm_eps)
        self.mlp = SwiGLU(config)

    def forward(self, x: "torch.Tensor") -> "torch.Tensor":
        x = x + self.attention(self.input_norm(x))
        x = x + self.mlp(self.post_attention_norm(x))
        return x


class MythosDecoderLM(nn.Module):  # type: ignore[misc]
    def __init__(self, config: ScratchDecoderConfig) -> None:
        require_torch()
        super().__init__()
        self.config = config
        self.embed_tokens = nn.Embedding(config.vocab_size, config.hidden_size)
        self.layers = nn.ModuleList([DecoderBlock(config) for _ in range(config.num_layers)])
        self.norm = RMSNorm(config.hidden_size, config.norm_eps)
        self.lm_head = nn.Linear(config.hidden_size, config.vocab_size, bias=False)
        if config.tie_word_embeddings:
            self.lm_head.weight = self.embed_tokens.weight
        self.apply(self._init_weights)

    def _init_weights(self, module: nn.Module) -> None:
        if isinstance(module, nn.Linear):
            nn.init.normal_(module.weight, mean=0.0, std=0.02)
        elif isinstance(module, nn.Embedding):
            nn.init.normal_(module.weight, mean=0.0, std=0.02)

    def forward(
        self,
        input_ids: "torch.Tensor",
        labels: "torch.Tensor | None" = None,
    ) -> DecoderForwardOutput:
        if input_ids.ndim != 2:
            raise ValueError("input_ids must be [batch, sequence]")
        if input_ids.size(1) > self.config.max_sequence_length:
            raise ValueError("input sequence exceeds max_sequence_length")
        x = self.embed_tokens(input_ids)
        for layer in self.layers:
            x = layer(x)
        logits = self.lm_head(self.norm(x))
        loss = None
        if labels is not None:
            shift_logits = logits[:, :-1, :].contiguous()
            shift_labels = labels[:, 1:].contiguous()
            loss = F.cross_entropy(shift_logits.view(-1, shift_logits.size(-1)), shift_labels.view(-1))
        return DecoderForwardOutput(logits=logits, loss=loss)


def tiny_smoke_config() -> ScratchDecoderConfig:
    return ScratchDecoderConfig(
        name="mythos-tiny-gpu-smoke",
        vocab_size=2048,
        max_sequence_length=128,
        hidden_size=128,
        num_layers=2,
        num_attention_heads=4,
        num_key_value_heads=2,
        intermediate_size=512,
        tie_word_embeddings=True,
    )
