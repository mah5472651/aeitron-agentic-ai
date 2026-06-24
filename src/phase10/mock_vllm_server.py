#!/usr/bin/env python
"""Tiny OpenAI-compatible mock vLLM server for local gateway smoke tests."""

from __future__ import annotations

import argparse
import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


class MockVllmHandler(BaseHTTPRequestHandler):
    server_version = "MockVLLM/1.0"

    def log_message(self, fmt: str, *args: Any) -> None:
        return

    def _json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path == "/health":
            self._json(200, {"status": "ok", "mock": True})
            return
        if self.path == "/v1/models":
            self._json(200, {"object": "list", "data": [{"id": "security-coder", "object": "model"}]})
            return
        self._json(404, {"error": "not_found"})

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            self._json(400, {"error": "invalid_json"})
            return
        if self.path != "/v1/chat/completions":
            self._json(404, {"error": "not_found"})
            return
        model = payload.get("model") or "security-coder"
        content = "mock-vllm: local gateway smoke response"
        response = {
            "id": f"chatcmpl-mock-{time.time_ns()}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }
        self._json(200, response)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a local mock vLLM server.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    server = ThreadingHTTPServer((args.host, args.port), MockVllmHandler)
    print(f"mock vLLM listening on http://{args.host}:{args.port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()

