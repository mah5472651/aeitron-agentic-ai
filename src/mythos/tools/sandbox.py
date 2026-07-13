"""Hardened Docker sandbox runner for untrusted code execution."""

from __future__ import annotations

import tarfile
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from io import BytesIO
from pathlib import Path
from typing import Any

from pydantic import Field

from src.mythos.shared.schemas import StrictModel


class HardenedSandboxPolicy(StrictModel):
    image: str = "ubuntu:24.04"
    user: str = "65534:65534"
    network_mode: str = "none"
    mem_limit: str = "512m"
    nano_cpus: int = 1_000_000_000
    read_only: bool = True
    cap_drop: list[str] = Field(default_factory=lambda: ["ALL"])
    security_opt: list[str] = Field(default_factory=lambda: ["no-new-privileges:true"])
    tmpfs: dict[str, str] = Field(default_factory=lambda: {"/tmp": "rw,noexec,nosuid,size=32m"})  # nosec B108 - isolated container tmpfs, noexec and nosuid.
    timeout_ms: int = Field(default=5_000, ge=100, le=300_000)


class SandboxRunRequest(StrictModel):
    command: list[str] = Field(min_length=1)
    files: dict[str, str] = Field(default_factory=dict)
    workdir: str = "/workspace"
    policy: HardenedSandboxPolicy = Field(default_factory=HardenedSandboxPolicy)


class SandboxRunResult(StrictModel):
    status: str
    stdout: str = ""
    stderr: str = ""
    exit_code: int | None = None
    duration_ms: float
    policy: dict[str, Any]
    reason: str = ""


class DockerSandboxRunner:
    def run(self, request: SandboxRunRequest) -> SandboxRunResult:
        started = time.perf_counter()
        try:
            import docker
        except ImportError:
            return self.unavailable(request, started, "docker SDK is not installed")
        try:
            client = docker.from_env()
            client.ping()
        except Exception as exc:
            return self.unavailable(request, started, f"docker engine unavailable: {exc}")

        container = None
        try:
            container = client.containers.create(
                request.policy.image,
                command=["sleep", "300"],
                detach=True,
                network_mode=request.policy.network_mode,
                mem_limit=request.policy.mem_limit,
                nano_cpus=request.policy.nano_cpus,
                read_only=request.policy.read_only,
                cap_drop=request.policy.cap_drop,
                security_opt=request.policy.security_opt,
                tmpfs=request.policy.tmpfs,
                user=request.policy.user,
                working_dir=request.workdir,
            )
            container.start()
            if request.files:
                container.put_archive(request.workdir, self.files_to_tar(request.files))
            executor = ThreadPoolExecutor(max_workers=1)
            future = executor.submit(
                container.exec_run,
                request.command,
                stdout=True,
                stderr=True,
                demux=True,
                workdir=request.workdir,
                user=request.policy.user,
            )
            try:
                exec_result = future.result(timeout=request.policy.timeout_ms / 1000)
            except TimeoutError:
                future.cancel()
                executor.shutdown(wait=False, cancel_futures=True)
                try:
                    container.kill()
                except Exception:
                    pass
                return SandboxRunResult(
                    status="timeout",
                    stderr="<|timeout|>",
                    exit_code=None,
                    duration_ms=(time.perf_counter() - started) * 1000,
                    policy=request.policy.model_dump(),
                    reason="sandbox timeout exceeded",
                )
            finally:
                if not future.cancelled():
                    executor.shutdown(wait=False, cancel_futures=True)
            stdout_raw, stderr_raw = exec_result.output if isinstance(exec_result.output, tuple) else (exec_result.output, b"")
            return SandboxRunResult(
                status="ok" if exec_result.exit_code == 0 else "failed",
                stdout=(stdout_raw or b"").decode("utf-8", errors="replace")[-20_000:],
                stderr=(stderr_raw or b"").decode("utf-8", errors="replace")[-20_000:],
                exit_code=exec_result.exit_code,
                duration_ms=(time.perf_counter() - started) * 1000,
                policy=request.policy.model_dump(),
            )
        except Exception as exc:
            return SandboxRunResult(
                status="failed",
                stderr=str(exc),
                exit_code=None,
                duration_ms=(time.perf_counter() - started) * 1000,
                policy=request.policy.model_dump(),
                reason="sandbox execution failed",
            )
        finally:
            if container is not None:
                try:
                    container.remove(force=True)
                except Exception:
                    pass

    def unavailable(self, request: SandboxRunRequest, started: float, reason: str) -> SandboxRunResult:
        return SandboxRunResult(
            status="unavailable",
            duration_ms=(time.perf_counter() - started) * 1000,
            policy=request.policy.model_dump(),
            reason=reason,
        )

    def files_to_tar(self, files: dict[str, str]) -> bytes:
        stream = BytesIO()
        with tarfile.open(fileobj=stream, mode="w") as archive:
            for relative, content in files.items():
                normalized = relative.replace("\\", "/").lstrip("/")
                if not normalized or normalized.startswith("../") or "/../" in f"/{normalized}/":
                    raise ValueError(f"unsafe sandbox file path: {relative}")
                data = content.encode("utf-8")
                info = tarfile.TarInfo(normalized)
                info.size = len(data)
                archive.addfile(info, BytesIO(data))
        stream.seek(0)
        return stream.read()


def run_local_sandbox_smoke(output_dir: str | Path) -> dict[str, Any]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    request = SandboxRunRequest(command=["python3", "-c", "print('sandbox-ok')"])
    result = DockerSandboxRunner().run(request)
    report = result.model_dump()
    (root / "sandbox_report.json").write_text(str(report), encoding="utf-8")
    return report
