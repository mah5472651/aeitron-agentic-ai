"""Production observability primitives for Mythos."""

from __future__ import annotations

import json
import logging
import os
import time
from collections import defaultdict
from dataclasses import dataclass, field
from threading import RLock
from typing import Any, Awaitable, Callable

from fastapi import FastAPI, Request
from fastapi.responses import Response
from starlette.middleware.base import BaseHTTPMiddleware


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts_unix": time.time(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key in ["method", "path", "status_code", "duration_ms", "user_id", "request_id"]:
            if hasattr(record, key):
                payload[key] = getattr(record, key)
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, sort_keys=True)


def configure_logging(level: int = logging.INFO) -> None:
    root = logging.getLogger("mythos")
    if not root.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(JsonFormatter())
        root.addHandler(handler)
    root.setLevel(level)


@dataclass
class MetricsRegistry:
    counters: dict[str, float] = field(default_factory=lambda: defaultdict(float))
    histograms: dict[str, list[float]] = field(default_factory=lambda: defaultdict(list))
    lock: RLock = field(default_factory=RLock)

    def inc(self, name: str, value: float = 1.0, **labels: str) -> None:
        key = self._key(name, labels)
        with self.lock:
            self.counters[key] += value

    def observe(self, name: str, value: float, **labels: str) -> None:
        key = self._key(name, labels)
        with self.lock:
            self.histograms[key].append(value)

    def render_prometheus(self) -> str:
        lines = [
            "# HELP mythos_http_requests_total Total HTTP requests.",
            "# TYPE mythos_http_requests_total counter",
        ]
        with self.lock:
            for key, value in sorted(self.counters.items()):
                lines.append(f"{key} {value}")
            lines.extend(
                [
                    "# HELP mythos_http_request_duration_ms HTTP request duration in milliseconds.",
                    "# TYPE mythos_http_request_duration_ms summary",
                ]
            )
            for key, values in sorted(self.histograms.items()):
                if not values:
                    continue
                lines.append(f'{self._with_labels(key, {"quantile": "avg"})} {sum(values) / len(values):.6f}')
                lines.append(f'{self._with_labels(key, {"quantile": "max"})} {max(values):.6f}')
                lines.append(f"{self._count_key(key)} {len(values)}")
        return "\n".join(lines) + "\n"

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return {
                "counters": dict(self.counters),
                "histograms": {key: {"count": len(values), "max": max(values) if values else 0.0} for key, values in self.histograms.items()},
            }

    def _key(self, name: str, labels: dict[str, str]) -> str:
        if not labels:
            return name
        label_text = ",".join(f'{key}="{value}"' for key, value in sorted(labels.items()))
        return f"{name}{{{label_text}}}"

    def _with_labels(self, metric_key: str, labels: dict[str, str]) -> str:
        extra = ",".join(f'{key}="{value}"' for key, value in sorted(labels.items()))
        if "{" not in metric_key:
            return f"{metric_key}{{{extra}}}"
        return metric_key[:-1] + f",{extra}" + "}"

    def _count_key(self, metric_key: str) -> str:
        if "{" not in metric_key:
            return f"{metric_key}_count"
        name, labels = metric_key.split("{", 1)
        return f"{name}_count{{{labels}"


METRICS = MetricsRegistry()
LOGGER = logging.getLogger("mythos.gateway")


class ObservabilityMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        started = time.perf_counter()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            return response
        finally:
            duration_ms = (time.perf_counter() - started) * 1000
            route = request.url.path
            method = request.method
            METRICS.inc("mythos_http_requests_total", method=method, path=route, status=str(status_code))
            METRICS.observe("mythos_http_request_duration_ms", duration_ms, method=method, path=route)
            LOGGER.info(
                "http_request",
                extra={
                    "method": method,
                    "path": route,
                    "status_code": status_code,
                    "duration_ms": round(duration_ms, 3),
                    "user_id": getattr(request.state, "user_id", ""),
                },
            )


def install_observability(app: FastAPI) -> None:
    configure_logging()
    app.add_middleware(ObservabilityMiddleware)
    install_tracing(app)


def install_tracing(app: FastAPI) -> None:
    endpoint = os.environ.get("MYTHOS_OTEL_EXPORTER_OTLP_ENDPOINT")
    if not endpoint:
        return
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:
        logging.getLogger("mythos.gateway").warning("opentelemetry_not_installed")
        return
    provider = TracerProvider(resource=Resource.create({"service.name": os.environ.get("MYTHOS_SERVICE_NAME", "mythos-api")}))
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
    trace.set_tracer_provider(provider)
    FastAPIInstrumentor.instrument_app(app)
