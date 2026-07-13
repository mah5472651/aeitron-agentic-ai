"""PyTorch Mythos decoder-only language model.

This is the scratch model implementation path. The module intentionally keeps
the complete transformer core in one place: architecture presets, RoPE, grouped
query attention, SDPA/FlashAttention dispatch, KV-cache inference, generation,
and export helpers.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, model_validator

from src.mythos.shared.schemas import StrictModel

try:
    import torch
    import torch.utils.checkpoint
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
    attention_impl: Literal["auto", "sdpa", "eager"] = "auto"
    attention_window: int | None = Field(default=None, ge=16)
    rope_scaling_factor: float = Field(default=1.0, ge=1.0)
    initializer_range: float = Field(default=0.02, gt=0.0, le=1.0)
    use_cache: bool = True
    gradient_checkpointing: bool = False
    logits_soft_cap: float | None = Field(default=None, gt=0.0)

    @model_validator(mode="after")
    def validate_shape(self) -> "ScratchDecoderConfig":
        if self.hidden_size % self.num_attention_heads != 0:
            raise ValueError("hidden_size must be divisible by num_attention_heads")
        if self.num_attention_heads % self.num_key_value_heads != 0:
            raise ValueError("num_attention_heads must be divisible by num_key_value_heads")
        if self.head_dim % 2 != 0:
            raise ValueError("head_dim must be even for rotary embeddings")
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
    past_key_values: tuple[tuple["torch.Tensor", "torch.Tensor"], ...] | None = None


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

    def forward(self, q: "torch.Tensor", k: "torch.Tensor", *, position_offset: int = 0) -> tuple["torch.Tensor", "torch.Tensor"]:
        seq_len = q.size(-2)
        end = position_offset + seq_len
        if end > self.cos.size(2):
            raise ValueError(f"sequence position {end} exceeds configured rotary cache {self.cos.size(2)}")
        cos = self.cos[:, :, position_offset:end, :].to(dtype=q.dtype, device=q.device)
        sin = self.sin[:, :, position_offset:end, :].to(dtype=q.dtype, device=q.device)
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
        self.rope = RotaryEmbedding(config.head_dim, config.max_sequence_length, config.rope_theta * config.rope_scaling_factor)
        self.dropout = config.dropout

    def build_attention_mask(
        self,
        *,
        query_len: int,
        key_len: int,
        past_len: int,
        device: "torch.device",
    ) -> "torch.Tensor | None":
        if self.config.attention_window is None and past_len == 0:
            return None
        query_positions = torch.arange(past_len, past_len + query_len, device=device)[:, None]
        key_positions = torch.arange(0, key_len, device=device)[None, :]
        mask = key_positions <= query_positions
        if self.config.attention_window is not None:
            mask = mask & (key_positions >= query_positions - self.config.attention_window + 1)
        return mask[None, None, :, :]

    def eager_attention(
        self,
        q: "torch.Tensor",
        k: "torch.Tensor",
        v: "torch.Tensor",
        *,
        mask: "torch.Tensor | None",
    ) -> "torch.Tensor":
        scale = 1.0 / math.sqrt(self.config.head_dim)
        scores = torch.matmul(q.float(), k.float().transpose(-2, -1)) * scale
        if mask is not None:
            scores = scores.masked_fill(~mask, torch.finfo(scores.dtype).min)
        else:
            causal = torch.ones(scores.size(-2), scores.size(-1), device=scores.device, dtype=torch.bool).tril()
            scores = scores.masked_fill(~causal[None, None, :, :], torch.finfo(scores.dtype).min)
        probs = F.softmax(scores, dim=-1).to(dtype=q.dtype)
        if self.training and self.dropout > 0:
            probs = F.dropout(probs, p=self.dropout)
        return torch.matmul(probs, v)

    def forward(
        self,
        x: "torch.Tensor",
        *,
        past_key_value: tuple["torch.Tensor", "torch.Tensor"] | None = None,
        use_cache: bool = False,
    ) -> tuple["torch.Tensor", tuple["torch.Tensor", "torch.Tensor"] | None]:
        batch, seq_len, _ = x.shape
        past_len = 0 if past_key_value is None else int(past_key_value[0].size(-2))
        q = self.q_proj(x).view(batch, seq_len, self.config.num_attention_heads, self.config.head_dim).transpose(1, 2)
        k = self.k_proj(x).view(batch, seq_len, self.config.num_key_value_heads, self.config.head_dim).transpose(1, 2)
        v = self.v_proj(x).view(batch, seq_len, self.config.num_key_value_heads, self.config.head_dim).transpose(1, 2)
        q, k = self.rope(q, k, position_offset=past_len)
        if past_key_value is not None:
            past_k, past_v = past_key_value
            k = torch.cat([past_k, k], dim=-2)
            v = torch.cat([past_v, v], dim=-2)
        present = (k, v) if use_cache else None
        repeat = self.config.num_attention_heads // self.config.num_key_value_heads
        if repeat > 1:
            k = k.repeat_interleave(repeat, dim=1)
            v = v.repeat_interleave(repeat, dim=1)
        mask = self.build_attention_mask(query_len=seq_len, key_len=k.size(-2), past_len=past_len, device=x.device)
        use_eager = self.config.attention_impl == "eager" or not hasattr(F, "scaled_dot_product_attention")
        if use_eager:
            attn = self.eager_attention(q, k, v, mask=mask)
        else:
            attn = F.scaled_dot_product_attention(
                q,
                k,
                v,
                attn_mask=mask,
                dropout_p=self.dropout if self.training else 0.0,
                is_causal=mask is None and past_len == 0,
            )
        attn = attn.transpose(1, 2).contiguous().view(batch, seq_len, self.config.hidden_size)
        return self.o_proj(attn), present


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

    def forward(
        self,
        x: "torch.Tensor",
        *,
        past_key_value: tuple["torch.Tensor", "torch.Tensor"] | None = None,
        use_cache: bool = False,
    ) -> tuple["torch.Tensor", tuple["torch.Tensor", "torch.Tensor"] | None]:
        attn, present = self.attention(self.input_norm(x), past_key_value=past_key_value, use_cache=use_cache)
        x = x + attn
        x = x + self.mlp(self.post_attention_norm(x))
        return x, present


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
        self.gradient_checkpointing = config.gradient_checkpointing

    def _init_weights(self, module: nn.Module) -> None:
        if isinstance(module, nn.Linear):
            nn.init.normal_(module.weight, mean=0.0, std=self.config.initializer_range)
        elif isinstance(module, nn.Embedding):
            nn.init.normal_(module.weight, mean=0.0, std=self.config.initializer_range)

    def enable_gradient_checkpointing(self) -> None:
        self.gradient_checkpointing = True

    def disable_gradient_checkpointing(self) -> None:
        self.gradient_checkpointing = False

    def forward(
        self,
        input_ids: "torch.Tensor",
        labels: "torch.Tensor | None" = None,
        past_key_values: tuple[tuple["torch.Tensor", "torch.Tensor"], ...] | None = None,
        use_cache: bool | None = None,
    ) -> DecoderForwardOutput:
        if input_ids.ndim != 2:
            raise ValueError("input_ids must be [batch, sequence]")
        past_len = 0 if not past_key_values else int(past_key_values[0][0].size(-2))
        if past_len + input_ids.size(1) > self.config.max_sequence_length:
            raise ValueError("input sequence exceeds max_sequence_length")
        if labels is not None and past_key_values is not None:
            raise ValueError("labels cannot be used with past_key_values")
        active_use_cache = self.config.use_cache if use_cache is None else use_cache
        x = self.embed_tokens(input_ids)
        presents: list[tuple["torch.Tensor", "torch.Tensor"]] = []
        for index, layer in enumerate(self.layers):
            past = None if past_key_values is None else past_key_values[index]
            if self.gradient_checkpointing and self.training and not active_use_cache:
                def custom_forward(hidden: "torch.Tensor", active_layer: DecoderBlock = layer) -> "torch.Tensor":
                    output, _present = active_layer(hidden, use_cache=False)
                    return output

                x = torch.utils.checkpoint.checkpoint(custom_forward, x, use_reentrant=False)
                present = None
            else:
                x, present = layer(x, past_key_value=past, use_cache=active_use_cache)
            if present is not None:
                presents.append(present)
        logits = self.lm_head(self.norm(x))
        if self.config.logits_soft_cap is not None:
            cap = self.config.logits_soft_cap
            logits = torch.tanh(logits / cap) * cap
        loss = None
        if labels is not None:
            shift_logits = logits[:, :-1, :].contiguous()
            shift_labels = labels[:, 1:].contiguous()
            loss = F.cross_entropy(shift_logits.view(-1, shift_logits.size(-1)), shift_labels.view(-1))
        return DecoderForwardOutput(logits=logits, loss=loss, past_key_values=tuple(presents) if presents else None)

    @torch.no_grad()
    def generate(
        self,
        input_ids: "torch.Tensor",
        *,
        max_new_tokens: int = 64,
        temperature: float = 0.0,
        top_k: int | None = None,
        eos_token_id: int | None = None,
    ) -> "torch.Tensor":
        if max_new_tokens < 1:
            return input_ids
        self.eval()
        generated = input_ids
        past: tuple[tuple["torch.Tensor", "torch.Tensor"], ...] | None = None
        next_input = input_ids
        for _ in range(max_new_tokens):
            output = self(next_input, past_key_values=past, use_cache=True)
            past = output.past_key_values
            logits = output.logits[:, -1, :]
            if temperature and temperature > 0:
                logits = logits / temperature
                if top_k is not None and top_k > 0:
                    values, _indices = torch.topk(logits, min(top_k, logits.size(-1)))
                    logits = logits.masked_fill(logits < values[:, [-1]], torch.finfo(logits.dtype).min)
                next_token = torch.multinomial(F.softmax(logits, dim=-1), num_samples=1)
            else:
                next_token = logits.argmax(dim=-1, keepdim=True)
            generated = torch.cat([generated, next_token], dim=1)
            next_input = next_token
            if eos_token_id is not None and bool((next_token == eos_token_id).all()):
                break
        return generated

    def export_checkpoint(self, output_dir: str | Path, *, dtype: str = "float32") -> Path:
        target = Path(output_dir)
        target.mkdir(parents=True, exist_ok=True)
        (target / "config.json").write_text(self.config.model_dump_json(indent=2), encoding="utf-8")
        torch.save({"model": self.state_dict(), "config": self.config.model_dump(), "format": "mythos_decoder_v1", "dtype": dtype}, target / "model.pt")
        (target / "serving_compatibility.json").write_text(
            "{\n"
            '  "format": "mythos_decoder_v1",\n'
            '  "vllm": "requires Mythos model adapter or conversion to Hugging Face compatible module",\n'
            '  "tensorrt_llm": "requires conversion plugin for Mythos decoder weights",\n'
            '  "kv_cache": true,\n'
            f'  "attention_impl": "{self.config.attention_impl}"\n'
            "}\n",
            encoding="utf-8",
        )
        return target


def model_profile(name: str) -> ScratchDecoderConfig:
    profiles = {
        "tiny": tiny_smoke_config(),
        "1b": ScratchDecoderConfig(
            name="mythos-1b-scratch",
            vocab_size=64_000,
            max_sequence_length=65_536,
            hidden_size=2048,
            num_layers=24,
            num_attention_heads=16,
            num_key_value_heads=4,
            intermediate_size=5504,
            attention_impl="auto",
            rope_scaling_factor=2.0,
        ),
        "7b": ScratchDecoderConfig(
            name="mythos-7b-scratch",
            vocab_size=64_000,
            max_sequence_length=131_072,
            hidden_size=4096,
            num_layers=32,
            num_attention_heads=32,
            num_key_value_heads=8,
            intermediate_size=11008,
            attention_impl="auto",
            rope_scaling_factor=4.0,
        ),
        "32b": ScratchDecoderConfig(
            name="mythos-32b-scratch",
            vocab_size=64_000,
            max_sequence_length=131_072,
            hidden_size=6656,
            num_layers=60,
            num_attention_heads=52,
            num_key_value_heads=4,
            intermediate_size=17920,
            attention_impl="auto",
            rope_scaling_factor=4.0,
        ),
        "62b": ScratchDecoderConfig(
            name="mythos-62b-scratch",
            vocab_size=64_000,
            max_sequence_length=262_144,
            hidden_size=8192,
            num_layers=72,
            num_attention_heads=64,
            num_key_value_heads=8,
            intermediate_size=28672,
            attention_impl="auto",
            rope_scaling_factor=8.0,
            gradient_checkpointing=True,
        ),
    }
    key = name.lower()
    if key not in profiles:
        raise ValueError(f"unknown model profile: {name}")
    return profiles[key]


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
