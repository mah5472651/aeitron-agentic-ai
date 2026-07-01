#!/usr/bin/env python
"""Tiny OpenAI-compatible server for a local Hugging Face Qwen/DeepSeek/Llama model."""

from __future__ import annotations

import asyncio
import argparse
import os
import sys
import time
from pathlib import Path
from typing import Any

import torch
import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from transformers import AutoModelForCausalLM, AutoTokenizer

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatCompletionRequest(BaseModel):
    model: str | None = None
    messages: list[ChatMessage]
    max_tokens: int = Field(default=512, ge=1, le=2048)
    temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    top_p: float = Field(default=0.95, ge=0.0, le=1.0)
    stream: bool = False


class ServerState:
    model_id: str = os.environ.get("PHASE16_HF_MODEL_ID", "Qwen/Qwen2.5-Coder-0.5B-Instruct")
    revision: str = os.environ.get("PHASE16_HF_REVISION", "ea3f2471cf1b1f0db85067f1ef93848e38e88c25")
    device: str = os.environ.get("PHASE16_HF_DEVICE", "cpu")
    tokenizer: Any | None = None
    model: Any | None = None
    loaded_at_unix: float | None = None


state = ServerState()
app = FastAPI(title="Phase 16 Local HF OpenAI Server", version="1.0.0")


def load_model() -> None:
    if state.model is not None and state.tokenizer is not None:
        return
    dtype = torch.float16 if state.device.startswith("cuda") else torch.float32
    if state.revision in {"", "main", "master", "latest"} and not Path(state.model_id).exists():
        raise ValueError("PHASE16_HF_REVISION must be pinned to an immutable commit hash for remote Hugging Face models.")
    state.tokenizer = AutoTokenizer.from_pretrained(
        state.model_id,
        revision=state.revision,
        trust_remote_code=False,
    )
    state.model = AutoModelForCausalLM.from_pretrained(
        state.model_id,
        revision=state.revision,
        dtype=dtype,
        low_cpu_mem_usage=True,
        trust_remote_code=False,
    )
    state.model.to(state.device)
    state.model.eval()
    state.loaded_at_unix = time.time()


@app.on_event("startup")
async def startup() -> None:
    await asyncio.to_thread(load_model)


@app.get("/health/ready")
async def health_ready() -> dict[str, Any]:
    return {
        "status": "ready" if state.model is not None else "loading",
        "model": state.model_id,
        "revision": state.revision,
        "device": state.device,
        "loaded_at_unix": state.loaded_at_unix,
    }


@app.get("/v1/models")
async def models() -> dict[str, Any]:
    return {"object": "list", "data": [{"id": state.model_id, "object": "model", "owned_by": "local-phase16"}]}


@app.post("/v1/chat/completions")
async def chat_completions(request: ChatCompletionRequest) -> dict[str, Any]:
    if request.stream:
        raise HTTPException(status_code=400, detail="streaming is not implemented in local HF server")
    if state.model is None or state.tokenizer is None:
        raise HTTPException(status_code=503, detail="model is not loaded")
    text = await asyncio.to_thread(generate_sync, request)
    created = int(time.time())
    return {
        "id": f"chatcmpl-phase16-{created}",
        "object": "chat.completion",
        "created": created,
        "model": state.model_id,
        "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
        "usage": {
            "prompt_tokens": 0,
            "completion_tokens": max(1, len(text) // 4),
            "total_tokens": max(1, len(text) // 4),
        },
    }


def generate_sync(request: ChatCompletionRequest) -> str:
    if state.model is None or state.tokenizer is None:
        raise RuntimeError("model is not loaded")
    messages = [{"role": item.role, "content": item.content} for item in request.messages]
    if hasattr(state.tokenizer, "apply_chat_template"):
        prompt = state.tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
    else:
        prompt = "\n".join(f"{item['role'].upper()}: {item['content']}" for item in messages) + "\nASSISTANT:"
    inputs = state.tokenizer(prompt, return_tensors="pt").to(state.device)
    do_sample = request.temperature > 0.0
    pad_token_id = state.tokenizer.eos_token_id
    if pad_token_id is None or int(pad_token_id) < 0:
        pad_token_id = state.tokenizer.pad_token_id
    if pad_token_id is None or int(pad_token_id) < 0:
        pad_token_id = 0
    with torch.inference_mode():
        output = state.model.generate(
            **inputs,
            max_new_tokens=request.max_tokens,
            do_sample=do_sample,
            temperature=max(request.temperature, 0.01),
            top_p=request.top_p,
            pad_token_id=int(pad_token_id),
        )
    generated = output[0, inputs["input_ids"].shape[-1] :]
    return state.tokenizer.decode(generated, skip_special_tokens=True).strip()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run local HF OpenAI-compatible model server.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8016)
    parser.add_argument("--model-id", default=os.environ.get("PHASE16_HF_MODEL_ID", state.model_id))
    parser.add_argument("--revision", default=os.environ.get("PHASE16_HF_REVISION", state.revision))
    parser.add_argument("--device", default=os.environ.get("PHASE16_HF_DEVICE", state.device))
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    state.model_id = args.model_id
    state.revision = args.revision
    state.device = args.device
    uvicorn.run(app, host=args.host, port=args.port, reload=False)


if __name__ == "__main__":
    main()
