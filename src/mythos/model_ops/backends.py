"""Serving adapters for Mythos-owned model checkpoints.

Mythos is scratch-first. The only production serving backend here targets a
Mythos checkpoint served locally/privately. The mock backend is a test double
for plumbing checks and is not a model strategy.
"""

from __future__ import annotations

import os
from typing import Any

import httpx

from src.mythos.shared.config import load_active_profile


class ModelBackend:
    name: str = "base"

    async def generate(self, prompt: str, *, temperature: float = 0.2, max_tokens: int = 1024) -> str:
        raise NotImplementedError

    async def aclose(self) -> None:
        return None


class MockModelBackend(ModelBackend):
    name = "mock"

    async def generate(self, prompt: str, *, temperature: float = 0.2, max_tokens: int = 1024) -> str:
        return (
            "Mock Aeitron response. I inspected the request and would create a minimal, tested patch. "
            f"Prompt: {prompt[:500]}"
        )


class MythosServingBackend(ModelBackend):
    name = "aeitron_serving"

    def __init__(self, *, endpoint: str, model_name: str, api_key: str | None = None) -> None:
        self.endpoint = endpoint.rstrip("/")
        self.model_name = model_name
        self.api_key = api_key
        self.client = httpx.AsyncClient(timeout=60)

    async def generate(self, prompt: str, *, temperature: float = 0.2, max_tokens: int = 1024) -> str:
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        response = await self.client.post(
            f"{self.endpoint}/chat/completions",
            headers=headers,
            json={
                "model": self.model_name,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": temperature,
                "max_tokens": max_tokens,
            },
        )
        response.raise_for_status()
        payload = response.json()
        return str(payload["choices"][0]["message"]["content"])

    async def aclose(self) -> None:
        await self.client.aclose()


def _profile_payload() -> dict[str, Any]:
    payload = load_active_profile()
    profile = payload.get("profile") if isinstance(payload.get("profile"), dict) else {}
    env = payload.get("env") if isinstance(payload.get("env"), dict) else {}
    return {**env, **profile}


def build_active_backend() -> ModelBackend:
    profile = _profile_payload()
    backend = str(profile.get("backend") or os.environ.get("MYTHOS_MODEL_BACKEND") or "mock")
    if backend in {"aeitron_serving", "mythos_serving", "active"}:
        return MythosServingBackend(
            endpoint=str(profile.get("endpoint") or os.environ.get("MYTHOS_MODEL_ENDPOINT") or "http://127.0.0.1:8000/v1"),
            model_name=str(profile.get("model_name") or os.environ.get("MYTHOS_MODEL_NAME") or "aeitron-scratch"),
            api_key=os.environ.get("MYTHOS_MODEL_API_KEY"),
        )
    return MockModelBackend()


def list_model_profiles() -> dict[str, Any]:
    return {
        "mock": {"backend": "mock", "quality": "test double only, not a real model"},
        "aeitron-scratch-local": {
            "backend": "aeitron_serving",
            "endpoint": os.environ.get("MYTHOS_MODEL_ENDPOINT", "http://127.0.0.1:8000/v1"),
            "model_name": os.environ.get("MYTHOS_MODEL_NAME", "aeitron-scratch"),
            "checkpoint_policy": "Aeitron-owned scratch checkpoint only",
        },
    }


def activate_model_profile(name: str, *, run_id: str = "mythos-profile") -> dict[str, Any]:
    profiles = list_model_profiles()
    if name not in profiles:
        raise ValueError(f"unknown model profile: {name}")
    return {"run_id": run_id, "activated": name, "profile": profiles[name]}


def active_model_health() -> dict[str, Any]:
    profile = _profile_payload()
    backend = str(profile.get("backend") or os.environ.get("MYTHOS_MODEL_BACKEND") or "mock")
    return {
        "ok": True,
        "backend": backend,
        "endpoint": str(profile.get("endpoint") or os.environ.get("MYTHOS_MODEL_ENDPOINT") or ""),
        "model_name": str(profile.get("model_name") or os.environ.get("MYTHOS_MODEL_NAME") or "mock"),
    }
