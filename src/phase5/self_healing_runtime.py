#!/usr/bin/env python
"""Production-grade self-healing runtime and async QLoRA staging pipeline.

Phase 5 integrates:
- Swarm/agent reasoning context
- hardened sandbox execution
- crash telemetry capture
- recursive repair loops capped at exactly 5 iterations
- successful lifecycle trace conversion into token-ready training sequences
- asynchronous PostgreSQL/Qdrant staging
- cron-style buffer monitor that queues offline QLoRA batch jobs

The module is designed to run with real Docker/PostgreSQL/Qdrant dependencies,
while still keeping interfaces explicit enough for test doubles.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Protocol


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.phase2.docker_sandbox_engine import (  # noqa: E402
    DEFAULT_IMAGE,
    ExecutionRequest,
    ExecutionResult,
    SandboxEngine,
    SandboxFile,
)


MAX_RECURSION_DEPTH = 5
DEFAULT_STAGING_COLLECTION = "self_healing_qlora_staging"


def validate_http_endpoint(endpoint: str) -> str:
    parsed = urllib.parse.urlparse(endpoint.rstrip("/"))
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("repair model endpoint must be an absolute http:// or https:// URL")
    return endpoint.rstrip("/")

POSTGRES_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS healing_lifecycle_traces (
    trace_id TEXT PRIMARY KEY,
    created_at_unix_ms BIGINT NOT NULL,
    initial_prompt TEXT NOT NULL,
    failed_reasoning_path JSONB NOT NULL,
    caught_telemetry_logs JSONB NOT NULL,
    corrected_reasoning_trace JSONB NOT NULL,
    success_verification_patch JSONB NOT NULL,
    training_sequence TEXT NOT NULL,
    qdrant_point_id TEXT,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    immutable_hash TEXT NOT NULL,
    promoted BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE TABLE IF NOT EXISTS healing_repair_iterations (
    iteration_id TEXT PRIMARY KEY,
    trace_id TEXT,
    created_at_unix_ms BIGINT NOT NULL,
    depth INTEGER NOT NULL,
    accepted BOOLEAN NOT NULL,
    reasoning JSONB NOT NULL,
    patch JSONB NOT NULL,
    telemetry JSONB NOT NULL,
    sandbox_result JSONB NOT NULL,
    FOREIGN KEY (trace_id) REFERENCES healing_lifecycle_traces(trace_id)
);

CREATE TABLE IF NOT EXISTS qlora_training_jobs (
    job_id TEXT PRIMARY KEY,
    created_at_unix_ms BIGINT NOT NULL,
    status TEXT NOT NULL,
    trace_count INTEGER NOT NULL,
    trace_ids JSONB NOT NULL,
    dataset_path TEXT,
    base_model TEXT,
    adapter_output_path TEXT,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_healing_lifecycle_promoted
    ON healing_lifecycle_traces (promoted, created_at_unix_ms);

CREATE INDEX IF NOT EXISTS idx_healing_iterations_trace
    ON healing_repair_iterations (trace_id, depth);

CREATE INDEX IF NOT EXISTS idx_qlora_training_jobs_status
    ON qlora_training_jobs (status, created_at_unix_ms);
"""

REPAIR_SYSTEM_PROMPT = """You are a production self-healing repair agent.
Return only valid JSON. Do not use markdown fences.
You will receive the initial prompt, failed reasoning path, source tree, and
an explicit runtime exception trace captured from a hardened sandbox.
Re-analyze the prior logic and output exactly one corrected patch attempt.

Schema:
{
  "corrected_reasoning_trace": [
    {"step": "diagnosis", "detail": "specific failed assumption"},
    {"step": "repair", "detail": "specific correction"}
  ],
  "patch": {
    "files": [
      {"path": "relative/file.ext", "content": "complete replacement file content"}
    ]
  }
}

Rules:
- Do not invent hidden tests or target CVE identifiers.
- Use the telemetry logs as runtime exception evidence.
- Keep patches minimal and API-compatible.
- Patched files must be complete replacement content.
"""


@dataclass(frozen=True)
class CrashTelemetry:
    crash_id: str
    exit_code: int | None
    timeout: bool
    flag: str | None
    command: str
    stdout: str
    stderr: str
    crash_class: str
    compiler_diagnostics: list[str]
    python_tracebacks: list[str]
    gdb_core_dumps: list[str]
    register_frames: list[str]
    sanitizer_findings: list[str]
    memory_metrics: dict[str, Any]
    cpu_metrics: dict[str, Any]
    raw_sandbox_result: dict[str, Any]


@dataclass(frozen=True)
class RepairPatch:
    corrected_reasoning_trace: list[dict[str, str]]
    files: list[SandboxFile]
    raw_model_output: dict[str, Any]


@dataclass(frozen=True)
class RepairIteration:
    iteration_id: str
    trace_id: str | None
    created_at_unix_ms: int
    depth: int
    accepted: bool
    reasoning: list[dict[str, str]]
    patch: list[dict[str, Any]]
    telemetry: dict[str, Any]
    sandbox_result: dict[str, Any]


@dataclass(frozen=True)
class HealingLifecycleTrace:
    trace_id: str
    created_at_unix_ms: int
    initial_prompt: str
    failed_reasoning_path: list[dict[str, Any]]
    caught_telemetry_logs: list[dict[str, Any]]
    corrected_reasoning_trace: list[dict[str, str]]
    success_verification_patch: list[dict[str, Any]]
    training_sequence: str
    qdrant_point_id: str | None
    metadata: dict[str, Any]
    immutable_hash: str


@dataclass(frozen=True)
class SelfHealingRequest:
    initial_prompt: str
    failed_reasoning_path: list[dict[str, Any]]
    source_files: list[SandboxFile]
    execution: ExecutionRequest
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class QLoRAJob:
    job_id: str
    created_at_unix_ms: int
    status: str
    trace_count: int
    trace_ids: list[str]
    dataset_path: str | None
    base_model: str | None
    adapter_output_path: str | None
    metadata: dict[str, Any]


class RepairModel(Protocol):
    async def propose_repair(self, context: dict[str, Any]) -> dict[str, Any]:
        """Return JSON repair candidate."""


def stable_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def stable_id(*parts: object) -> str:
    return sha256_text("\x1f".join(str(part) for part in parts))[:24]


def now_ms() -> int:
    return int(time.time() * 1000)


def extract_json_object(raw: str) -> dict[str, Any]:
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("repair model output did not contain JSON")
        return json.loads(text[start : end + 1])


def source_file_from_payload(payload: dict[str, Any]) -> SandboxFile:
    return SandboxFile(
        path=str(payload["path"]),
        content=str(payload["content"]),
        encoding=str(payload.get("encoding", "utf-8")),
        executable=bool(payload.get("executable", False)),
    )


def source_files_payload(files: list[SandboxFile]) -> list[dict[str, Any]]:
    return [asdict(item) for item in files]


class CrashTelemetryExtractor:
    GCC_RE = re.compile(r"^(.*?:\d+:\d+:\s+(?:warning|error|fatal error):.*)$", re.MULTILINE)
    PY_TRACE_RE = re.compile(r"(Traceback \(most recent call last\):.*?)(?=\n\S|\Z)", re.DOTALL)
    GDB_RE = re.compile(r"((?:Core was generated by|Program terminated with signal|#\d+\s+0x[0-9a-fA-F]+).*?)(?=\n\n|\Z)", re.DOTALL)
    REGISTER_RE = re.compile(
        r"\b(?:rip|eip|rsp|esp|rbp|ebp|rax|rbx|rcx|rdx|rsi|rdi|pc|sp|lr)\s+0x[0-9a-fA-F]+",
        re.IGNORECASE,
    )
    SANITIZER_RE = re.compile(
        r"(AddressSanitizer|UndefinedBehaviorSanitizer|MemorySanitizer|heap-use-after-free|stack-overflow|buffer-overflow).*",
        re.IGNORECASE,
    )

    def extract(self, result: ExecutionResult) -> CrashTelemetry:
        payload = asdict(result)
        combined = "\n".join([result.stdout or "", result.stderr or "", result.error or ""])
        compiler = self.GCC_RE.findall(combined)
        tracebacks = [match.strip() for match in self.PY_TRACE_RE.findall(combined)]
        gdb = [match.strip() for match in self.GDB_RE.findall(combined)]
        registers = self.REGISTER_RE.findall(combined)
        sanitizer = self.SANITIZER_RE.findall(combined)
        crash_class = self.classify(result, combined, compiler, tracebacks, gdb, sanitizer)
        metrics = payload.get("metrics") or {}
        return CrashTelemetry(
            crash_id=stable_id(result.command, result.exit_code, result.stderr, result.stdout),
            exit_code=result.exit_code,
            timeout=result.timeout,
            flag=result.flag,
            command=result.command,
            stdout=result.stdout,
            stderr=result.stderr,
            crash_class=crash_class,
            compiler_diagnostics=compiler,
            python_tracebacks=tracebacks,
            gdb_core_dumps=gdb,
            register_frames=registers,
            sanitizer_findings=[str(item) for item in sanitizer],
            memory_metrics={
                "memory_current_bytes": metrics.get("memory_current_bytes"),
                "memory_peak_bytes": metrics.get("memory_peak_bytes"),
            },
            cpu_metrics={
                "wall_time_us": metrics.get("wall_time_us"),
                "cpu_total_usage_us": metrics.get("cpu_total_usage_us"),
                "cpu_kernel_usage_us": metrics.get("cpu_kernel_usage_us"),
                "cpu_user_usage_us": metrics.get("cpu_user_usage_us"),
            },
            raw_sandbox_result=payload,
        )

    @staticmethod
    def classify(
        result: ExecutionResult,
        combined: str,
        compiler: list[str],
        tracebacks: list[str],
        gdb: list[str],
        sanitizer: list[Any],
    ) -> str:
        lower = combined.lower()
        if result.timeout:
            return "timeout"
        if compiler:
            return "compiler_error"
        if tracebacks:
            return "python_traceback"
        if sanitizer:
            return "sanitizer_violation"
        if gdb or "segmentation fault" in lower or "core dumped" in lower:
            return "native_crash"
        if "stack overflow" in lower:
            return "stack_overflow"
        if result.exit_code not in {0, None}:
            return "nonzero_exit"
        return "unknown_failure"


class OpenAICompatibleRepairModel:
    def __init__(
        self,
        endpoint: str,
        model: str,
        api_key: str | None,
        temperature: float = 0.1,
        max_tokens: int = 8192,
        timeout_seconds: int = 180,
    ) -> None:
        self.endpoint = validate_http_endpoint(endpoint)
        self.model = model
        self.api_key = api_key
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout_seconds = timeout_seconds

    async def propose_repair(self, context: dict[str, Any]) -> dict[str, Any]:
        raw = await asyncio.to_thread(self._complete, context)
        return extract_json_object(raw)

    def _complete(self, context: dict[str, Any]) -> str:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": REPAIR_SYSTEM_PROMPT},
                {"role": "user", "content": stable_json(context)},
            ],
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "response_format": {"type": "json_object"},
        }
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        request = urllib.request.Request(
            f"{self.endpoint}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:  # nosec B310
                body = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            details = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"repair model HTTP {exc.code}: {details}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"repair model endpoint error: {exc}") from exc
        return body["choices"][0]["message"]["content"]


class MockRepairModel:
    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self.responses = responses
        self.index = 0

    async def propose_repair(self, context: dict[str, Any]) -> dict[str, Any]:
        del context
        await asyncio.sleep(0)
        if self.index >= len(self.responses):
            raise RuntimeError("mock repair responses exhausted")
        response = self.responses[self.index]
        self.index += 1
        return response


def parse_repair_patch(payload: dict[str, Any]) -> RepairPatch:
    reasoning = payload.get("corrected_reasoning_trace")
    if not isinstance(reasoning, list) or not reasoning:
        raise ValueError("repair payload missing corrected_reasoning_trace")
    normalized_reasoning: list[dict[str, str]] = []
    for item in reasoning:
        if isinstance(item, dict):
            normalized_reasoning.append(
                {
                    "step": str(item.get("step", "reasoning")),
                    "detail": str(item.get("detail", item)),
                }
            )
        else:
            normalized_reasoning.append({"step": "reasoning", "detail": str(item)})
    patch = payload.get("patch")
    if not isinstance(patch, dict) or not isinstance(patch.get("files"), list):
        raise ValueError("repair payload missing patch.files")
    files = [source_file_from_payload(item) for item in patch["files"]]
    if not files:
        raise ValueError("patch.files cannot be empty")
    return RepairPatch(
        corrected_reasoning_trace=normalized_reasoning,
        files=files,
        raw_model_output=payload,
    )


def merge_patch(source_files: list[SandboxFile], patch_files: list[SandboxFile]) -> list[SandboxFile]:
    merged = {source.path: source for source in source_files}
    for patch in patch_files:
        if patch.path not in merged:
            raise ValueError(f"patch attempted to create unknown file: {patch.path}")
        merged[patch.path] = patch
    return list(merged.values())


class TrainingTraceFormatter:
    def format(self, trace: HealingLifecycleTrace) -> str:
        return (
            "<|self_heal_start|>"
            f"<|initial_prompt|>{trace.initial_prompt}"
            f"<|failed_reasoning_path|>{stable_json(trace.failed_reasoning_path)}"
            f"<|caught_telemetry_logs|>{stable_json(trace.caught_telemetry_logs)}"
            f"<|corrected_reasoning_trace|>{stable_json(trace.corrected_reasoning_trace)}"
            f"<|success_verification_patch|>{stable_json(trace.success_verification_patch)}"
            "<|self_heal_end|>"
        )


def build_trace(
    request: SelfHealingRequest,
    telemetry_sequence: list[CrashTelemetry],
    accepted_patch: RepairPatch,
    successful_result: ExecutionResult,
) -> HealingLifecycleTrace:
    created_at = now_ms()
    trace_id = stable_id(request.initial_prompt, created_at, telemetry_sequence[0].crash_id)
    trace_without_hash = {
        "trace_id": trace_id,
        "created_at_unix_ms": created_at,
        "initial_prompt": request.initial_prompt,
        "failed_reasoning_path": request.failed_reasoning_path,
        "caught_telemetry_logs": [asdict(item) for item in telemetry_sequence],
        "corrected_reasoning_trace": accepted_patch.corrected_reasoning_trace,
        "success_verification_patch": source_files_payload(accepted_patch.files),
        "training_sequence": "",
        "qdrant_point_id": None,
        "metadata": {
            **request.metadata,
            "successful_sandbox_result": asdict(successful_result),
        },
    }
    immutable_hash = sha256_text(stable_json(trace_without_hash))
    trace = HealingLifecycleTrace(**trace_without_hash, immutable_hash=immutable_hash)
    sequence = TrainingTraceFormatter().format(trace)
    return HealingLifecycleTrace(**{**asdict(trace), "training_sequence": sequence})


class SelfHealingLoop:
    def __init__(
        self,
        repair_model: RepairModel,
        sandbox: SandboxEngine,
        telemetry_extractor: CrashTelemetryExtractor | None = None,
        max_depth: int = MAX_RECURSION_DEPTH,
    ) -> None:
        if max_depth != MAX_RECURSION_DEPTH:
            raise ValueError("recursive correction depth must be exactly 5")
        self.repair_model = repair_model
        self.sandbox = sandbox
        self.telemetry_extractor = telemetry_extractor or CrashTelemetryExtractor()
        self.max_depth = max_depth

    async def run(self, request: SelfHealingRequest) -> tuple[HealingLifecycleTrace | None, list[RepairIteration]]:
        source_state = list(request.source_files)
        execution = request.execution
        initial_result = await self.sandbox.run(execution)
        if initial_result.ok and initial_result.exit_code == 0:
            return None, []

        telemetry_sequence = [self.telemetry_extractor.extract(initial_result)]
        iterations: list[RepairIteration] = []

        for depth in range(1, self.max_depth + 1):
            context = self._repair_context(
                request=request,
                depth=depth,
                source_state=source_state,
                telemetry_sequence=telemetry_sequence,
                iterations=iterations,
            )
            repair_payload = await self.repair_model.propose_repair(context)
            patch = parse_repair_patch(repair_payload)
            patched_source = merge_patch(source_state, patch.files)
            patched_execution = ExecutionRequest(
                files=patched_source,
                compile_command=execution.compile_command,
                run_command=execution.run_command,
                image=execution.image,
                bash_args=execution.bash_args,
                env=execution.env,
                request_id=execution.request_id,
                pull_missing_image=execution.pull_missing_image,
            )
            result = await self.sandbox.run(patched_execution)
            accepted = result.ok and result.exit_code == 0 and not result.timeout
            telemetry = self.telemetry_extractor.extract(result)
            iteration = RepairIteration(
                iteration_id=stable_id(request.initial_prompt, depth, stable_json(repair_payload)),
                trace_id=None,
                created_at_unix_ms=now_ms(),
                depth=depth,
                accepted=accepted,
                reasoning=patch.corrected_reasoning_trace,
                patch=source_files_payload(patch.files),
                telemetry=asdict(telemetry),
                sandbox_result=asdict(result),
            )
            iterations.append(iteration)
            if accepted:
                trace = build_trace(request, telemetry_sequence, patch, result)
                iterations = [
                    RepairIteration(**{**asdict(item), "trace_id": trace.trace_id})
                    for item in iterations
                ]
                return trace, iterations
            telemetry_sequence.append(telemetry)
            source_state = patched_source

        return None, iterations

    @staticmethod
    def _repair_context(
        request: SelfHealingRequest,
        depth: int,
        source_state: list[SandboxFile],
        telemetry_sequence: list[CrashTelemetry],
        iterations: list[RepairIteration],
    ) -> dict[str, Any]:
        return {
            "initial_prompt": request.initial_prompt,
            "failed_reasoning_path": request.failed_reasoning_path,
            "recursion_depth": depth,
            "max_recursion_depth": MAX_RECURSION_DEPTH,
            "current_source_tree": source_files_payload(source_state),
            "runtime_exception_trace": [asdict(item) for item in telemetry_sequence],
            "previous_repair_iterations": [asdict(item) for item in iterations],
            "repair_contract": {
                "corrected_reasoning_trace": [{"step": "string", "detail": "string"}],
                "patch": {"files": [{"path": "string", "content": "complete replacement"}]},
            },
        }


class HashEmbedding:
    def __init__(self, dimensions: int = 384) -> None:
        self.dimensions = dimensions

    def embed(self, text: str) -> list[float]:
        vector = [0.0] * self.dimensions
        for token in re.findall(r"[A-Za-z_][A-Za-z0-9_]*|0x[0-9a-fA-F]+|\S", text.lower()):
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            index = int.from_bytes(digest[:4], "big") % self.dimensions
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vector[index] += sign
        norm = math.sqrt(sum(value * value for value in vector)) or 1.0
        return [value / norm for value in vector]


class AsyncStagingBuffer:
    def __init__(
        self,
        postgres_dsn: str | None,
        qdrant_url: str | None,
        qdrant_api_key: str | None,
        collection: str = DEFAULT_STAGING_COLLECTION,
        embedding_dimensions: int = 384,
        jsonl_fallback: Path | None = None,
    ) -> None:
        self.postgres_dsn = postgres_dsn
        self.qdrant_url = qdrant_url
        self.qdrant_api_key = qdrant_api_key
        self.collection = collection
        self.embedding_dimensions = embedding_dimensions
        self.embedding = HashEmbedding(embedding_dimensions)
        self.jsonl_fallback = jsonl_fallback
        self.queue: asyncio.Queue[tuple[HealingLifecycleTrace, list[RepairIteration]]] = asyncio.Queue()
        self.worker_task: asyncio.Task[None] | None = None
        self._closed = False

    async def initialize(self) -> None:
        if self.postgres_dsn:
            await self._init_postgres()
        if self.qdrant_url:
            await self._init_qdrant()

    async def start_worker(self) -> None:
        if self.worker_task is None:
            self.worker_task = asyncio.create_task(self._worker(), name="qlora-staging-worker")

    async def close(self) -> None:
        self._closed = True
        await self.queue.join()
        if self.worker_task:
            self.worker_task.cancel()
            await asyncio.gather(self.worker_task, return_exceptions=True)

    async def enqueue(self, trace: HealingLifecycleTrace, iterations: list[RepairIteration]) -> None:
        await self.start_worker()
        await self.queue.put((trace, iterations))

    async def _worker(self) -> None:
        while True:
            trace, iterations = await self.queue.get()
            try:
                await self._persist(trace, iterations)
            finally:
                self.queue.task_done()

    async def _persist(self, trace: HealingLifecycleTrace, iterations: list[RepairIteration]) -> None:
        point_id = await self._upsert_qdrant(trace)
        trace_payload = asdict(trace)
        if point_id:
            trace_payload["qdrant_point_id"] = point_id
        if self.postgres_dsn:
            await self._insert_postgres(trace_payload, iterations)
        if self.jsonl_fallback:
            await self._append_jsonl(trace_payload, iterations)

    async def _init_postgres(self) -> None:
        try:
            import asyncpg  # type: ignore
        except ImportError as exc:
            raise RuntimeError("Install asyncpg for PostgreSQL staging.") from exc
        conn = await asyncpg.connect(self.postgres_dsn)
        try:
            await conn.execute(POSTGRES_SCHEMA_SQL)
        finally:
            await conn.close()

    async def _insert_postgres(self, trace_payload: dict[str, Any], iterations: list[RepairIteration]) -> None:
        try:
            import asyncpg  # type: ignore
        except ImportError as exc:
            raise RuntimeError("Install asyncpg for PostgreSQL staging.") from exc
        conn = await asyncpg.connect(self.postgres_dsn)
        try:
            async with conn.transaction():
                await conn.execute(
                    """
                    INSERT INTO healing_lifecycle_traces (
                        trace_id, created_at_unix_ms, initial_prompt, failed_reasoning_path,
                        caught_telemetry_logs, corrected_reasoning_trace, success_verification_patch,
                        training_sequence, qdrant_point_id, metadata, immutable_hash
                    )
                    VALUES ($1,$2,$3,$4::jsonb,$5::jsonb,$6::jsonb,$7::jsonb,$8,$9,$10::jsonb,$11)
                    ON CONFLICT (trace_id) DO NOTHING
                    """,
                    trace_payload["trace_id"],
                    trace_payload["created_at_unix_ms"],
                    trace_payload["initial_prompt"],
                    json.dumps(trace_payload["failed_reasoning_path"]),
                    json.dumps(trace_payload["caught_telemetry_logs"]),
                    json.dumps(trace_payload["corrected_reasoning_trace"]),
                    json.dumps(trace_payload["success_verification_patch"]),
                    trace_payload["training_sequence"],
                    trace_payload.get("qdrant_point_id"),
                    json.dumps(trace_payload["metadata"]),
                    trace_payload["immutable_hash"],
                )
                for iteration in iterations:
                    payload = asdict(iteration)
                    await conn.execute(
                        """
                        INSERT INTO healing_repair_iterations (
                            iteration_id, trace_id, created_at_unix_ms, depth, accepted,
                            reasoning, patch, telemetry, sandbox_result
                        )
                        VALUES ($1,$2,$3,$4,$5,$6::jsonb,$7::jsonb,$8::jsonb,$9::jsonb)
                        ON CONFLICT (iteration_id) DO NOTHING
                        """,
                        payload["iteration_id"],
                        payload["trace_id"],
                        payload["created_at_unix_ms"],
                        payload["depth"],
                        payload["accepted"],
                        json.dumps(payload["reasoning"]),
                        json.dumps(payload["patch"]),
                        json.dumps(payload["telemetry"]),
                        json.dumps(payload["sandbox_result"]),
                    )
        finally:
            await conn.close()

    async def _init_qdrant(self) -> None:
        try:
            from qdrant_client import AsyncQdrantClient  # type: ignore
            from qdrant_client.models import Distance, VectorParams  # type: ignore
        except ImportError as exc:
            raise RuntimeError("Install qdrant-client for vector staging.") from exc
        client = AsyncQdrantClient(url=self.qdrant_url, api_key=self.qdrant_api_key)
        try:
            collections = await client.get_collections()
            exists = any(item.name == self.collection for item in collections.collections)
            if not exists:
                await client.create_collection(
                    collection_name=self.collection,
                    vectors_config=VectorParams(size=self.embedding_dimensions, distance=Distance.COSINE),
                )
        finally:
            await client.close()

    async def _upsert_qdrant(self, trace: HealingLifecycleTrace) -> str | None:
        if not self.qdrant_url:
            return None
        try:
            from qdrant_client import AsyncQdrantClient  # type: ignore
            from qdrant_client.models import PointStruct  # type: ignore
        except ImportError as exc:
            raise RuntimeError("Install qdrant-client for vector staging.") from exc
        point_id = trace.trace_id
        client = AsyncQdrantClient(url=self.qdrant_url, api_key=self.qdrant_api_key)
        try:
            await client.upsert(
                collection_name=self.collection,
                points=[
                    PointStruct(
                        id=point_id,
                        vector=self.embedding.embed(trace.training_sequence),
                        payload={
                            "trace_id": trace.trace_id,
                            "created_at_unix_ms": trace.created_at_unix_ms,
                            "immutable_hash": trace.immutable_hash,
                            "metadata": trace.metadata,
                        },
                    )
                ],
            )
        finally:
            await client.close()
        return point_id

    async def _append_jsonl(self, trace_payload: dict[str, Any], iterations: list[RepairIteration]) -> None:
        self.jsonl_fallback.parent.mkdir(parents=True, exist_ok=True)
        payload = {"trace": trace_payload, "iterations": [asdict(item) for item in iterations]}
        await asyncio.to_thread(self._append_jsonl_sync, payload)

    def _append_jsonl_sync(self, payload: dict[str, Any]) -> None:
        with self.jsonl_fallback.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, sort_keys=True, ensure_ascii=False) + "\n")


class QLoRACronWorker:
    def __init__(
        self,
        postgres_dsn: str,
        threshold_block_size: int,
        poll_interval_seconds: int,
        dataset_dir: Path,
        base_model: str,
        adapter_output_dir: Path,
    ) -> None:
        self.postgres_dsn = postgres_dsn
        self.threshold_block_size = threshold_block_size
        self.poll_interval_seconds = poll_interval_seconds
        self.dataset_dir = dataset_dir
        self.base_model = base_model
        self.adapter_output_dir = adapter_output_dir

    async def run_forever(self) -> None:
        while True:
            job = await self.check_and_queue_once()
            if job:
                print(json.dumps({"queued_job": asdict(job)}, indent=2))
            await asyncio.sleep(self.poll_interval_seconds)

    async def check_and_queue_once(self) -> QLoRAJob | None:
        try:
            import asyncpg  # type: ignore
        except ImportError as exc:
            raise RuntimeError("Install asyncpg for cron worker.") from exc
        conn = await asyncpg.connect(self.postgres_dsn)
        try:
            rows = await conn.fetch(
                """
                SELECT trace_id, training_sequence
                FROM healing_lifecycle_traces
                WHERE promoted = FALSE
                ORDER BY created_at_unix_ms ASC
                LIMIT $1
                """,
                self.threshold_block_size,
            )
            if len(rows) < self.threshold_block_size:
                return None
            trace_ids = [row["trace_id"] for row in rows]
            dataset_path = self.dataset_dir / f"qlora_batch_{now_ms()}.jsonl"
            await asyncio.to_thread(self._write_dataset, dataset_path, rows)
            job = QLoRAJob(
                job_id=stable_id("qlora_job", trace_ids, dataset_path),
                created_at_unix_ms=now_ms(),
                status="queued",
                trace_count=len(trace_ids),
                trace_ids=trace_ids,
                dataset_path=str(dataset_path),
                base_model=self.base_model,
                adapter_output_path=str(self.adapter_output_dir / stable_id("adapter", trace_ids)),
                metadata={"source": "self_healing_staging"},
            )
            async with conn.transaction():
                await conn.execute(
                    """
                    INSERT INTO qlora_training_jobs (
                        job_id, created_at_unix_ms, status, trace_count, trace_ids,
                        dataset_path, base_model, adapter_output_path, metadata
                    )
                    VALUES ($1,$2,$3,$4,$5::jsonb,$6,$7,$8,$9::jsonb)
                    ON CONFLICT (job_id) DO NOTHING
                    """,
                    job.job_id,
                    job.created_at_unix_ms,
                    job.status,
                    job.trace_count,
                    json.dumps(job.trace_ids),
                    job.dataset_path,
                    job.base_model,
                    job.adapter_output_path,
                    json.dumps(job.metadata),
                )
                await conn.execute(
                    "UPDATE healing_lifecycle_traces SET promoted = TRUE WHERE trace_id = ANY($1::text[])",
                    trace_ids,
                )
            return job
        finally:
            await conn.close()

    @staticmethod
    def _write_dataset(path: Path, rows: list[Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as handle:
            for row in rows:
                payload = {
                    "text": row["training_sequence"],
                    "metadata": {"trace_id": row["trace_id"]},
                }
                handle.write(json.dumps(payload, sort_keys=True, ensure_ascii=False) + "\n")


class IntegratedExecutionRunner:
    def __init__(
        self,
        repair_model: RepairModel,
        staging_buffer: AsyncStagingBuffer,
        sandbox: SandboxEngine,
    ) -> None:
        self.repair_model = repair_model
        self.staging_buffer = staging_buffer
        self.sandbox = sandbox

    async def execute_with_self_healing(self, request: SelfHealingRequest) -> HealingLifecycleTrace | None:
        loop = SelfHealingLoop(
            repair_model=self.repair_model,
            sandbox=self.sandbox,
            max_depth=MAX_RECURSION_DEPTH,
        )
        trace, iterations = await loop.run(request)
        if trace:
            await self.staging_buffer.enqueue(trace, iterations)
        return trace


def request_from_payload(payload: dict[str, Any]) -> SelfHealingRequest:
    source_files = [source_file_from_payload(item) for item in payload["source_files"]]
    execution_payload = payload["execution"]
    execution = ExecutionRequest(
        files=source_files,
        compile_command=execution_payload.get("compile_command"),
        run_command=str(execution_payload["run_command"]),
        image=str(execution_payload.get("image", payload.get("image", DEFAULT_IMAGE))),
        bash_args=list(execution_payload.get("bash_args", ["-lc"])),
        env=dict(execution_payload.get("env", {})),
        request_id=execution_payload.get("request_id"),
        pull_missing_image=bool(execution_payload.get("pull_missing_image", False)),
    )
    return SelfHealingRequest(
        initial_prompt=str(payload["initial_prompt"]),
        failed_reasoning_path=list(payload.get("failed_reasoning_path", [])),
        source_files=source_files,
        execution=execution,
        metadata=dict(payload.get("metadata", {})),
    )


def load_mock_responses(path: Path) -> list[dict[str, Any]]:
    responses: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            payload = json.loads(line)
            if "response" in payload and isinstance(payload["response"], str):
                responses.append(extract_json_object(payload["response"]))
            else:
                responses.append(payload)
    return responses


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Phase 5 self-healing runtime and QLoRA staging.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run = subparsers.add_parser("run", help="Run one self-healing execution.")
    run.add_argument("--request-json", required=True, type=Path)
    run.add_argument("--endpoint", default=os.environ.get("HEALING_MODEL_ENDPOINT", "http://localhost:8000/v1"))
    run.add_argument("--model", default=os.environ.get("HEALING_MODEL_NAME", "repair-model"))
    run.add_argument("--api-key-env", default="HEALING_MODEL_API_KEY")
    run.add_argument("--mock-response-file", type=Path)
    run.add_argument("--postgres-dsn", default=os.environ.get("HEALING_POSTGRES_DSN"))
    run.add_argument("--qdrant-url", default=os.environ.get("HEALING_QDRANT_URL"))
    run.add_argument("--qdrant-api-key-env", default="HEALING_QDRANT_API_KEY")
    run.add_argument("--jsonl-fallback", type=Path)
    run.add_argument("--init-db", action="store_true")

    cron = subparsers.add_parser("cron", help="Monitor staging DB and queue QLoRA jobs.")
    cron.add_argument("--postgres-dsn", default=os.environ.get("HEALING_POSTGRES_DSN"), required=False)
    cron.add_argument("--threshold-block-size", type=int, default=128)
    cron.add_argument("--poll-interval-seconds", type=int, default=300)
    cron.add_argument("--dataset-dir", type=Path, default=Path("artifacts/qlora_batches"))
    cron.add_argument("--base-model", default=os.environ.get("QLORA_BASE_MODEL", "baseline-reasoning-model"))
    cron.add_argument("--adapter-output-dir", type=Path, default=Path("artifacts/qlora_adapters"))
    cron.add_argument("--once", action="store_true")
    return parser.parse_args()


async def run_once(args: argparse.Namespace) -> None:
    request = request_from_payload(json.loads(args.request_json.read_text(encoding="utf-8")))
    if args.mock_response_file:
        repair_model: RepairModel = MockRepairModel(load_mock_responses(args.mock_response_file))
    else:
        repair_model = OpenAICompatibleRepairModel(
            endpoint=args.endpoint,
            model=args.model,
            api_key=os.environ.get(args.api_key_env) if args.api_key_env else None,
        )
    staging = AsyncStagingBuffer(
        postgres_dsn=args.postgres_dsn,
        qdrant_url=args.qdrant_url,
        qdrant_api_key=os.environ.get(args.qdrant_api_key_env) if args.qdrant_api_key_env else None,
        jsonl_fallback=args.jsonl_fallback,
    )
    if args.init_db:
        await staging.initialize()
    async with SandboxEngine() as sandbox:
        runner = IntegratedExecutionRunner(repair_model, staging, sandbox)
        trace = await runner.execute_with_self_healing(request)
        await staging.close()
    print(json.dumps({"healed": trace is not None, "trace": asdict(trace) if trace else None}, indent=2))


async def run_cron(args: argparse.Namespace) -> None:
    if not args.postgres_dsn:
        raise SystemExit("--postgres-dsn is required for cron mode")
    worker = QLoRACronWorker(
        postgres_dsn=args.postgres_dsn,
        threshold_block_size=args.threshold_block_size,
        poll_interval_seconds=args.poll_interval_seconds,
        dataset_dir=args.dataset_dir,
        base_model=args.base_model,
        adapter_output_dir=args.adapter_output_dir,
    )
    if args.once:
        job = await worker.check_and_queue_once()
        print(json.dumps({"queued": asdict(job) if job else None}, indent=2))
    else:
        await worker.run_forever()


async def async_main() -> None:
    args = parse_args()
    if args.command == "run":
        await run_once(args)
    else:
        await run_cron(args)


def main() -> None:
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
