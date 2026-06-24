#!/usr/bin/env python
"""Real base-model connection probes for Qwen/DeepSeek/Llama-family endpoints."""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field

from src.phase11.model_backends import ModelBackend, build_backend


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class ModelEndpointProbe(StrictModel):
    configured: bool
    reachable: bool
    lineage_ok: bool = False
    backend_kind: str
    endpoint: str | None = None
    model_name: str | None = None
    family: str | None = None
    message: str
    latency_ms: float = 0.0
    metadata: dict[str, Any] = Field(default_factory=dict)


def detect_model_family(*values: str | None) -> str | None:
    text = " ".join(value or "" for value in values).lower()
    for family in ("qwen", "deepseek", "llama"):
        if family in text:
            return family
    return None


def custom_model_allowed(prefix: str) -> bool:
    return os.environ.get(f"{prefix}_ALLOW_CUSTOM_REAL_MODEL", "0") == "1"


def backend_from_environment(prefix: str = "PHASE16") -> ModelBackend:
    kind = os.environ.get(f"{prefix}_BACKEND", os.environ.get("PHASE11_BACKEND", "mock"))
    return build_backend(
        kind,
        endpoint=os.environ.get(f"{prefix}_MODEL_ENDPOINT", os.environ.get("PHASE11_MODEL_ENDPOINT", "http://127.0.0.1:8000/v1")),
        model_name=os.environ.get(f"{prefix}_MODEL_NAME", os.environ.get("PHASE11_MODEL_NAME", "security-coder")),
        api_key=os.environ.get(f"{prefix}_API_KEY", os.environ.get("PHASE11_API_KEY")),
        checkpoint=os.environ.get(f"{prefix}_CHECKPOINT", os.environ.get("PHASE11_CHECKPOINT")),
        tokenizer_path=os.environ.get(f"{prefix}_TOKENIZER", os.environ.get("PHASE11_TOKENIZER")),
        device=os.environ.get(f"{prefix}_DEVICE", os.environ.get("PHASE11_DEVICE", "cpu")),
    )


async def probe_real_endpoint(prefix: str = "PHASE16") -> ModelEndpointProbe:
    backend_kind = os.environ.get(f"{prefix}_BACKEND", os.environ.get("PHASE11_BACKEND", "mock"))
    endpoint = os.environ.get(f"{prefix}_MODEL_ENDPOINT", os.environ.get("PHASE11_MODEL_ENDPOINT"))
    model_name = os.environ.get(f"{prefix}_MODEL_NAME", os.environ.get("PHASE11_MODEL_NAME"))
    if backend_kind == "mock":
        return ModelEndpointProbe(
            configured=False,
            reachable=False,
            lineage_ok=False,
            backend_kind=backend_kind,
            endpoint=endpoint,
            model_name=model_name,
            family=None,
            message="Real Qwen/DeepSeek/Llama endpoint is not configured; mock backend is active.",
        )
    if backend_kind == "pytorch":
        checkpoint = os.environ.get(f"{prefix}_CHECKPOINT", os.environ.get("PHASE11_CHECKPOINT"))
        ok = bool(checkpoint and Path(checkpoint).exists())
        family = detect_model_family(model_name, checkpoint)
        lineage_ok = bool(family or custom_model_allowed(prefix))
        return ModelEndpointProbe(
            configured=bool(checkpoint),
            reachable=ok,
            lineage_ok=lineage_ok,
            backend_kind=backend_kind,
            endpoint=None,
            model_name=model_name,
            family=family,
            message=(
                "PyTorch checkpoint is present and model lineage is accepted."
                if ok and lineage_ok
                else "PyTorch backend selected but checkpoint path is missing or lineage is not Qwen/DeepSeek/Llama."
            ),
            metadata={"checkpoint": checkpoint, "custom_model_allowed": custom_model_allowed(prefix)},
        )
    if not endpoint:
        return ModelEndpointProbe(
            configured=False,
            reachable=False,
            lineage_ok=False,
            backend_kind=backend_kind,
            endpoint=None,
            model_name=model_name,
            family=None,
            message="OpenAI-compatible endpoint is not configured.",
        )
    started = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            response = await client.get(endpoint.rstrip("/") + "/models")
            if response.status_code == 404:
                response = await client.get(endpoint.rstrip("/").removesuffix("/v1") + "/v1/models")
        body_preview = response.text[:1000]
        family = detect_model_family(model_name, body_preview)
        lineage_ok = bool(family or custom_model_allowed(prefix))
        reachable = response.status_code < 500
        message = f"endpoint probe status={response.status_code}"
        if reachable and not lineage_ok:
            message += "; reachable but Qwen/DeepSeek/Llama lineage was not detected"
        return ModelEndpointProbe(
            configured=True,
            reachable=reachable,
            lineage_ok=lineage_ok,
            backend_kind=backend_kind,
            endpoint=endpoint,
            model_name=model_name,
            family=family,
            message=message,
            latency_ms=(time.perf_counter() - started) * 1000,
            metadata={"body_preview": body_preview, "custom_model_allowed": custom_model_allowed(prefix)},
        )
    except Exception as exc:
        return ModelEndpointProbe(
            configured=True,
            reachable=False,
            lineage_ok=False,
            backend_kind=backend_kind,
            endpoint=endpoint,
            model_name=model_name,
            family=None,
            message=f"{type(exc).__name__}: {exc}",
            latency_ms=(time.perf_counter() - started) * 1000,
        )
