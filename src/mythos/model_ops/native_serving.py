"""Native Mythos scratch checkpoint serving.

This is the production path for serving Mythos-owned scratch checkpoints before
vLLM/TensorRT conversion exists. It intentionally fails fast on missing assets
or incompatible tokenizer/model state instead of falling back to a mock model.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import Field

from src.mythos.identity.auth import install_auth
from src.mythos.identity.quota import install_quota
from src.mythos.model_ops.checkpoint_compare import GenerationConfig, generate_text
from src.mythos.model_ops.foundation import CheckpointManifest, sha256_file
from src.mythos.model_ops.tokenizer_pipeline import load_tokenizer
from src.mythos.model_ops.torch_decoder import MythosDecoderLM, ScratchDecoderConfig, require_torch
from src.mythos.observability import install_observability
from src.mythos.shared.schemas import StrictModel

try:
    import torch
except ImportError:  # pragma: no cover
    torch = None  # type: ignore[assignment]


class ChatMessage(StrictModel):
    role: str
    content: str


class ChatCompletionRequest(StrictModel):
    model: str = "mythos-scratch"
    messages: list[ChatMessage] = Field(min_length=1)
    temperature: float = Field(default=0.0, ge=0.0, le=5.0)
    max_tokens: int = Field(default=256, ge=1, le=4096)
    top_k: int = Field(default=20, ge=0, le=500)
    stream: bool = False


class NativeServingConfig(StrictModel):
    checkpoint_manifest: str
    tokenizer_path: str
    model_name: str = "mythos-scratch"
    device: str = "auto"
    require_tokenizer_hash_match: bool = True
    auth_enabled: bool = True
    quota_enabled: bool = True


class NativeServingState:
    def __init__(self, config: NativeServingConfig) -> None:
        require_torch()
        self.config = config
        self.manifest = CheckpointManifest.model_validate(json.loads(Path(config.checkpoint_manifest).read_text(encoding="utf-8-sig")))
        self.checkpoint_path = Path(self.manifest.checkpoint_dir) / "model.pt"
        self.tokenizer_path = Path(config.tokenizer_path)
        if not self.checkpoint_path.exists():
            raise FileNotFoundError(f"checkpoint model file not found: {self.checkpoint_path}")
        if not self.tokenizer_path.exists():
            raise FileNotFoundError(f"tokenizer file not found: {self.tokenizer_path}")
        self.device = self._select_device(config.device)
        payload = torch.load(self.checkpoint_path, map_location=self.device)
        self.payload = payload
        self.model_config = ScratchDecoderConfig.model_validate(payload["config"])
        self.tokenizer = load_tokenizer(self.tokenizer_path)
        vocab_size = int(self.tokenizer.get_vocab_size(with_added_tokens=True))
        if vocab_size > self.model_config.vocab_size:
            raise ValueError(f"tokenizer vocab {vocab_size} exceeds model vocab {self.model_config.vocab_size}")
        saved_hash = str(payload.get("tokenizer_sha256") or "")
        actual_hash = sha256_file(self.tokenizer_path)
        if config.require_tokenizer_hash_match and saved_hash and saved_hash != actual_hash:
            raise ValueError("tokenizer hash does not match checkpoint metadata")
        self.model = MythosDecoderLM(self.model_config).to(self.device)
        self.model.load_state_dict(payload["model"])
        self.model.eval()
        self.started_at_unix = time.time()

    def _select_device(self, requested: str) -> "torch.device":
        if requested == "auto":
            return torch.device("cuda" if torch.cuda.is_available() else "cpu")
        if requested == "cuda" and not torch.cuda.is_available():
            raise RuntimeError("CUDA requested but unavailable")
        return torch.device(requested)

    def health(self) -> dict[str, Any]:
        return {
            "status": "ready",
            "model_name": self.config.model_name,
            "checkpoint_manifest": self.config.checkpoint_manifest,
            "checkpoint_step": self.manifest.step,
            "trained_tokens": self.manifest.trained_tokens,
            "tokenizer_path": str(self.tokenizer_path),
            "device": str(self.device),
            "uptime_seconds": round(time.time() - self.started_at_unix, 3),
            "scratch_only": True,
        }

    def render_prompt(self, messages: list[ChatMessage]) -> str:
        rendered = []
        for message in messages:
            role = message.role.strip().lower()
            if role not in {"system", "user", "assistant"}:
                raise ValueError(f"unsupported message role: {message.role}")
            rendered.append(f"{role.title()}:\n{message.content.strip()}")
        rendered.append("Assistant:\n")
        return "\n\n".join(rendered)

    def generate(self, request: ChatCompletionRequest) -> tuple[str, int, float]:
        started = time.perf_counter()
        prompt = self.render_prompt(request.messages)
        text, token_count = generate_text(
            model=self.model,
            tokenizer=self.tokenizer,
            prompt=prompt,
            device=self.device,
            config=GenerationConfig(max_new_tokens=request.max_tokens, temperature=request.temperature, top_k=request.top_k),
        )
        return text, token_count, (time.perf_counter() - started) * 1000


def create_app(config: NativeServingConfig | None = None) -> FastAPI:
    active_config = config or NativeServingConfig(
        checkpoint_manifest=os.environ.get("MYTHOS_CHECKPOINT_MANIFEST", ""),
        tokenizer_path=os.environ.get("MYTHOS_TOKENIZER_PATH", ""),
        model_name=os.environ.get("MYTHOS_MODEL_NAME", "mythos-scratch"),
        device=os.environ.get("MYTHOS_SERVING_DEVICE", "auto"),
        require_tokenizer_hash_match=os.environ.get("MYTHOS_REQUIRE_TOKENIZER_HASH_MATCH", "1") == "1",
        auth_enabled=os.environ.get("MYTHOS_AUTH_ENABLED", "1") == "1",
        quota_enabled=os.environ.get("MYTHOS_QUOTA_ENABLED", "1") == "1",
    )
    if not active_config.checkpoint_manifest:
        raise ValueError("MYTHOS_CHECKPOINT_MANIFEST is required")
    if not active_config.tokenizer_path:
        raise ValueError("MYTHOS_TOKENIZER_PATH is required")
    state = NativeServingState(active_config)
    app = FastAPI(title="Mythos Native Scratch Serving", version="1.0.0")
    if active_config.quota_enabled:
        install_quota(app)
    if active_config.auth_enabled:
        install_auth(app)
    install_observability(app)

    @app.get("/health/live")
    async def live() -> dict[str, str]:
        return {"status": "live"}

    @app.get("/health/ready")
    async def ready() -> dict[str, Any]:
        return state.health()

    @app.get("/v1/models")
    async def models() -> dict[str, Any]:
        return {"data": [{"id": active_config.model_name, "object": "model", "owned_by": "mythos"}]}

    @app.post("/v1/chat/completions")
    async def chat_completions(request: ChatCompletionRequest) -> Any:
        if request.model != active_config.model_name:
            raise HTTPException(status_code=404, detail=f"unknown model: {request.model}")
        if request.stream:
            return StreamingResponse(_stream_response(state, request), media_type="text/event-stream")
        text, token_count, latency_ms = state.generate(request)
        return {
            "id": f"chatcmpl-{uuid.uuid4()}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": active_config.model_name,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "length"}],
            "usage": {"completion_tokens": token_count, "total_tokens": token_count},
            "mythos": {"latency_ms": round(latency_ms, 3), "scratch_only": True},
        }

    return app


async def _stream_response(state: NativeServingState, request: ChatCompletionRequest) -> Any:
    text, token_count, latency_ms = state.generate(request)
    chunk_id = f"chatcmpl-{uuid.uuid4()}"
    for part in text.split():
        payload = {
            "id": chunk_id,
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": state.config.model_name,
            "choices": [{"index": 0, "delta": {"content": part + " "}, "finish_reason": None}],
        }
        yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
        await asyncio.sleep(0)
    final = {
        "id": chunk_id,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": state.config.model_name,
        "choices": [{"index": 0, "delta": {}, "finish_reason": "length"}],
        "usage": {"completion_tokens": token_count, "total_tokens": token_count},
        "mythos": {"latency_ms": round(latency_ms, 3), "scratch_only": True},
    }
    yield f"data: {json.dumps(final, ensure_ascii=False)}\n\n"
    yield "data: [DONE]\n\n"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Serve a Mythos scratch checkpoint with a native OpenAI-compatible API.")
    parser.add_argument("--checkpoint-manifest", required=True)
    parser.add_argument("--tokenizer-path", required=True)
    parser.add_argument("--model-name", default="mythos-scratch")
    parser.add_argument("--device", default="auto", choices=["auto", "cpu", "cuda"])
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8001)
    parser.add_argument("--no-auth", action="store_true")
    parser.add_argument("--no-quota", action="store_true")
    parser.add_argument("--allow-tokenizer-hash-mismatch", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    import uvicorn

    app = create_app(
        NativeServingConfig(
            checkpoint_manifest=args.checkpoint_manifest,
            tokenizer_path=args.tokenizer_path,
            model_name=args.model_name,
            device=args.device,
            require_tokenizer_hash_match=not args.allow_tokenizer_hash_mismatch,
            auth_enabled=not args.no_auth,
            quota_enabled=not args.no_quota,
        )
    )
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
