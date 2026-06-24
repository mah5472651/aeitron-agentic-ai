#!/usr/bin/env python
"""OpenAI-compatible model and judge clients for evaluation."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from src.phase9.models import Generation


class ModelClientError(RuntimeError):
    """Raised when generation or judge calls fail."""


class BaseModelClient:
    model: str

    async def generate(self, prompt: str, *, n: int = 1, temperature: float = 0.2, max_tokens: int = 768) -> list[Generation]:
        raise NotImplementedError


class OpenAICompatibleClient(BaseModelClient):
    """Minimal async client for vLLM/OpenAI-compatible chat-completion endpoints."""

    def __init__(
        self,
        endpoint: str,
        model: str,
        api_key: str | None = None,
        timeout_s: float = 120.0,
    ) -> None:
        parsed = urlparse(endpoint.rstrip("/"))
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("model endpoint must be an absolute http:// or https:// URL")
        self.endpoint = endpoint.rstrip("/")
        self.model = model
        self.timeout_s = timeout_s
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self.client = httpx.AsyncClient(headers=headers, timeout=httpx.Timeout(timeout_s, connect=10.0))

    async def aclose(self) -> None:
        await self.client.aclose()

    async def generate(self, prompt: str, *, n: int = 1, temperature: float = 0.2, max_tokens: int = 768) -> list[Generation]:
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "n": n,
        }
        started = time.perf_counter()
        response = await self.client.post(f"{self.endpoint}/chat/completions", json=payload, timeout=self.timeout_s)
        if response.status_code == 404:
            response = await self.client.post(f"{self.endpoint}/v1/chat/completions", json=payload, timeout=self.timeout_s)
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise ModelClientError(exc.response.text) from exc
        raw = response.json()
        latency_ms = (time.perf_counter() - started) * 1000
        generations: list[Generation] = []
        for choice in raw.get("choices", []):
            message = choice.get("message") or {}
            text = message.get("content") or choice.get("text") or ""
            generations.append(Generation(text=text, model=self.model, latency_ms=latency_ms, raw=choice))
        if not generations:
            raise ModelClientError("model endpoint returned zero generations")
        return generations


class JsonlReplayClient(BaseModelClient):
    """Deterministic offline client for CI/debug runs."""

    def __init__(self, path: Path, model: str = "jsonl-replay") -> None:
        self.model = model
        self._responses = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        self._lock = asyncio.Lock()
        self._index = 0

    async def generate(self, prompt: str, *, n: int = 1, temperature: float = 0.2, max_tokens: int = 768) -> list[Generation]:
        del prompt, temperature, max_tokens
        async with self._lock:
            batch: list[Generation] = []
            for _ in range(n):
                if self._index >= len(self._responses):
                    raise ModelClientError("replay file exhausted")
                payload = self._responses[self._index]
                self._index += 1
                text = str(payload.get("response") or payload.get("text") or payload.get("completion") or "")
                batch.append(Generation(text=text, model=self.model, latency_ms=0.0, raw=payload))
            return batch


class LLMJudge:
    """Structured head-to-head judge using any OpenAI-compatible reasoning model."""

    def __init__(self, client: BaseModelClient) -> None:
        self.client = client

    async def judge(self, prompt: str, answer_a: str, answer_b: str) -> dict[str, Any]:
        judge_prompt = f"""You are a strict coding and security evaluation judge.
Score two model responses on correctness, security awareness, and explanation quality.
Return only JSON with keys: winner, correctness_a, correctness_b, security_a, security_b,
explanation_a, explanation_b, rationale. winner must be model_a, model_b, or tie.

PROMPT:
{prompt}

MODEL_A:
{answer_a}

MODEL_B:
{answer_b}
"""
        generation = (await self.client.generate(judge_prompt, n=1, temperature=0.0, max_tokens=512))[0]
        text = generation.text.strip()
        try:
            return json.loads(text[text.find("{") : text.rfind("}") + 1])
        except Exception as exc:
            raise ModelClientError(f"judge returned non-JSON payload: {text[:500]}") from exc
