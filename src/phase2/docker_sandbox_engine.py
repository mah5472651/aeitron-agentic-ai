#!/usr/bin/env python
"""Async hardened Docker sandbox engine for untrusted AI-generated code.

Security policy:
- absolute network isolation: network_mode="none", network_disabled=True
- cgroups: 1 CPU core maximum, exactly 512MB RAM and swap cap
- read-only container root filesystem
- read-only source workspace mounted at /workspace
- only writable path is /tmp tmpfs with 32MB and noexec/nosuid
- all Linux capabilities dropped
- no-new-privileges
- unprivileged UID/GID
- absolute execution timeout: 5,000 ms

The Docker Python SDK is synchronous, so this engine wraps all blocking Docker
operations with asyncio.to_thread and provides an async daemon/queue interface.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import queue
import shlex
import shutil
import tempfile
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterable

import docker
from docker.errors import APIError, DockerException, ImageNotFound, NotFound


DEFAULT_IMAGE = "ubuntu:24.04"
WORKDIR = "/workspace"
UNPRIVILEGED_USER = "65534:65534"
TIMEOUT_MS = 5_000
MEMORY_LIMIT = "512m"
NANO_CPUS = 1_000_000_000
TMPFS_SPEC = "rw,noexec,nosuid,size=32m"
MAX_SOURCE_BYTES = 4 * 1024 * 1024
MAX_FILE_COUNT = 256
STDOUT_LIMIT_BYTES = 2 * 1024 * 1024
STDERR_LIMIT_BYTES = 2 * 1024 * 1024

LANGUAGE_PRESETS = {
    "python": {
        "path": "main.py",
        "compile": None,
        "run": "python3 /workspace/main.py",
    },
    "bash": {
        "path": "main.sh",
        "compile": None,
        "run": "bash /workspace/main.sh",
        "executable": True,
    },
    "c": {
        "path": "main.c",
        "compile": "gcc -O3 /workspace/main.c -o /tmp/main_bin",
        "run": "/tmp/main_bin",  # nosec B108
    },
    "cpp": {
        "path": "main.cpp",
        "compile": "g++ -O3 /workspace/main.cpp -o /tmp/main_bin",
        "run": "/tmp/main_bin",  # nosec B108
    },
    "rust": {
        "path": "main.rs",
        "compile": "rustc -O /workspace/main.rs -o /tmp/main_bin",
        "run": "/tmp/main_bin",  # nosec B108
    },
}


class SandboxError(RuntimeError):
    """Base exception for sandbox lifecycle failures."""


class SandboxValidationError(SandboxError, ValueError):
    """Raised when a request is malformed or violates local policy."""


class SandboxLifecycleError(SandboxError):
    """Raised when Docker container creation/execution/cleanup fails."""


@dataclass(frozen=True)
class SandboxFile:
    path: str
    content: str
    encoding: str = "utf-8"
    executable: bool = False


@dataclass(frozen=True)
class SandboxPolicy:
    image: str = DEFAULT_IMAGE
    user: str = UNPRIVILEGED_USER
    timeout_ms: int = TIMEOUT_MS
    memory: str = MEMORY_LIMIT
    nano_cpus: int = NANO_CPUS
    pids_limit: int = 128
    tmpfs: dict[str, str] = field(default_factory=lambda: {"/tmp": TMPFS_SPEC})  # nosec B108
    read_only_root: bool = True
    network_mode: str = "none"
    stdout_limit_bytes: int = STDOUT_LIMIT_BYTES
    stderr_limit_bytes: int = STDERR_LIMIT_BYTES
    pull_missing_image: bool = False

    def validate(self) -> None:
        if self.timeout_ms != TIMEOUT_MS:
            raise SandboxValidationError("timeout_ms must be exactly 5000.")
        if self.memory.lower() not in {"512m", "512mb"}:
            raise SandboxValidationError("memory must be exactly 512MB.")
        if self.nano_cpus != NANO_CPUS:
            raise SandboxValidationError("nano_cpus must limit execution to exactly 1 core.")
        if self.network_mode != "none":
            raise SandboxValidationError('network_mode must be "none".')
        if self.tmpfs != {"/tmp": TMPFS_SPEC}:  # nosec B108
            raise SandboxValidationError(f"tmpfs must be exactly {{'/tmp': '{TMPFS_SPEC}'}}.")
        if not self.read_only_root:
            raise SandboxValidationError("read_only_root must be true.")


@dataclass(frozen=True)
class ExecutionRequest:
    files: list[SandboxFile]
    compile_command: str | None
    run_command: str
    image: str = DEFAULT_IMAGE
    bash_args: list[str] = field(default_factory=lambda: ["-lc"])
    env: dict[str, str] = field(default_factory=dict)
    request_id: str | None = None
    pull_missing_image: bool = False


@dataclass(frozen=True)
class SandboxLimits:
    """Compatibility shim for earlier Phase 2 callers.

    The hardened engine enforces 5,000 ms and 512MB regardless of these fields.
    They remain here so Phase 3/5 imports and request construction keep working.
    """

    timeout_seconds: int = 5
    memory: str = MEMORY_LIMIT
    nano_cpus: int = NANO_CPUS
    pids_limit: int = 128
    tmpfs_size: str = "32m"
    stdout_limit_bytes: int = STDOUT_LIMIT_BYTES
    stderr_limit_bytes: int = STDERR_LIMIT_BYTES


@dataclass(frozen=True)
class SandboxRequest:
    """Compatibility request for earlier pipeline phases."""

    files: list[SandboxFile]
    command: list[str]
    image: str = DEFAULT_IMAGE
    workdir: str = WORKDIR
    user: str = UNPRIVILEGED_USER
    env: dict[str, str] = field(default_factory=dict)
    limits: SandboxLimits = field(default_factory=SandboxLimits)
    pull_missing_image: bool = False


SourceFile = SandboxFile


@dataclass(frozen=True)
class ResourceMetrics:
    wall_time_us: int
    cpu_total_usage_us: int | None
    cpu_kernel_usage_us: int | None
    cpu_user_usage_us: int | None
    memory_current_bytes: int | None
    memory_peak_bytes: int | None
    stats_samples: int


@dataclass(frozen=True)
class ExecutionResult:
    ok: bool
    request_id: str | None
    exit_code: int | None
    timeout: bool
    flag: str | None
    stdout: str
    stderr: str
    metrics: ResourceMetrics
    image: str
    command: str
    container_id: str | None
    error: str | None = None


def truncate_bytes(data: bytes, limit: int) -> bytes:
    if len(data) <= limit:
        return data
    marker = f"\n...[truncated {len(data) - limit} bytes]...\n".encode("utf-8")
    return data[: max(0, limit - len(marker))] + marker


def decode_stream(data: bytes, limit: int) -> str:
    return truncate_bytes(data, limit).decode("utf-8", errors="replace")


def validate_relative_path(path: str) -> PurePosixPath:
    candidate = PurePosixPath(path)
    if candidate.is_absolute():
        raise SandboxValidationError(f"file path must be relative: {path}")
    if not candidate.parts:
        raise SandboxValidationError("file path cannot be empty")
    if any(part in {"", ".", ".."} for part in candidate.parts):
        raise SandboxValidationError(f"unsafe path segment in: {path}")
    if len(candidate.parts) > 12:
        raise SandboxValidationError(f"file path is too deep: {path}")
    return candidate


def validate_env_key(key: str) -> bool:
    if not key:
        return False
    first = key[0]
    return (first == "_" or first.isalpha()) and all(char == "_" or char.isalnum() for char in key)


def validate_request(request: ExecutionRequest) -> None:
    if not request.files:
        raise SandboxValidationError("at least one source file is required")
    if len(request.files) > MAX_FILE_COUNT:
        raise SandboxValidationError(f"too many files; max={MAX_FILE_COUNT}")
    if not request.run_command.strip():
        raise SandboxValidationError("run_command cannot be empty")
    if request.bash_args != ["-lc"]:
        raise SandboxValidationError('bash_args must be exactly ["-lc"] for deterministic routing')
    total_bytes = 0
    seen: set[str] = set()
    for source in request.files:
        normalized = validate_relative_path(source.path).as_posix()
        if normalized in seen:
            raise SandboxValidationError(f"duplicate file path: {normalized}")
        seen.add(normalized)
        try:
            total_bytes += len(source.content.encode(source.encoding))
        except LookupError as exc:
            raise SandboxValidationError(f"unsupported encoding for {source.path}: {source.encoding}") from exc
        if total_bytes > MAX_SOURCE_BYTES:
            raise SandboxValidationError(f"source payload exceeds {MAX_SOURCE_BYTES} bytes")
    for key in request.env:
        if not validate_env_key(key):
            raise SandboxValidationError(f"unsafe environment key: {key}")


def normalize_execution_request(request: ExecutionRequest | SandboxRequest) -> ExecutionRequest:
    if isinstance(request, ExecutionRequest):
        return request
    if request.workdir != WORKDIR:
        raise SandboxValidationError(f"workdir must be {WORKDIR}")
    if request.user != UNPRIVILEGED_USER:
        raise SandboxValidationError(f"user must be {UNPRIVILEGED_USER}")
    if not request.command:
        raise SandboxValidationError("command cannot be empty")
    return ExecutionRequest(
        files=request.files,
        compile_command=None,
        run_command=shlex.join(request.command),
        image=request.image,
        env=request.env,
        pull_missing_image=request.pull_missing_image,
    )


def write_file_tree(host_workspace: Path, files: Iterable[SandboxFile]) -> None:
    for source in files:
        relative = validate_relative_path(source.path)
        destination = host_workspace / Path(*relative.parts)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(source.content.encode(source.encoding))
        destination.chmod(0o755 if source.executable else 0o444)


def shell_pipeline(compile_command: str | None, run_command: str) -> str:
    if compile_command:
        return f"set -euo pipefail; {compile_command}; exec {run_command}"
    return f"set -euo pipefail; exec {run_command}"


def safe_environment(user_env: dict[str, str]) -> dict[str, str]:
    env = {
        "HOME": "/tmp",  # nosec B108
        "TMPDIR": "/tmp",  # nosec B108
        "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONUNBUFFERED": "1",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
    }
    env.update(user_env)
    return env


def extract_memory_usage(stats: dict[str, Any]) -> tuple[int | None, int | None]:
    memory_stats = stats.get("memory_stats") or {}
    current = memory_stats.get("usage")
    peak = memory_stats.get("max_usage") or memory_stats.get("stats", {}).get("peak")
    return current, peak


def extract_cpu_usage(stats_samples: list[dict[str, Any]]) -> tuple[int | None, int | None, int | None]:
    if not stats_samples:
        return None, None, None
    first = stats_samples[0].get("cpu_stats") or {}
    last = stats_samples[-1].get("cpu_stats") or {}
    first_usage = first.get("cpu_usage") or {}
    last_usage = last.get("cpu_usage") or {}

    def delta(field: str) -> int | None:
        start = first_usage.get(field)
        end = last_usage.get(field)
        if start is None or end is None:
            return None
        return max(0, int(end) - int(start)) // 1000

    return delta("total_usage"), delta("usage_in_kernelmode"), delta("usage_in_usermode")


def metrics_from_samples(start_ns: int, end_ns: int, samples: list[dict[str, Any]]) -> ResourceMetrics:
    wall_time_us = max(0, (end_ns - start_ns) // 1000)
    cpu_total_us, cpu_kernel_us, cpu_user_us = extract_cpu_usage(samples)
    memory_current = None
    memory_peak = None
    for sample in samples:
        current, peak = extract_memory_usage(sample)
        if current is not None:
            memory_current = int(current)
        if peak is not None:
            memory_peak = max(memory_peak or 0, int(peak))
        elif current is not None:
            memory_peak = max(memory_peak or 0, int(current))
    return ResourceMetrics(
        wall_time_us=wall_time_us,
        cpu_total_usage_us=cpu_total_us,
        cpu_kernel_usage_us=cpu_kernel_us,
        cpu_user_usage_us=cpu_user_us,
        memory_current_bytes=memory_current,
        memory_peak_bytes=memory_peak,
        stats_samples=len(samples),
    )


class SandboxEngine:
    """Fully async object-oriented Docker sandbox engine."""

    def __init__(
        self,
        policy: SandboxPolicy | None = None,
        pool_size: int = 4,
        docker_client_factory: Callable[[], docker.DockerClient] = docker.from_env,
    ) -> None:
        self.policy = policy or SandboxPolicy()
        self.policy.validate()
        self.pool_size = pool_size
        self._semaphore = asyncio.Semaphore(pool_size)
        self._client_factory = docker_client_factory
        self._client: docker.DockerClient | None = None
        self._api: docker.APIClient | None = None
        self._queue: asyncio.Queue[
            tuple[ExecutionRequest | SandboxRequest, asyncio.Future[ExecutionResult]]
        ] | None = None
        self._workers: list[asyncio.Task[None]] = []
        self._closed = False

    async def start(self) -> None:
        if self._client is None:
            self._client = await asyncio.to_thread(self._client_factory)
            self._api = docker.APIClient()

    async def close(self) -> None:
        self._closed = True
        if self._queue is not None:
            await self._queue.join()
        for worker in self._workers:
            worker.cancel()
        if self._workers:
            await asyncio.gather(*self._workers, return_exceptions=True)
        if self._client is not None:
            await asyncio.to_thread(self._client.close)
        if self._api is not None:
            await asyncio.to_thread(self._api.close)

    async def __aenter__(self) -> "SandboxEngine":
        await self.start()
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        await self.close()

    def _require_client(self) -> docker.DockerClient:
        if self._client is None:
            raise SandboxLifecycleError("Docker client is not initialized")
        return self._client

    def _require_api(self) -> docker.APIClient:
        if self._api is None:
            raise SandboxLifecycleError("Docker API client is not initialized")
        return self._api

    def _require_queue(self) -> asyncio.Queue[tuple[ExecutionRequest | SandboxRequest, asyncio.Future[ExecutionResult]]]:
        if self._queue is None:
            raise SandboxLifecycleError("sandbox daemon queue is not initialized")
        return self._queue

    async def start_daemon(self) -> None:
        await self.start()
        if self._queue is None:
            self._queue = asyncio.Queue()
        if not self._workers:
            self._workers = [
                asyncio.create_task(self._daemon_worker(index), name=f"sandbox-worker-{index}")
                for index in range(self.pool_size)
            ]

    async def submit(self, request: ExecutionRequest | SandboxRequest) -> ExecutionResult:
        if self._closed:
            raise SandboxLifecycleError("sandbox engine is closed")
        await self.start_daemon()
        queue_ref = self._require_queue()
        loop = asyncio.get_running_loop()
        future: asyncio.Future[ExecutionResult] = loop.create_future()
        await queue_ref.put((request, future))
        return await future

    async def run(self, request: ExecutionRequest | SandboxRequest) -> ExecutionResult:
        if self._closed:
            raise SandboxLifecycleError("sandbox engine is closed")
        request = normalize_execution_request(request)
        validate_request(request)
        await self.start()
        async with self._semaphore:
            return await self._run_once(request)

    async def _daemon_worker(self, index: int) -> None:
        del index
        queue_ref = self._require_queue()
        while True:
            request, future = await queue_ref.get()
            try:
                result = await self.run(request)
                if not future.done():
                    future.set_result(result)
            except Exception as exc:
                if not future.done():
                    future.set_exception(exc)
            finally:
                queue_ref.task_done()

    async def _run_once(self, request: ExecutionRequest) -> ExecutionResult:
        self._require_client()
        self._require_api()

        image = request.image or self.policy.image
        container = None
        temp_dir: str | None = None
        stats_task: asyncio.Task[None] | None = None
        stats_samples: list[dict[str, Any]] = []
        start_ns = time.perf_counter_ns()
        stdout = b""
        stderr = b""
        exit_code: int | None = None
        timeout = False
        error: str | None = None
        exec_id: str | None = None
        command = shell_pipeline(request.compile_command, request.run_command)

        try:
            await self._ensure_image(image, request.pull_missing_image or self.policy.pull_missing_image)
            temp_dir = await asyncio.to_thread(tempfile.mkdtemp, prefix="ai_sandbox_src_")
            host_workspace = Path(temp_dir)
            await asyncio.to_thread(write_file_tree, host_workspace, request.files)
            container = await asyncio.to_thread(
                self._create_container,
                image,
                host_workspace,
                request,
            )
            await asyncio.to_thread(container.start)
            exec_id = await asyncio.to_thread(self._create_exec, container.id, command, request)
            stats_task = asyncio.create_task(self._collect_stats(container.id, stats_samples))
            stream_queue: queue.Queue[tuple[str, bytes | str | None]] = queue.Queue()
            stream_thread = threading.Thread(
                target=self._stream_exec_to_queue,
                args=(exec_id, stream_queue),
                name=f"sandbox-stream-{exec_id[:12]}",
                daemon=True,
            )
            stream_thread.start()
            try:
                stdout, stderr = await self._drain_exec_stream(stream_queue)
                exit_code = await asyncio.to_thread(self._exec_exit_code, exec_id)
            except asyncio.TimeoutError:
                timeout = True
                error = "<|timeout|>"
                await self._kill_container(container)
                await asyncio.to_thread(stream_thread.join, 1.0)
        except Exception as exc:
            if isinstance(exc, SandboxError):
                error = str(exc)
            elif isinstance(exc, ImageNotFound):
                error = f"image not found: {image}"
            else:
                error = f"{type(exc).__name__}: {exc}"
        finally:
            end_ns = time.perf_counter_ns()
            if stats_task is not None:
                stats_task.cancel()
                await self._finish_stats_task(stats_task)
            if container is not None:
                await self._remove_container(container)
            if temp_dir is not None:
                await asyncio.to_thread(shutil.rmtree, temp_dir, True)

        metrics = metrics_from_samples(start_ns, end_ns, stats_samples)
        if timeout:
            exit_code = None
        return ExecutionResult(
            ok=(exit_code == 0 and not timeout and error is None),
            request_id=request.request_id,
            exit_code=exit_code,
            timeout=timeout,
            flag="<|timeout|>" if timeout else None,
            stdout=decode_stream(stdout, self.policy.stdout_limit_bytes),
            stderr=decode_stream(stderr, self.policy.stderr_limit_bytes),
            metrics=metrics,
            image=image,
            command=command,
            container_id=getattr(container, "id", None),
            error=error,
        )

    def _create_container(
        self,
        image: str,
        host_workspace: Path,
        request: ExecutionRequest,
    ) -> Any:
        client = self._require_client()
        return client.containers.create(
            image=image,
            command=["sleep", "infinity"],
            working_dir=WORKDIR,
            user=self.policy.user,
            environment=safe_environment(request.env),
            network_disabled=True,
            network_mode=self.policy.network_mode,
            detach=True,
            stdin_open=False,
            tty=False,
            read_only=self.policy.read_only_root,
            mem_limit=self.policy.memory,
            memswap_limit=self.policy.memory,
            nano_cpus=self.policy.nano_cpus,
            pids_limit=self.policy.pids_limit,
            cap_drop=["ALL"],
            security_opt=["no-new-privileges:true"],
            tmpfs=self.policy.tmpfs,
            volumes={
                str(host_workspace): {
                    "bind": WORKDIR,
                    "mode": "ro",
                }
            },
            labels={
                "ai-architecture.phase": "2",
                "ai-architecture.component": "async-sandbox-engine",
            },
        )

    def _create_exec(self, container_id: str, command: str, request: ExecutionRequest) -> str:
        api = self._require_api()
        created = api.exec_create(
            container=container_id,
            cmd=["bash", *request.bash_args, command],
            stdout=True,
            stderr=True,
            stdin=False,
            tty=False,
            privileged=False,
            user=self.policy.user,
            workdir=WORKDIR,
            environment=safe_environment(request.env),
        )
        return str(created["Id"])

    def _stream_exec(self, exec_id: str) -> tuple[bytes, bytes]:
        api = self._require_api()
        stdout_chunks: list[bytes] = []
        stderr_chunks: list[bytes] = []
        stream = api.exec_start(exec_id, stream=True, demux=True, tty=False)
        for item in stream:
            if item is None:
                continue
            out, err = item
            if out:
                stdout_chunks.append(out)
            if err:
                stderr_chunks.append(err)
        return b"".join(stdout_chunks), b"".join(stderr_chunks)

    def _stream_exec_to_queue(
        self,
        exec_id: str,
        output_queue: queue.Queue[tuple[str, bytes | str | None]],
    ) -> None:
        api = self._require_api()
        try:
            stream = api.exec_start(exec_id, stream=True, demux=True, tty=False)
            for item in stream:
                if item is None:
                    continue
                out, err = item
                if out:
                    output_queue.put(("stdout", out))
                if err:
                    output_queue.put(("stderr", err))
            output_queue.put(("done", None))
        except Exception as exc:
            output_queue.put(("error", f"{type(exc).__name__}: {exc}"))
            output_queue.put(("done", None))

    async def _drain_exec_stream(
        self,
        output_queue: queue.Queue[tuple[str, bytes | str | None]],
    ) -> tuple[bytes, bytes]:
        deadline = time.monotonic() + self.policy.timeout_ms / 1000
        stdout_chunks: list[bytes] = []
        stderr_chunks: list[bytes] = []
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise asyncio.TimeoutError
            channel, payload = await asyncio.wait_for(
                asyncio.to_thread(output_queue.get),
                timeout=remaining,
            )
            if channel == "done":
                return b"".join(stdout_chunks), b"".join(stderr_chunks)
            if channel == "stdout" and isinstance(payload, bytes):
                stdout_chunks.append(payload)
            elif channel == "stderr" and isinstance(payload, bytes):
                stderr_chunks.append(payload)
            elif channel == "error":
                stderr_chunks.append(str(payload).encode("utf-8", errors="replace"))

    def _exec_exit_code(self, exec_id: str) -> int | None:
        api = self._require_api()
        inspected = api.exec_inspect(exec_id)
        value = inspected.get("ExitCode")
        return int(value) if value is not None else None

    async def _collect_stats(self, container_id: str, samples: list[dict[str, Any]]) -> None:
        api = self._require_api()
        while True:
            try:
                sample = await asyncio.to_thread(api.stats, container_id, False)
                if isinstance(sample, dict):
                    samples.append(sample)
            except (APIError, DockerException, NotFound):
                break
            await asyncio.sleep(0.05)

    async def _finish_stats_task(self, task: asyncio.Task[None]) -> None:
        try:
            await task
        except asyncio.CancelledError:
            return
        except Exception:
            return

    async def _ensure_image(self, image: str, pull_missing: bool) -> None:
        client = self._require_client()
        try:
            await asyncio.to_thread(client.images.get, image)
        except ImageNotFound:
            if not pull_missing:
                raise
            await asyncio.to_thread(client.images.pull, image)

    async def _kill_container(self, container: Any) -> None:
        try:
            await asyncio.to_thread(container.kill)
        except (APIError, DockerException, NotFound):
            pass

    async def _remove_container(self, container: Any) -> None:
        try:
            await asyncio.to_thread(container.remove, force=True)
        except (APIError, DockerException, NotFound):
            pass


class DockerSandboxEngine:
    """Synchronous compatibility wrapper for older Phase 3/5 imports."""

    def __init__(self, policy: SandboxPolicy | None = None, pool_size: int = 1) -> None:
        self.policy = policy
        self.pool_size = pool_size

    def run(self, request: ExecutionRequest | SandboxRequest) -> ExecutionResult:
        async def _run() -> ExecutionResult:
            async with SandboxEngine(policy=self.policy, pool_size=self.pool_size) as engine:
                return await engine.run(request)

        return asyncio.run(_run())


def request_from_language(
    language: str,
    code: str,
    image: str,
    compiler: str | None,
    run: str | None,
    request_id: str | None,
    pull_missing_image: bool,
) -> ExecutionRequest:
    preset = LANGUAGE_PRESETS.get(language.lower())
    if preset is None:
        raise SandboxValidationError(f"unsupported language preset: {language}")
    file_path = str(preset["path"])
    compile_command = compiler if compiler is not None else preset.get("compile")
    run_command = run if run is not None else str(preset["run"])
    return ExecutionRequest(
        files=[
            SandboxFile(
                path=file_path,
                content=code,
                executable=bool(preset.get("executable", False)),
            )
        ],
        compile_command=compile_command,
        run_command=run_command,
        image=image,
        request_id=request_id,
        pull_missing_image=pull_missing_image,
    )


def request_from_json(path: Path) -> ExecutionRequest:
    payload = json.loads(path.read_text(encoding="utf-8"))
    files = [SandboxFile(**item) for item in payload["files"]]
    return ExecutionRequest(
        files=files,
        compile_command=payload.get("compile_command"),
        run_command=str(payload["run_command"]),
        image=str(payload.get("image", DEFAULT_IMAGE)),
        bash_args=list(payload.get("bash_args", ["-lc"])),
        env=dict(payload.get("env", {})),
        request_id=payload.get("request_id"),
        pull_missing_image=bool(payload.get("pull_missing_image", False)),
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Async hardened Docker sandbox engine.")
    subparsers = parser.add_subparsers(dest="command_name", required=True)

    run_code = subparsers.add_parser("run-code", help="Run a single source snippet.")
    run_code.add_argument("--language", required=True, choices=sorted(LANGUAGE_PRESETS))
    run_code.add_argument("--code")
    run_code.add_argument("--code-base64")
    run_code.add_argument("--image", default=DEFAULT_IMAGE)
    run_code.add_argument("--compiler", help="Compile command, e.g. 'g++ -O3 /workspace/main.cpp -o /tmp/a.out'.")
    run_code.add_argument("--run", help="Run command, e.g. '/tmp/a.out --case 1'.")
    run_code.add_argument("--request-id")
    run_code.add_argument("--pull-missing-image", action="store_true")

    run_json = subparsers.add_parser("run-json", help="Run a full ExecutionRequest JSON file.")
    run_json.add_argument("--request", required=True, type=Path)

    return parser.parse_args()


def code_from_args(args: argparse.Namespace) -> str:
    if args.code is not None:
        return args.code
    if args.code_base64 is not None:
        return base64.b64decode(args.code_base64).decode("utf-8", errors="replace")
    raise SystemExit("Provide --code or --code-base64.")


async def async_main() -> None:
    args = parse_args()
    try:
        if args.command_name == "run-code":
            request = request_from_language(
                language=args.language,
                code=code_from_args(args),
                image=args.image,
                compiler=args.compiler,
                run=args.run,
                request_id=args.request_id,
                pull_missing_image=args.pull_missing_image,
            )
        else:
            request = request_from_json(args.request)
        async with SandboxEngine() as engine:
            result = await engine.run(request)
        print(json.dumps(asdict(result), indent=2, ensure_ascii=False))
        raise SystemExit(0 if result.ok else 1)
    except (SandboxError, DockerException) as exc:
        payload = {
            "ok": False,
            "exit_code": None,
            "timeout": False,
            "flag": None,
            "stdout": "",
            "stderr": "",
            "error": f"{type(exc).__name__}: {exc}",
        }
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        raise SystemExit(1)


def main() -> None:
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
