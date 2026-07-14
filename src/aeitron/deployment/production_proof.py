"""Live production proof runner for Aeitron deployments.

This module does not fake production readiness. It verifies live dependencies
when URLs/secrets are supplied and marks missing dependencies as skipped in
validation mode or failed in strict mode.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import time
from pathlib import Path
from typing import Any

import httpx
from pydantic import Field

from src.aeitron.db.migration_runner import apply_migrations
from src.aeitron.evaluation.benchmark_pack import BenchmarkPackConfig, run_benchmark_pack
from src.aeitron.identity.quota import RedisQuotaStore
from src.aeitron.learning.storage import ObjectStoreConfig, verify_object_store_lifecycle
from src.aeitron.security.audit import run_security_audit
from src.aeitron.shared.schemas import StrictModel


class ProofCheckResult(StrictModel):
    name: str
    status: str
    required: bool
    duration_ms: float
    details: dict[str, Any] = Field(default_factory=dict)
    error: str = ""


class NativeServingLoadReport(StrictModel):
    status: str
    endpoint: str
    requests: int
    concurrency: int
    passed: int
    failed: int
    latency_ms_p50: float
    latency_ms_p95: float
    max_latency_ms: float
    timeout_count: int
    error_samples: list[str] = Field(default_factory=list)


class ProductionProofConfig(StrictModel):
    strict: bool = False
    output_dir: str = "artifacts/aeitron/production-proof"
    postgres_url: str | None = None
    apply_postgres_migrations: bool = False
    redis_url: str | None = None
    object_store_uri: str | None = None
    object_store_endpoint_url: str | None = None
    qdrant_url: str | None = None
    serving_url: str | None = None
    serving_api_key: str | None = None
    serving_model: str = "aeitron-scratch"
    load_test_requests: int = Field(default=0, ge=0, le=10_000)
    load_test_concurrency: int = Field(default=4, ge=1, le=512)
    load_test_timeout_seconds: float = Field(default=30.0, ge=1.0, le=300.0)
    benchmark_dir: str | None = None
    run_security_audit: bool = False
    strict_security_tools: bool = False


class ProductionProofReport(StrictModel):
    status: str
    mode: str
    checks: list[ProofCheckResult]
    recommendations: list[str]
    created_at_unix: float = Field(default_factory=time.time)

    def write(self, output_dir: str | Path) -> Path:
        root = Path(output_dir)
        root.mkdir(parents=True, exist_ok=True)
        path = root / "production_proof_report.json"
        path.write_text(json.dumps(self.model_dump(), indent=2, sort_keys=True), encoding="utf-8")
        write_markdown(self, root / "production_proof_report.md")
        return path


def _env(name: str) -> str | None:
    value = os.environ.get(name)
    return value if value else None


def config_from_env(args: argparse.Namespace) -> ProductionProofConfig:
    return ProductionProofConfig(
        strict=args.strict,
        output_dir=args.output_dir,
        postgres_url=args.postgres_url or _env("AEITRON_DATABASE_URL"),
        apply_postgres_migrations=args.apply_postgres_migrations,
        redis_url=args.redis_url or _env("AEITRON_REDIS_URL"),
        object_store_uri=args.object_store_uri or _env("AEITRON_OBJECT_STORE_URI"),
        object_store_endpoint_url=args.object_store_endpoint_url or _env("AEITRON_OBJECT_STORE_ENDPOINT_URL"),
        qdrant_url=args.qdrant_url or _env("AEITRON_QDRANT_URL"),
        serving_url=args.serving_url or _env("AEITRON_SERVING_URL"),
        serving_api_key=args.serving_api_key or _env("AEITRON_MODEL_API_KEY"),
        serving_model=args.serving_model,
        load_test_requests=args.load_test_requests,
        load_test_concurrency=args.load_test_concurrency,
        load_test_timeout_seconds=args.load_test_timeout_seconds,
        benchmark_dir=args.benchmark_dir or _env("AEITRON_BENCHMARK_DIR"),
        run_security_audit=args.run_security_audit,
        strict_security_tools=args.strict_security_tools,
    )


async def _check_postgres(config: ProductionProofConfig) -> ProofCheckResult:
    started = time.perf_counter()
    if not config.postgres_url:
        return _missing("postgres_migrations", config.strict, started, "AEITRON_DATABASE_URL or --postgres-url is required")
    try:
        result = await apply_migrations(config.postgres_url, dry_run=not config.apply_postgres_migrations)
        return _result("postgres_migrations", "passed", True, started, result)
    except Exception as exc:
        return _result("postgres_migrations", "failed", True, started, error=str(exc))


async def _check_redis_quota(config: ProductionProofConfig) -> ProofCheckResult:
    started = time.perf_counter()
    if not config.redis_url:
        return _missing("redis_quota", config.strict, started, "AEITRON_REDIS_URL or --redis-url is required")
    try:
        store = RedisQuotaStore(config.redis_url)
        allowed, remaining = await store.consume(
            "production-proof",
            now=time.time(),
            rate=1.0,
            capacity=5.0,
            cost=1.0,
        )
        return _result("redis_quota", "passed" if allowed else "failed", True, started, {"allowed": allowed, "remaining": remaining})
    except Exception as exc:
        return _result("redis_quota", "failed", True, started, error=str(exc))


async def _check_object_store(config: ProductionProofConfig) -> ProofCheckResult:
    started = time.perf_counter()
    if not config.object_store_uri:
        return _missing("object_store_lifecycle", config.strict, started, "AEITRON_OBJECT_STORE_URI or --object-store-uri is required")
    try:
        report = verify_object_store_lifecycle(
            config=ObjectStoreConfig(uri=config.object_store_uri, endpoint_url=config.object_store_endpoint_url),
            work_dir=Path(config.output_dir) / "object-store",
            key=f"production-proof/{int(time.time())}.json",
        )
        return _result("object_store_lifecycle", report.status, True, started, report.model_dump())
    except Exception as exc:
        return _result("object_store_lifecycle", "failed", True, started, error=str(exc))


async def _check_qdrant(config: ProductionProofConfig) -> ProofCheckResult:
    started = time.perf_counter()
    if not config.qdrant_url:
        return _missing("qdrant_health", config.strict, started, "AEITRON_QDRANT_URL or --qdrant-url is required")
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(f"{config.qdrant_url.rstrip('/')}/collections")
        response.raise_for_status()
        return _result("qdrant_health", "passed", True, started, {"status_code": response.status_code, "collections": response.json()})
    except Exception as exc:
        return _result("qdrant_health", "failed", True, started, error=str(exc))


async def _check_serving_health(config: ProductionProofConfig) -> ProofCheckResult:
    started = time.perf_counter()
    if not config.serving_url:
        return _missing("native_serving_health", config.strict, started, "AEITRON_SERVING_URL or --serving-url is required")
    try:
        headers = _auth_headers(config)
        async with httpx.AsyncClient(timeout=10.0) as client:
            ready = await client.get(f"{config.serving_url.rstrip('/')}/health/ready", headers=headers)
            models = await client.get(f"{config.serving_url.rstrip('/')}/v1/models", headers=headers)
        ready.raise_for_status()
        models.raise_for_status()
        return _result(
            "native_serving_health",
            "passed",
            True,
            started,
            {"ready": ready.json(), "models": models.json()},
        )
    except Exception as exc:
        return _result("native_serving_health", "failed", True, started, error=str(exc))


async def _check_serving_load(config: ProductionProofConfig) -> ProofCheckResult:
    started = time.perf_counter()
    if not config.serving_url:
        return _missing("native_serving_load", config.strict, started, "AEITRON_SERVING_URL or --serving-url is required")
    if config.load_test_requests <= 0:
        return _result("native_serving_load", "skipped" if not config.strict else "failed", config.strict, started, {"reason": "--load-test-requests is 0"})
    try:
        report = await run_native_serving_load_test(
            endpoint=config.serving_url,
            model=config.serving_model,
            api_key=config.serving_api_key,
            requests=config.load_test_requests,
            concurrency=config.load_test_concurrency,
            timeout_seconds=config.load_test_timeout_seconds,
        )
        return _result("native_serving_load", report.status, True, started, report.model_dump())
    except Exception as exc:
        return _result("native_serving_load", "failed", True, started, error=str(exc))


def _check_benchmarks(config: ProductionProofConfig) -> ProofCheckResult:
    started = time.perf_counter()
    if not config.benchmark_dir:
        return _missing("benchmark_pack", config.strict, started, "AEITRON_BENCHMARK_DIR or --benchmark-dir is required")
    root = Path(config.benchmark_dir)
    try:
        report = run_benchmark_pack(
            BenchmarkPackConfig(
                human_eval_path=str(root / "humaneval.jsonl"),
                mbpp_path=str(root / "mbpp.jsonl"),
                swe_bench_path=str(root / "swe_bench_style.jsonl"),
                cyberseceval_path=str(root / "cyberseceval_style.jsonl"),
                custom_security_path=str(root / "aeitron_security.jsonl"),
                strict=config.strict,
                production=config.strict,
            ),
            output_dir=Path(config.output_dir) / "benchmarks",
        )
        return _result("benchmark_pack", report.status, True, started, report.model_dump())
    except Exception as exc:
        return _result("benchmark_pack", "failed", True, started, error=str(exc))


def _check_security_audit(config: ProductionProofConfig) -> ProofCheckResult:
    started = time.perf_counter()
    if not config.run_security_audit:
        return _result("security_audit", "skipped" if not config.strict else "failed", config.strict, started, {"reason": "--run-security-audit not set"})
    try:
        report = run_security_audit(
            output_dir=Path(config.output_dir) / "security-audit",
            strict_external_tools=config.strict_security_tools,
        )
        return _result("security_audit", report.status, True, started, report.model_dump())
    except Exception as exc:
        return _result("security_audit", "failed", True, started, error=str(exc))


async def run_production_proof(config: ProductionProofConfig) -> ProductionProofReport:
    checks = [
        await _check_postgres(config),
        await _check_redis_quota(config),
        await _check_object_store(config),
        await _check_qdrant(config),
        await _check_serving_health(config),
        await _check_serving_load(config),
        _check_benchmarks(config),
        _check_security_audit(config),
    ]
    failed = [item for item in checks if item.status == "failed"]
    skipped_required = [item for item in checks if item.required and item.status == "skipped"]
    recommendations = []
    for item in failed + skipped_required:
        recommendations.append(f"{item.name}: {item.error or item.details.get('reason') or 'check did not pass'}")
    report = ProductionProofReport(
        status="passed" if not failed and not skipped_required else "failed",
        mode="strict" if config.strict else "validation",
        checks=checks,
        recommendations=recommendations,
    )
    report.write(config.output_dir)
    return report


async def run_native_serving_load_test(
    *,
    endpoint: str,
    model: str,
    api_key: str | None,
    requests: int,
    concurrency: int,
    timeout_seconds: float,
) -> NativeServingLoadReport:
    semaphore = asyncio.Semaphore(concurrency)
    latencies: list[float] = []
    errors: list[str] = []
    timeout_count = 0
    headers = _auth_headers_value(api_key)
    timeout = httpx.Timeout(timeout_seconds)

    async def one(client: httpx.AsyncClient, index: int) -> None:
        nonlocal timeout_count
        async with semaphore:
            started = time.perf_counter()
            try:
                response = await client.post(
                    f"{endpoint.rstrip('/')}/v1/chat/completions",
                    headers=headers,
                    json={
                        "model": model,
                        "messages": [{"role": "user", "content": f"Return a concise safe coding checklist. Request {index}."}],
                        "temperature": 0.0,
                        "max_tokens": 32,
                        "stream": False,
                    },
                )
                response.raise_for_status()
                payload = response.json()
                if not payload.get("choices"):
                    raise RuntimeError("response missing choices")
                latencies.append((time.perf_counter() - started) * 1000)
            except httpx.TimeoutException as exc:
                timeout_count += 1
                errors.append(f"timeout:{exc}")
            except Exception as exc:
                errors.append(str(exc)[:240])

    async with httpx.AsyncClient(timeout=timeout) as client:
        await asyncio.gather(*(one(client, index) for index in range(requests)))
    passed = len(latencies)
    sorted_latencies = sorted(latencies)
    p50 = statistics.median(sorted_latencies) if sorted_latencies else 0.0
    p95 = sorted_latencies[min(len(sorted_latencies) - 1, int(len(sorted_latencies) * 0.95))] if sorted_latencies else 0.0
    status = "passed" if passed == requests and timeout_count == 0 else "failed"
    return NativeServingLoadReport(
        status=status,
        endpoint=endpoint,
        requests=requests,
        concurrency=concurrency,
        passed=passed,
        failed=requests - passed,
        latency_ms_p50=round(p50, 3),
        latency_ms_p95=round(p95, 3),
        max_latency_ms=round(max(sorted_latencies) if sorted_latencies else 0.0, 3),
        timeout_count=timeout_count,
        error_samples=errors[:10],
    )


def _auth_headers(config: ProductionProofConfig) -> dict[str, str]:
    return _auth_headers_value(config.serving_api_key)


def _auth_headers_value(api_key: str | None) -> dict[str, str]:
    return {"Authorization": f"Bearer {api_key}"} if api_key else {}


def _missing(name: str, strict: bool, started: float, reason: str) -> ProofCheckResult:
    return _result(name, "failed" if strict else "skipped", strict, started, {"reason": reason})


def _result(
    name: str,
    status: str,
    required: bool,
    started: float,
    details: dict[str, Any] | None = None,
    *,
    error: str = "",
) -> ProofCheckResult:
    return ProofCheckResult(
        name=name,
        status=status,
        required=required,
        duration_ms=round((time.perf_counter() - started) * 1000, 3),
        details=details or {},
        error=error,
    )


def write_markdown(report: ProductionProofReport, path: str | Path) -> Path:
    target = Path(path)
    lines = [
        "# Aeitron Production Proof Report",
        "",
        f"- status: {report.status}",
        f"- mode: {report.mode}",
        "",
        "| check | status | required | duration_ms | detail |",
        "|---|---|---:|---:|---|",
    ]
    for check in report.checks:
        detail = check.error or check.details.get("reason") or ""
        lines.append(f"| {check.name} | {check.status} | {str(check.required).lower()} | {check.duration_ms:.3f} | {detail} |")
    if report.recommendations:
        lines.extend(["", "## Recommendations", ""])
        for item in report.recommendations:
            lines.append(f"- {item}")
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return target


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run live Aeitron production proof checks.")
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--output-dir", default="artifacts/aeitron/production-proof")
    parser.add_argument("--postgres-url")
    parser.add_argument("--apply-postgres-migrations", action="store_true")
    parser.add_argument("--redis-url")
    parser.add_argument("--object-store-uri")
    parser.add_argument("--object-store-endpoint-url")
    parser.add_argument("--qdrant-url")
    parser.add_argument("--serving-url")
    parser.add_argument("--serving-api-key")
    parser.add_argument("--serving-model", default="aeitron-scratch")
    parser.add_argument("--load-test-requests", type=int, default=0)
    parser.add_argument("--load-test-concurrency", type=int, default=4)
    parser.add_argument("--load-test-timeout-seconds", type=float, default=30.0)
    parser.add_argument("--benchmark-dir")
    parser.add_argument("--run-security-audit", action="store_true")
    parser.add_argument("--strict-security-tools", action="store_true")
    return parser.parse_args()


def main() -> None:
    config = config_from_env(parse_args())
    report = asyncio.run(run_production_proof(config))
    print(json.dumps(report.model_dump(), indent=2, sort_keys=True))
    if report.status != "passed":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
