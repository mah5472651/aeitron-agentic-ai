#!/usr/bin/env python
"""Phase 10 deployment doctor and end-to-end smoke runner.

The runner is intentionally non-destructive. It checks local code health, core
Python dependencies, tokenizer artifacts, in-process swarm/evaluation smoke
paths, and optionally live infrastructure services.
"""

from __future__ import annotations

import argparse
import asyncio
import compileall
import importlib.util
import json
import platform
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


OK = "ok"
WARN = "warn"
FAIL = "fail"
SKIP = "skip"


@dataclass
class CheckResult:
    name: str
    status: str
    message: str
    duration_ms: float
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class SmokeReport:
    run_id: str
    started_at_unix: float
    duration_ms: float
    results: list[CheckResult]

    @property
    def passed(self) -> bool:
        return not any(item.status == FAIL for item in self.results)

    def summary(self) -> dict[str, int]:
        counts = {OK: 0, WARN: 0, FAIL: 0, SKIP: 0}
        for result in self.results:
            counts[result.status] = counts.get(result.status, 0) + 1
        return counts


async def timed_check(name: str, fn: Callable[[], Awaitable[CheckResult]]) -> CheckResult:
    started = time.perf_counter()
    try:
        result = await fn()
        result.duration_ms = (time.perf_counter() - started) * 1000
        return result
    except Exception as exc:
        return CheckResult(
            name=name,
            status=FAIL,
            message=f"{type(exc).__name__}: {exc}",
            duration_ms=(time.perf_counter() - started) * 1000,
        )


def result(name: str, status: str, message: str, details: dict[str, Any] | None = None) -> CheckResult:
    return CheckResult(name=name, status=status, message=message, duration_ms=0.0, details=details or {})


async def check_python() -> CheckResult:
    version = sys.version_info
    ok = version >= (3, 11)
    return result(
        "python_runtime",
        OK if ok else FAIL,
        f"Python {platform.python_version()} on {platform.system()}",
        {"executable": sys.executable, "version": platform.python_version()},
    )


async def check_compileall() -> CheckResult:
    ok = await asyncio.to_thread(compileall.compile_dir, str(ROOT / "src"), quiet=1)
    return result("compileall", OK if ok else FAIL, "All Python files compile." if ok else "Compilation failed.")


async def check_required_files() -> CheckResult:
    required = [
        "src/phase1/callgraph_extractor.py",
        "src/phase1/train_code_bpe_tokenizer.py",
        "src/phase2/docker_sandbox_engine.py",
        "src/phase3/rejection_sampling_pipeline.py",
        "src/phase4/swarm_orchestrator.py",
        "src/phase5/self_healing_runtime.py",
        "src/phase6/redis_quota_engine.py",
        "src/phase7/grpo_training_loop.py",
        "src/phase8/gateway.py",
        "src/phase9/evaluate.py",
    ]
    missing = [path for path in required if not (ROOT / path).exists()]
    return result(
        "phase_files",
        FAIL if missing else OK,
        "All phase entrypoints exist." if not missing else f"Missing {len(missing)} required files.",
        {"missing": missing},
    )


async def check_python_packages() -> CheckResult:
    packages = [
        "tokenizers",
        "tree_sitter",
        "docker",
        "redis",
        "pydantic",
        "fastapi",
        "uvicorn",
        "httpx",
        "bandit",
    ]
    optional_heavy = ["torch", "transformers", "accelerate", "trl", "deepspeed", "wandb", "vllm", "awq"]
    missing = [package for package in packages if importlib.util.find_spec(package) is None]
    missing_heavy = [package for package in optional_heavy if importlib.util.find_spec(package) is None]
    status = FAIL if missing else (WARN if missing_heavy else OK)
    message = "Core packages installed."
    if missing:
        message = f"Missing core packages: {', '.join(missing)}"
    elif missing_heavy:
        message = f"Core packages installed; heavy ML/runtime packages missing: {', '.join(missing_heavy)}"
    return result("python_packages", status, message, {"missing": missing, "missing_heavy": missing_heavy})


async def check_tokenizer(tokenizer_path: Path | None) -> CheckResult:
    if tokenizer_path is None:
        return result("tokenizer_artifact", SKIP, "No tokenizer path provided.")
    if not tokenizer_path.exists():
        return result("tokenizer_artifact", FAIL, f"Tokenizer not found: {tokenizer_path}")
    from tokenizers import Tokenizer

    tokenizer = await asyncio.to_thread(Tokenizer.from_file, str(tokenizer_path))
    encoded = tokenizer.encode("def add(a, b):\n    return a + b\n0xff <|compile_error|>")
    ok = len(encoded.ids) > 0
    return result(
        "tokenizer_artifact",
        OK if ok else FAIL,
        f"Tokenizer loaded and encoded {len(encoded.ids)} tokens.",
        {"path": str(tokenizer_path), "vocab_size": tokenizer.get_vocab_size()},
    )


async def check_phase4_swarm() -> CheckResult:
    from src.phase4.swarm_orchestrator import run_mock_workflow

    report = await run_mock_workflow("Phase 10 secure architecture smoke", max_concurrency=3)
    accepted = len(report.accepted_artifacts)
    rejected = len(report.rejected_artifacts)
    ok = accepted >= 1 and rejected >= 1
    return result(
        "phase4_swarm_mock",
        OK if ok else FAIL,
        f"Swarm mock accepted={accepted}, rejected={rejected}.",
        {"trace_id": report.trace_id, "accepted": accepted, "rejected": rejected},
    )


async def check_phase9_custom_suite() -> CheckResult:
    from src.phase9.benchmarks import CustomSecurityRunner
    from src.phase9.models import Generation

    class FixedClient:
        model = "phase10-fixed"

        async def generate(self, prompt: str, **_: Any) -> list[Generation]:
            del prompt
            text = (
                "buffer overflow strcpy sql injection parameter query md5 argon2 "
                "resolve base shell=False list json schema defusedxml allowlist relative "
                "path traversal command injection deserialization xxe open redirect"
            )
            return [Generation(text=text, model=self.model, latency_ms=0.0)]

    report = await CustomSecurityRunner(FixedClient(), "phase10-smoke", concurrency=16).run()
    ok = len(report.sample_results) == 200 and {"overall", "buffer_overflow_detection"}.issubset(report.metrics)
    return result(
        "phase9_custom_security_mock",
        OK if ok else FAIL,
        f"Custom security suite produced {len(report.sample_results)} sample results.",
        {"metrics": report.metrics},
    )


async def check_http_health(name: str, url: str | None, path: str) -> CheckResult:
    if not url:
        return result(name, SKIP, "No URL configured.")
    import httpx

    target = f"{url.rstrip('/')}{path}"
    async with httpx.AsyncClient(timeout=httpx.Timeout(5.0, connect=2.0)) as client:
        response = await client.get(target)
    ok = 200 <= response.status_code < 400
    status = OK if ok else FAIL
    return result(name, status, f"{target} returned HTTP {response.status_code}.", {"url": target, "ok": ok})


async def check_docker() -> CheckResult:
    if importlib.util.find_spec("docker") is None:
        return result("docker_daemon", FAIL, "docker Python package is missing.")
    import docker

    client = docker.from_env()
    try:
        await asyncio.to_thread(client.ping)
    finally:
        await asyncio.to_thread(client.close)
    return result("docker_daemon", OK, "Docker daemon responded to ping.")


async def check_redis(redis_url: str | None) -> CheckResult:
    if not redis_url:
        return result("redis", SKIP, "No Redis URL configured.")
    if importlib.util.find_spec("redis") is None:
        return result("redis", FAIL, "redis Python package is missing.")
    import redis.asyncio as redis

    last_error: Exception | None = None
    for attempt in range(1, 4):
        client = redis.from_url(
            redis_url,
            socket_connect_timeout=5,
            socket_timeout=5,
            retry_on_timeout=True,
        )
        try:
            pong = await client.ping()
            if pong:
                return result("redis", OK, "Redis ping succeeded.", {"attempt": attempt})
        except Exception as exc:
            last_error = exc
            await asyncio.sleep(0.35 * attempt)
        finally:
            await client.aclose()
    if last_error:
        return result("redis", FAIL, f"{type(last_error).__name__}: {last_error}")
    return result("redis", FAIL, "Redis ping failed.")


async def check_postgres(postgres_dsn: str | None) -> CheckResult:
    if not postgres_dsn:
        return result("postgres", SKIP, "No PostgreSQL DSN configured.")
    if importlib.util.find_spec("asyncpg") is None:
        return result("postgres", FAIL, "asyncpg package is missing.")
    import asyncpg

    conn = await asyncpg.connect(postgres_dsn)
    try:
        value = await conn.fetchval("SELECT 1")
    finally:
        await conn.close()
    return result("postgres", OK if value == 1 else FAIL, "PostgreSQL SELECT 1 succeeded.")


async def check_qdrant(qdrant_url: str | None) -> CheckResult:
    return await check_http_health("qdrant", qdrant_url, "/collections")


async def check_sandbox_smoke(image: str, enabled: bool) -> CheckResult:
    if not enabled:
        return result("sandbox_execution", SKIP, "Sandbox smoke disabled. Pass --run-sandbox-smoke to execute.")
    from src.phase2.docker_sandbox_engine import ExecutionRequest, SandboxEngine, SandboxFile

    request = ExecutionRequest(
        files=[SandboxFile(path="main.py", content="print('phase10-sandbox-ok')\n")],
        compile_command=None,
        run_command="python3 /workspace/main.py",
        image=image,
    )
    async with SandboxEngine(pool_size=1) as engine:
        execution = await engine.run(request)
    ok = execution.ok and "phase10-sandbox-ok" in execution.stdout
    return result(
        "sandbox_execution",
        OK if ok else FAIL,
        "Sandbox execution succeeded." if ok else f"Sandbox failed: {execution.error or execution.stderr}",
        {"exit_code": execution.exit_code, "timeout": execution.timeout, "stdout": execution.stdout[-200:]},
    )


async def build_report(args: argparse.Namespace) -> SmokeReport:
    started = time.time()
    checks: list[tuple[str, Callable[[], Awaitable[CheckResult]]]] = [
        ("python_runtime", check_python),
        ("phase_files", check_required_files),
        ("compileall", check_compileall),
        ("python_packages", check_python_packages),
        ("tokenizer_artifact", lambda: check_tokenizer(args.tokenizer)),
        ("phase4_swarm_mock", check_phase4_swarm),
        ("phase9_custom_security_mock", check_phase9_custom_suite),
    ]
    if args.offline:
        checks.extend(
            [
                ("docker_daemon", lambda: asyncio.sleep(0, result("docker_daemon", SKIP, "Offline mode enabled."))),
                ("redis", lambda: asyncio.sleep(0, result("redis", SKIP, "Offline mode enabled."))),
                ("postgres", lambda: asyncio.sleep(0, result("postgres", SKIP, "Offline mode enabled."))),
                ("qdrant", lambda: asyncio.sleep(0, result("qdrant", SKIP, "Offline mode enabled."))),
                ("vllm_health", lambda: asyncio.sleep(0, result("vllm_health", SKIP, "Offline mode enabled."))),
                ("gateway_health", lambda: asyncio.sleep(0, result("gateway_health", SKIP, "Offline mode enabled."))),
                ("sandbox_execution", lambda: asyncio.sleep(0, result("sandbox_execution", SKIP, "Offline mode enabled."))),
            ]
        )
    else:
        checks.extend(
            [
                ("docker_daemon", check_docker),
                ("redis", lambda: check_redis(args.redis_url)),
                ("postgres", lambda: check_postgres(args.postgres_dsn)),
                ("qdrant", lambda: check_qdrant(args.qdrant_url)),
                ("vllm_health", lambda: check_http_health("vllm_health", args.vllm_url, "/health")),
                ("gateway_health", lambda: check_http_health("gateway_health", args.gateway_url, "/health/live")),
                ("sandbox_execution", lambda: check_sandbox_smoke(args.sandbox_image, args.run_sandbox_smoke)),
            ]
        )

    results = await asyncio.gather(*(timed_check(name, fn) for name, fn in checks))
    return SmokeReport(
        run_id=args.run_id,
        started_at_unix=started,
        duration_ms=(time.time() - started) * 1000,
        results=list(results),
    )


def write_reports(report: SmokeReport, output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / f"{report.run_id}.json"
    md_path = output_dir / f"{report.run_id}.md"
    json_path.write_text(json.dumps(asdict(report), indent=2, ensure_ascii=False), encoding="utf-8")

    lines = [
        "# Phase 10 Smoke Report",
        "",
        f"- Run ID: `{report.run_id}`",
        f"- Passed: `{report.passed}`",
        f"- Duration: `{report.duration_ms:.1f} ms`",
        f"- Summary: `{report.summary()}`",
        "",
        "| Check | Status | Message | Duration ms |",
        "| --- | --- | --- | ---: |",
    ]
    for item in report.results:
        lines.append(f"| {item.name} | {item.status} | {item.message.replace('|', '/')} | {item.duration_ms:.1f} |")
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, md_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Phase 10 deployment doctor and E2E smoke runner.")
    parser.add_argument("--run-id", default=f"phase10-{int(time.time())}")
    parser.add_argument("--output-dir", type=Path, default=Path("artifacts/phase10"))
    parser.add_argument("--tokenizer", type=Path, default=Path("artifacts/debug_tokenizer/tokenizer.json"))
    parser.add_argument("--offline", action="store_true", help="Skip external services and Docker checks.")
    parser.add_argument("--strict", action="store_true", help="Exit non-zero if any check fails.")
    parser.add_argument("--run-sandbox-smoke", action="store_true")
    parser.add_argument("--sandbox-image", default="python:3.12-slim")
    parser.add_argument("--gateway-url", default="http://localhost:18080")
    parser.add_argument("--vllm-url", default="http://localhost:8000")
    parser.add_argument("--redis-url", default="redis://127.0.0.1:6379/0")
    parser.add_argument("--postgres-dsn")
    parser.add_argument("--qdrant-url", default="http://localhost:6333")
    return parser.parse_args()


async def async_main() -> int:
    args = parse_args()
    report = await build_report(args)
    json_path, md_path = write_reports(report, args.output_dir)
    print(json.dumps({"run_id": report.run_id, "passed": report.passed, "summary": report.summary(), "json": str(json_path), "markdown": str(md_path)}, indent=2))
    return 1 if args.strict and not report.passed else 0


def main() -> None:
    raise SystemExit(asyncio.run(async_main()))


if __name__ == "__main__":
    main()
