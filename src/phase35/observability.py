#!/usr/bin/env python
"""Structured logging and Prometheus-style metrics for the API surface."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from collections import defaultdict
from pathlib import Path
from threading import Lock
from typing import Any, Awaitable, Callable

from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse, Response
from starlette.middleware.base import BaseHTTPMiddleware

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


PHASE_PREFIXES = {
    "/v1/chat": "phase11",
    "/v1/agent": "phase11",
    "/v1/quality": "phase10",
    "/v1/scorecard": "phase14",
    "/v1/phase16": "phase16",
    "/v1/gpu-readiness": "phase17",
    "/v1/model-quality": "phase18",
    "/v1/verifier": "phase19",
    "/v1/taskgraph": "phase20",
    "/v1/main-agent-v2": "phase24",
    "/v1/auth": "phase34",
    "/metrics": "phase35",
}


def phase_for_path(path: str) -> str:
    for prefix, phase in sorted(PHASE_PREFIXES.items(), key=lambda item: len(item[0]), reverse=True):
        if path.startswith(prefix):
            return phase
    if path.startswith("/health"):
        return "health"
    return "unknown"


class MetricsRegistry:
    def __init__(self) -> None:
        self._lock = Lock()
        self.request_count: dict[tuple[str, str, int], int] = defaultdict(int)
        self.error_count: dict[tuple[str, str], int] = defaultdict(int)
        self.duration_sum: dict[tuple[str, str], float] = defaultdict(float)
        self.duration_count: dict[tuple[str, str], int] = defaultdict(int)
        self.phase_events: dict[tuple[str, str], int] = defaultdict(int)
        self.started_at_unix = time.time()

    def record_request(self, *, phase: str, method: str, status_code: int, duration_ms: float) -> None:
        with self._lock:
            self.request_count[(phase, method, status_code)] += 1
            self.duration_sum[(phase, method)] += duration_ms / 1000.0
            self.duration_count[(phase, method)] += 1
            if status_code >= 500:
                self.error_count[(phase, method)] += 1

    def record_event(self, *, phase: str, event: str) -> None:
        with self._lock:
            self.phase_events[(phase, event)] += 1

    def render_prometheus(self) -> str:
        lines = [
            "# HELP mythos_api_requests_total Total API requests.",
            "# TYPE mythos_api_requests_total counter",
        ]
        with self._lock:
            for (phase, method, status), value in sorted(self.request_count.items()):
                lines.append(f'mythos_api_requests_total{{phase="{phase}",method="{method}",status="{status}"}} {value}')
            lines.extend(
                [
                    "# HELP mythos_api_request_duration_seconds_sum Sum of request durations.",
                    "# TYPE mythos_api_request_duration_seconds_sum counter",
                ]
            )
            for (phase, method), value in sorted(self.duration_sum.items()):
                lines.append(f'mythos_api_request_duration_seconds_sum{{phase="{phase}",method="{method}"}} {value:.6f}')
            lines.append("# HELP mythos_api_request_duration_seconds_count Count of request durations.")
            lines.append("# TYPE mythos_api_request_duration_seconds_count counter")
            for (phase, method), value in sorted(self.duration_count.items()):
                lines.append(f'mythos_api_request_duration_seconds_count{{phase="{phase}",method="{method}"}} {value}')
            lines.append("# HELP mythos_api_errors_total Total 5xx API errors.")
            lines.append("# TYPE mythos_api_errors_total counter")
            for (phase, method), value in sorted(self.error_count.items()):
                lines.append(f'mythos_api_errors_total{{phase="{phase}",method="{method}"}} {value}')
            lines.append("# HELP mythos_phase_events_total Phase-level custom events.")
            lines.append("# TYPE mythos_phase_events_total counter")
            for (phase, event), value in sorted(self.phase_events.items()):
                lines.append(f'mythos_phase_events_total{{phase="{phase}",event="{event}"}} {value}')
            lines.append("# HELP mythos_observability_uptime_seconds API observability uptime.")
            lines.append("# TYPE mythos_observability_uptime_seconds gauge")
            lines.append(f"mythos_observability_uptime_seconds {time.time() - self.started_at_unix:.6f}")
        return "\n".join(lines) + "\n"


REGISTRY = MetricsRegistry()


class StructuredLogger:
    def __init__(self, path: str | Path = ROOT / "artifacts" / "phase35" / "api-events.jsonl") -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()

    async def write(self, event: dict[str, Any]) -> None:
        event = {"ts_unix": time.time(), **event}
        line = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
        async with self._lock:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")


LOGGER = StructuredLogger()


class ObservabilityMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: FastAPI, logger: StructuredLogger | None = None, registry: MetricsRegistry | None = None) -> None:
        super().__init__(app)
        self.logger = logger or LOGGER
        self.registry = registry or REGISTRY

    async def dispatch(self, request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        started = time.perf_counter()
        phase = phase_for_path(request.url.path)
        status_code = 500
        error: str | None = None
        try:
            response = await call_next(request)
            status_code = response.status_code
            return response
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            duration_ms = (time.perf_counter() - started) * 1000
            self.registry.record_request(phase=phase, method=request.method, status_code=status_code, duration_ms=duration_ms)
            await self.logger.write(
                {
                    "event": "api_request",
                    "phase": phase,
                    "method": request.method,
                    "path": request.url.path,
                    "status_code": status_code,
                    "duration_ms": round(duration_ms, 3),
                    "error": error,
                }
            )


def install_observability(app: FastAPI) -> None:
    app.add_middleware(ObservabilityMiddleware)


def metrics_response() -> PlainTextResponse:
    return PlainTextResponse(REGISTRY.render_prometheus(), media_type="text/plain; version=0.0.4")


def latest_log_tail(limit: int = 50) -> dict[str, Any]:
    path = LOGGER.path
    if not path.exists():
        return {"available": False, "path": str(path), "events": []}
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[-limit:]
    events = []
    for line in lines:
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return {"available": True, "path": str(path), "events": events}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Inspect Phase 35 observability state.")
    parser.add_argument("--tail", type=int, default=20)
    parser.add_argument("--metrics", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.metrics:
        print(REGISTRY.render_prometheus())
    else:
        print(json.dumps(latest_log_tail(args.tail), indent=2))


if __name__ == "__main__":
    main()
