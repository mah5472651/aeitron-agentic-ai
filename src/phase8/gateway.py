#!/usr/bin/env python
"""FastAPI gateway for vLLM with SSE streaming, priority lanes, and prompt routing."""

from __future__ import annotations

import argparse
import asyncio
import itertools
import json
import os
import re
import sys
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from enum import IntEnum
from pathlib import Path
from typing import Any, AsyncIterator
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field


VLLM_BASE_URL = os.environ.get("VLLM_BASE_URL", "http://vllm:8000")
SERVED_MODEL_NAME = os.environ.get("SERVED_MODEL_NAME", "security-coder")
QUEUE_WORKERS = int(os.environ.get("GATEWAY_WORKERS", "8"))
MAX_QUEUE_SIZE = int(os.environ.get("MAX_QUEUE_SIZE", "2048"))


class Lane(IntEnum):
    CODE_EXECUTION = 0
    CHAT = 1
    BATCH = 2


class RequestKind(str):
    VULNERABILITY_ANALYSIS = "vulnerability_analysis"
    CODE_GENERATION = "code_generation"
    AGENTIC_REASONING = "agentic_reasoning"
    CHAT = "chat"


class ChatMessage(BaseModel):
    role: str
    content: str


class GatewayRequest(BaseModel):
    model: str | None = None
    messages: list[ChatMessage] = Field(default_factory=list)
    prompt: str | None = None
    stream: bool = False
    max_tokens: int = 1024
    agentic: bool = False
    priority: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


@dataclass
class QueueJob:
    request_id: str
    payload: dict[str, Any]
    lane: Lane
    timeout_s: float
    stream: bool
    future: asyncio.Future[Any]
    stream_queue: asyncio.Queue[str | None] | None = None
    enqueued_at: float = field(default_factory=time.monotonic)


class PromptRouter:
    VULN_RE = re.compile(
        r"\b(vulnerab\w*|exploit\w*|cve|rce|xss|sqli|injection|overflow|use-after-free|sandbox escape)\b",
        re.IGNORECASE,
    )
    CODE_RE = re.compile(r"\b(write|implement|generate|refactor|fix|patch|function|class|api|code)\b", re.IGNORECASE)
    AGENTIC_RE = re.compile(r"\b(agent|tool call|multi-step|plan|execute|sandbox|orchestrate)\b", re.IGNORECASE)

    def detect(self, request: GatewayRequest) -> str:
        text = request.prompt or "\n".join(message.content for message in request.messages)
        if request.agentic or self.AGENTIC_RE.search(text):
            return RequestKind.AGENTIC_REASONING
        if self.VULN_RE.search(text):
            return RequestKind.VULNERABILITY_ANALYSIS
        if self.CODE_RE.search(text):
            return RequestKind.CODE_GENERATION
        return RequestKind.CHAT

    def lane_for(self, request: GatewayRequest, kind: str) -> Lane:
        if request.priority == "code_execution" or kind in {RequestKind.AGENTIC_REASONING, RequestKind.VULNERABILITY_ANALYSIS}:
            return Lane.CODE_EXECUTION
        if request.priority == "batch":
            return Lane.BATCH
        return Lane.CHAT

    def generation_config(self, kind: str) -> dict[str, Any]:
        if kind == RequestKind.VULNERABILITY_ANALYSIS:
            return {"temperature": 0.1, "top_p": 0.9}
        if kind == RequestKind.CODE_GENERATION:
            return {"temperature": 0.2, "top_p": 0.95}
        if kind == RequestKind.AGENTIC_REASONING:
            return {
                "temperature": 0.4,
                "top_p": 0.95,
                "stop": ["<|tool_call|>", "<|tool_result|>", "<|end_tool_call|>"],
            }
        return {"temperature": 0.7, "top_p": 0.95}


class VllmClient:
    def __init__(self, base_url: str) -> None:
        parsed = urlparse(base_url.rstrip("/"))
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("VLLM_BASE_URL must be an absolute http:// or https:// URL")
        self.base_url = base_url.rstrip("/")
        self.client = httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=5.0))

    async def health(self) -> bool:
        try:
            response = await self.client.get(f"{self.base_url}/health", timeout=2.0)
            return response.status_code < 500
        except httpx.HTTPError:
            return False

    async def aclose(self) -> None:
        await self.client.aclose()

    async def complete(self, payload: dict[str, Any], timeout_s: float) -> dict[str, Any]:
        response = await self.client.post(
            f"{self.base_url}/v1/chat/completions",
            json=payload,
            timeout=timeout_s,
        )
        response.raise_for_status()
        return response.json()

    async def stream(self, payload: dict[str, Any], timeout_s: float) -> AsyncIterator[str]:
        async with self.client.stream(
            "POST",
            f"{self.base_url}/v1/chat/completions",
            json=payload,
            timeout=timeout_s,
        ) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if line:
                    yield line if line.startswith("data:") else f"data: {line}"


class PriorityScheduler:
    def __init__(self, client: VllmClient, max_size: int, workers: int) -> None:
        self.client = client
        self.queue: asyncio.PriorityQueue[tuple[int, int, QueueJob]] = asyncio.PriorityQueue(maxsize=max_size)
        self.counter = itertools.count()
        self.workers = workers
        self.tasks: list[asyncio.Task[None]] = []

    async def start(self) -> None:
        if not self.tasks:
            self.tasks = [asyncio.create_task(self._worker(), name=f"gateway-worker-{i}") for i in range(self.workers)]

    async def stop(self) -> None:
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        self.tasks.clear()

    async def submit(self, job: QueueJob) -> None:
        await self.queue.put((int(job.lane), next(self.counter), job))

    async def _worker(self) -> None:
        while True:
            _lane, _seq, job = await self.queue.get()
            try:
                if job.stream:
                    stream_queue = job.stream_queue
                    if stream_queue is None:
                        raise RuntimeError("streaming job is missing its stream queue")
                    try:
                        async with asyncio.timeout(job.timeout_s):
                            async for chunk in self.client.stream(job.payload, job.timeout_s):
                                await stream_queue.put(chunk + "\n\n")
                        await stream_queue.put("data: [DONE]\n\n")
                        job.future.set_result({"ok": True})
                    except Exception as exc:
                        await stream_queue.put(f"event: error\ndata: {json.dumps({'error': str(exc)})}\n\n")
                        if not job.future.done():
                            job.future.set_result({"ok": False, "error": str(exc)})
                    finally:
                        await stream_queue.put(None)
                else:
                    try:
                        async with asyncio.timeout(job.timeout_s):
                            result = await self.client.complete(job.payload, job.timeout_s)
                        job.future.set_result(result)
                    except Exception as exc:
                        job.future.set_exception(exc)
            finally:
                self.queue.task_done()


router = PromptRouter()
vllm_client = VllmClient(VLLM_BASE_URL)
scheduler = PriorityScheduler(vllm_client, MAX_QUEUE_SIZE, QUEUE_WORKERS)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    await scheduler.start()
    try:
        yield
    finally:
        await scheduler.stop()
        await vllm_client.aclose()


app = FastAPI(title="Phase 8 vLLM Gateway", version="1.0.0", lifespan=lifespan)


@app.get("/health/live")
async def live() -> dict[str, str]:
    return {"status": "live"}


@app.get("/health/ready")
async def ready() -> JSONResponse:
    ok = await vllm_client.health()
    return JSONResponse(status_code=200 if ok else 503, content={"status": "ready" if ok else "not_ready"})


@app.get("/metrics")
async def metrics() -> dict[str, Any]:
    return {
        "queue_size": scheduler.queue.qsize(),
        "workers": scheduler.workers,
        "vllm_base_url": VLLM_BASE_URL,
    }


@app.post("/v1/chat/completions")
async def chat_completions(raw_request: Request) -> Any:
    body = await raw_request.json()
    request = GatewayRequest.model_validate(body)
    kind = router.detect(request)
    lane = router.lane_for(request, kind)
    timeout_s = 120.0 if kind == RequestKind.AGENTIC_REASONING or request.agentic else 30.0
    payload = build_vllm_payload(request, kind)
    loop = asyncio.get_running_loop()
    request_id = f"req-{time.time_ns()}"
    if request.stream:
        stream_queue: asyncio.Queue[str | None] = asyncio.Queue()
        future: asyncio.Future[Any] = loop.create_future()
        await scheduler.submit(
            QueueJob(
                request_id=request_id,
                payload=payload,
                lane=lane,
                timeout_s=timeout_s,
                stream=True,
                future=future,
                stream_queue=stream_queue,
            )
        )
        return StreamingResponse(
            sse_iter(stream_queue),
            media_type="text/event-stream",
            headers={"X-Request-ID": request_id, "X-Route-Kind": kind, "X-Priority-Lane": lane.name},
        )
    future = loop.create_future()
    await scheduler.submit(
        QueueJob(
            request_id=request_id,
            payload=payload,
            lane=lane,
            timeout_s=timeout_s,
            stream=False,
            future=future,
        )
    )
    try:
        result = await future
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=exc.response.status_code, detail=exc.response.text) from exc
    except Exception as exc:
        raise HTTPException(status_code=504, detail=str(exc)) from exc
    return JSONResponse(
        content=result,
        headers={"X-Request-ID": request_id, "X-Route-Kind": kind, "X-Priority-Lane": lane.name},
    )


def build_vllm_payload(request: GatewayRequest, kind: str) -> dict[str, Any]:
    messages = [message.model_dump() for message in request.messages]
    if not messages and request.prompt:
        messages = [{"role": "user", "content": request.prompt}]
    payload = {
        "model": request.model or SERVED_MODEL_NAME,
        "messages": messages,
        "stream": request.stream,
        "max_tokens": request.max_tokens,
    }
    payload.update(router.generation_config(kind))
    return payload


async def sse_iter(queue: asyncio.Queue[str | None]) -> AsyncIterator[str]:
    while True:
        item = await queue.get()
        if item is None:
            return
        yield item


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Phase 8 FastAPI gateway.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    uvicorn.run("src.phase8.gateway:app", host=args.host, port=args.port, reload=False)


if __name__ == "__main__":
    main()
