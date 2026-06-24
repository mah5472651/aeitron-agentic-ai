#!/usr/bin/env python
"""Unified model backend layer for mock, PyTorch, and OpenAI-compatible models."""

from __future__ import annotations

import asyncio
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from src.phase11.pytorch_model import DecoderConfig, DecoderOnlyTransformer, load_checkpoint
from src.phase11.schemas import BackendKind, ChatMessage, ChatRole, GenerationRequest, GenerationResponse
from src.phase11.tokenization import TokenizerAdapter, load_tokenizer


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def render_messages(messages: list[ChatMessage]) -> str:
    return "\n".join(f"{message.role.value.upper()}: {message.content}" for message in messages)


class ModelBackend(ABC):
    kind: BackendKind
    model_name: str

    @abstractmethod
    async def generate(self, request: GenerationRequest) -> GenerationResponse:
        raise NotImplementedError

    async def aclose(self) -> None:
        return None


class MockReasoningBackend(ModelBackend):
    kind = BackendKind.MOCK
    model_name = "phase11-mock-reasoner"

    async def generate(self, request: GenerationRequest) -> GenerationResponse:
        started = time.perf_counter()
        user_text = next((message.content for message in reversed(request.messages) if message.role == ChatRole.USER), "")
        text = self._answer(user_text, request.workspace)
        return GenerationResponse(
            text=text,
            backend=self.kind.value,
            model=self.model_name,
            latency_ms=(time.perf_counter() - started) * 1000,
            prompt_tokens_estimate=estimate_tokens(render_messages(request.messages)),
            completion_tokens_estimate=estimate_tokens(text),
            metadata={"deterministic": True},
        )

    def _answer(self, prompt: str, workspace: str | None) -> str:
        lower = prompt.lower()
        if any(marker in lower for marker in ("vulnerab", "security", "exploit", "cve")):
            focus = "security review, exploitability analysis, patch design, and regression verification"
        elif any(marker in lower for marker in ("build", "code", "implement", "app", "api")):
            focus = "repo inspection, minimal architecture design, implementation, tests, and verification"
        else:
            focus = "intent expansion, context gathering, execution plan, and final validation"
        workspace_note = f"\nWorkspace: {workspace}" if workspace else ""
        return (
            "I expanded the request into an engineering workflow.\n"
            f"Primary focus: {focus}.{workspace_note}\n"
            "Plan:\n"
            "1. Inspect relevant files, symbols, tests, and runtime constraints.\n"
            "2. Build the smallest correct implementation with clear module boundaries.\n"
            "3. Run sandbox/tests/static checks and repair failures before finalizing.\n"
            "4. Return concise changes, verification, and remaining constraints."
        )


class OpenAICompatibleBackend(ModelBackend):
    kind = BackendKind.OPENAI_COMPATIBLE

    def __init__(self, endpoint: str, model_name: str, api_key: str | None = None, timeout_s: float = 120.0) -> None:
        parsed = urlparse(endpoint.rstrip("/"))
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("endpoint must be an absolute http:// or https:// URL")
        self.endpoint = endpoint.rstrip("/")
        self.model_name = model_name
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self.client = httpx.AsyncClient(headers=headers, timeout=httpx.Timeout(timeout_s, connect=10.0))

    async def aclose(self) -> None:
        await self.client.aclose()

    async def generate(self, request: GenerationRequest) -> GenerationResponse:
        started = time.perf_counter()
        payload = {
            "model": self.model_name,
            "messages": [{"role": item.role.value, "content": item.content} for item in request.messages],
            "max_tokens": request.config.max_new_tokens,
            "temperature": request.config.temperature,
            "top_p": request.config.top_p,
            "stream": False,
        }
        response = await self.client.post(f"{self.endpoint}/chat/completions", json=payload)
        if response.status_code == 404:
            response = await self.client.post(f"{self.endpoint}/v1/chat/completions", json=payload)
        response.raise_for_status()
        raw = response.json()
        text = ""
        choices = raw.get("choices") or []
        if choices:
            message = choices[0].get("message") or {}
            text = message.get("content") or choices[0].get("text") or ""
        return GenerationResponse(
            text=text,
            backend=self.kind.value,
            model=self.model_name,
            latency_ms=(time.perf_counter() - started) * 1000,
            prompt_tokens_estimate=estimate_tokens(render_messages(request.messages)),
            completion_tokens_estimate=estimate_tokens(text),
            metadata={"raw_usage": raw.get("usage", {})},
        )


class PyTorchCausalLMBackend(ModelBackend):
    kind = BackendKind.PYTORCH

    def __init__(
        self,
        checkpoint: Path | None = None,
        model_name: str = "phase11-pytorch-decoder",
        device: str = "cpu",
        config: DecoderConfig | None = None,
        tokenizer_path: Path | None = None,
    ) -> None:
        self.model_name = model_name
        self.device = device
        self.checkpoint_loaded = bool(checkpoint and checkpoint.exists())
        self.tokenizer: TokenizerAdapter
        if checkpoint and checkpoint.exists():
            self.model = load_checkpoint(checkpoint, map_location=device)
            self.tokenizer = load_tokenizer(tokenizer_path, fallback_vocab_size=self.model.config.vocab_size)
            if tokenizer_path and self.tokenizer.vocab_size != self.model.config.vocab_size:
                raise ValueError(
                    "tokenizer vocab_size does not match checkpoint config: "
                    f"{self.tokenizer.vocab_size} != {self.model.config.vocab_size}"
                )
        else:
            fallback_size = config.vocab_size if config else 256
            self.tokenizer = load_tokenizer(tokenizer_path, fallback_vocab_size=fallback_size)
            model_config = config or DecoderConfig(
                vocab_size=self.tokenizer.vocab_size,
                max_seq_len=512,
                d_model=128,
                n_layers=2,
                n_heads=4,
                d_ff=512,
            )
            if model_config.vocab_size != self.tokenizer.vocab_size:
                if tokenizer_path:
                    raise ValueError(
                        "tokenizer vocab_size does not match model config: "
                        f"{self.tokenizer.vocab_size} != {model_config.vocab_size}"
                    )
                self.tokenizer = load_tokenizer(None, fallback_vocab_size=model_config.vocab_size)
            self.model = DecoderOnlyTransformer(model_config)
        self.model.to(device)

    async def generate(self, request: GenerationRequest) -> GenerationResponse:
        return await asyncio.to_thread(self._generate_sync, request)

    def _generate_sync(self, request: GenerationRequest) -> GenerationResponse:
        import torch

        started = time.perf_counter()
        prompt = render_messages(request.messages) + "\nASSISTANT:"
        ids = self.tokenizer.encode(prompt)[-self.model.config.max_seq_len :]
        input_ids = torch.tensor([ids], dtype=torch.long, device=self.device)
        output = self.model.generate(
            input_ids,
            max_new_tokens=min(request.config.max_new_tokens, 128),
            temperature=request.config.temperature,
            top_p=request.config.top_p,
            eos_token_id=self.tokenizer.eos_token_id,
        )
        generated_ids = output[0, input_ids.shape[1] :].detach().cpu().tolist()
        text = self.tokenizer.decode(generated_ids)
        return GenerationResponse(
            text=text,
            backend=self.kind.value,
            model=self.model_name,
            latency_ms=(time.perf_counter() - started) * 1000,
            prompt_tokens_estimate=len(ids),
            completion_tokens_estimate=len(generated_ids),
            metadata={"checkpoint_loaded": self.checkpoint_loaded, "tokenizer": self.tokenizer.name},
        )


def build_backend(kind: str = "mock", **kwargs: Any) -> ModelBackend:
    if kind == BackendKind.MOCK.value:
        return MockReasoningBackend()
    if kind == BackendKind.OPENAI_COMPATIBLE.value:
        return OpenAICompatibleBackend(
            endpoint=kwargs["endpoint"],
            model_name=kwargs.get("model_name", "security-coder"),
            api_key=kwargs.get("api_key"),
        )
    if kind == BackendKind.PYTORCH.value:
        checkpoint = Path(kwargs["checkpoint"]) if kwargs.get("checkpoint") else None
        tokenizer_path = Path(kwargs["tokenizer_path"]) if kwargs.get("tokenizer_path") else None
        return PyTorchCausalLMBackend(
            checkpoint=checkpoint,
            model_name=kwargs.get("model_name", "phase11-pytorch-decoder"),
            device=kwargs.get("device", "cpu"),
            tokenizer_path=tokenizer_path,
        )
    raise ValueError(f"unsupported backend kind: {kind}")
