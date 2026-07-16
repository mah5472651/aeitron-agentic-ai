"""Measured production-proof harness for the Aeitron training workspace.

The harness never converts a missing external dependency into a pass. Local
Docker can prove the Postgres, Redis, and MinIO lifecycle. Kubernetes, Slurm,
multi-node GPU, long soak, and 60B resume proofs require their real targets.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import shutil
import subprocess  # nosec B404 - fixed proof commands, no shell execution
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import Field

from src.aeitron.db.migration_runner import apply_migrations
from src.aeitron.learning.storage import ObjectStoreConfig, S3ObjectStore, verify_object_store_lifecycle
from src.aeitron.shared.schemas import StrictModel
from src.aeitron.training_workspace import (
    JobStatus,
    PostgresTrainingStore,
    QualificationCampaignRegistry,
    RedisEventBus,
    SchedulerAdapter,
    SchedulerBinding,
    TrainingAttempt,
    TrainingController,
    TrainingJob,
    TrainingEventBatch,
    TrainingEventInput,
    TrainingProfileRegistry,
    TrainingWorkspaceService,
)


ProofStatus = Literal["passed", "failed", "blocked"]


class ProofResult(StrictModel):
    name: str
    status: ProofStatus
    started_at: str
    duration_seconds: float = Field(ge=0.0)
    evidence: dict[str, Any] = Field(default_factory=dict)
    blockers: list[str] = Field(default_factory=list)


class ProductionProofReport(StrictModel):
    schema_version: int = 1
    generated_at: str
    environment: dict[str, Any]
    proofs: list[ProofResult]
    passed: int
    failed: int
    blocked: int
    status: ProofStatus


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def command_evidence(argv: list[str], *, timeout: int = 30) -> dict[str, Any]:
    executable = shutil.which(argv[0])
    if executable is None:
        raise FileNotFoundError(f"required executable is missing: {argv[0]}")
    completed = subprocess.run(  # nosec B603 - argv is internally constructed
        [executable, *argv[1:]],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )
    return {
        "argv": argv,
        "exit_code": completed.returncode,
        "stdout": completed.stdout[-8000:],
        "stderr": completed.stderr[-8000:],
    }


async def infrastructure_lifecycle_proof(
    *,
    database_url: str,
    redis_url: str,
    object_store_uri: str,
    object_store_endpoint_url: str,
    output_dir: Path,
) -> ProofResult:
    started = time.perf_counter()
    started_at = now_iso()
    service: TrainingWorkspaceService | None = None
    try:
        import boto3

        bucket = urlparse(object_store_uri).netloc
        s3_client = boto3.client("s3", endpoint_url=object_store_endpoint_url)
        try:
            await asyncio.to_thread(s3_client.head_bucket, Bucket=bucket)
        except Exception:
            await asyncio.to_thread(s3_client.create_bucket, Bucket=bucket)
        migration = await apply_migrations(database_url)
        object_report = await asyncio.to_thread(
            verify_object_store_lifecycle,
            config=ObjectStoreConfig(uri=object_store_uri, endpoint_url=object_store_endpoint_url),
            work_dir=output_dir / "object-lifecycle",
            key=f"proofs/{uuid.uuid4()}/lifecycle.json",
        )
        store = PostgresTrainingStore(database_url)
        events = RedisEventBus(redis_url, retention_events=2_000_000)
        object_store = S3ObjectStore(object_store_uri, endpoint_url=object_store_endpoint_url)
        service = TrainingWorkspaceService(
            store=store,
            events=events,
            object_store=object_store,
            profiles=TrainingProfileRegistry.from_file(),
            campaigns=QualificationCampaignRegistry.from_file(),
        )
        owner = f"proof-{uuid.uuid4()}"
        from src.aeitron.training_workspace import TrainingJobCreateRequest

        job = await service.create_job(
            TrainingJobCreateRequest(
                profile_id="defensive-1k",
                idempotency_key=f"proof-{uuid.uuid4()}",
                git_commit="abcdef1",
                container_digest="sha256:" + "a" * 64,
                metadata={"proof": "postgres-redis-minio-lifecycle"},
            ),
            owner_id=owner,
        )
        running, attempt = await service.claim_notebook_job(job.job_id)
        accepted = await service.ingest_events(
            running.job_id,
            TrainingEventBatch(
                attempt_id=attempt.attempt_id,
                events=[
                    TrainingEventInput(
                        source_sequence=1,
                        kind="heartbeat",
                        stage="proof",
                        status="running",
                        payload={"probe": True},
                    )
                ],
            ),
        )
        replay = await service.ingest_events(
            running.job_id,
            TrainingEventBatch(
                attempt_id=attempt.attempt_id,
                events=[
                    TrainingEventInput(
                        source_sequence=1,
                        kind="heartbeat",
                        stage="proof",
                        status="running",
                    )
                ],
            ),
        )
        persisted_events = await events.read(job.job_id, after_sequence=0, limit=10)
        cancelled = await service.cancel_job(job.job_id, actor_id="proof-runner")
        import asyncpg

        connection = await asyncpg.connect(database_url)
        try:
            database_evidence = {
                "job_rows": await connection.fetchval("SELECT count(*) FROM training_jobs WHERE id=$1", uuid.UUID(job.job_id)),
                "attempt_rows": await connection.fetchval("SELECT count(*) FROM training_attempts WHERE job_id=$1", uuid.UUID(job.job_id)),
                "ingress_rows": await connection.fetchval(
                    "SELECT count(*) FROM training_event_ingress WHERE attempt_id=$1", uuid.UUID(attempt.attempt_id)
                ),
            }
        finally:
            await connection.close()
        passed = (
            object_report.status == "passed"
            and len(accepted) == 1
            and not replay
            and len(persisted_events) == 1
            and cancelled.status == JobStatus.CANCELLED
            and all(value == 1 for value in database_evidence.values())
        )
        return ProofResult(
            name="postgres_redis_minio_lifecycle",
            status="passed" if passed else "failed",
            started_at=started_at,
            duration_seconds=time.perf_counter() - started,
            evidence={
                "migration": migration,
                "object_lifecycle": object_report.model_dump(mode="json"),
                "job_id": job.job_id,
                "attempt_id": attempt.attempt_id,
                "redis_event_count": len(persisted_events),
                "duplicate_event_rejected": not replay,
                "database": database_evidence,
            },
        )
    except Exception as exc:
        return ProofResult(
            name="postgres_redis_minio_lifecycle",
            status="failed",
            started_at=started_at,
            duration_seconds=time.perf_counter() - started,
            blockers=[f"{type(exc).__name__}: {exc}"],
        )
    finally:
        if service is not None:
            await service.close()


async def million_event_proof(
    *,
    database_url: str,
    redis_url: str,
    object_store_uri: str,
    object_store_endpoint_url: str,
    event_count: int,
) -> ProofResult:
    started = time.perf_counter()
    started_at = now_iso()
    service: TrainingWorkspaceService | None = None
    try:
        store = PostgresTrainingStore(database_url)
        events = RedisEventBus(redis_url, retention_events=max(1_100_000, event_count + 1000))
        service = TrainingWorkspaceService(
            store=store,
            events=events,
            object_store=S3ObjectStore(object_store_uri, endpoint_url=object_store_endpoint_url),
        )
        from src.aeitron.training_workspace import TrainingJobCreateRequest

        job = await service.create_job(
            TrainingJobCreateRequest(
                profile_id="defensive-1k",
                idempotency_key=f"event-proof-{uuid.uuid4()}",
                git_commit="abcdef1",
                container_digest="sha256:" + "b" * 64,
            ),
            owner_id=f"event-proof-{uuid.uuid4()}",
        )
        running, attempt = await service.claim_notebook_job(job.job_id)
        batch_size = 100
        for offset in range(0, event_count, batch_size):
            size = min(batch_size, event_count - offset)
            batch = TrainingEventBatch(
                attempt_id=attempt.attempt_id,
                events=[
                    TrainingEventInput(
                        source_sequence=offset + index + 1,
                        kind="metric",
                        stage="stress",
                        status="running",
                        step=offset + index + 1,
                        max_steps=event_count,
                        loss=1.0,
                    )
                    for index in range(size)
                ],
            )
            accepted = await service.ingest_events(running.job_id, batch)
            if len(accepted) != size:
                raise RuntimeError(f"event batch loss at offset {offset}: accepted={len(accepted)}, expected={size}")
        final_job = await service.get_job(job.job_id)
        tail = await events.read(job.job_id, after_sequence=max(0, event_count - 1), limit=2)
        duration = time.perf_counter() - started
        passed = final_job.event_sequence == event_count and len(tail) == 1 and tail[0].sequence == event_count
        await service.cancel_job(job.job_id, actor_id="proof-runner")
        return ProofResult(
            name=f"ordered_event_stress_{event_count}",
            status="passed" if passed else "failed",
            started_at=started_at,
            duration_seconds=duration,
            evidence={
                "job_id": job.job_id,
                "event_count": event_count,
                "final_sequence": final_job.event_sequence,
                "events_per_second": event_count / max(duration, 1e-9),
                "tail_sequence": tail[0].sequence if tail else None,
            },
        )
    except Exception as exc:
        return ProofResult(
            name=f"ordered_event_stress_{event_count}",
            status="failed",
            started_at=started_at,
            duration_seconds=time.perf_counter() - started,
            blockers=[f"{type(exc).__name__}: {exc}"],
        )
    finally:
        if service is not None:
            await service.close()


async def soak_proof(
    *,
    database_url: str,
    redis_url: str,
    object_store_uri: str,
    object_store_endpoint_url: str,
    duration_seconds: int,
    interval_seconds: int,
) -> ProofResult:
    started = time.perf_counter()
    started_at = now_iso()
    checks = 0
    failures: list[str] = []
    try:
        import asyncpg
        import redis.asyncio as redis

        redis_client = redis.from_url(redis_url, decode_responses=True)
        object_store = S3ObjectStore(object_store_uri, endpoint_url=object_store_endpoint_url)
        deadline = time.monotonic() + duration_seconds
        while time.monotonic() < deadline:
            try:
                connection = await asyncpg.connect(database_url)
                try:
                    await connection.fetchval("SELECT 1")
                finally:
                    await connection.close()
                if not await redis_client.ping():
                    raise RuntimeError("Redis ping returned false")
                await asyncio.to_thread(object_store.list_objects, "proofs")
                checks += 1
            except Exception as exc:
                failures.append(f"{type(exc).__name__}: {exc}")
            await asyncio.sleep(min(interval_seconds, max(0.0, deadline - time.monotonic())))
        await redis_client.aclose()
        measured = time.perf_counter() - started
        full_duration = measured >= duration_seconds * 0.99
        return ProofResult(
            name=f"infrastructure_soak_{duration_seconds}s",
            status="passed" if full_duration and checks > 0 and not failures else "failed",
            started_at=started_at,
            duration_seconds=measured,
            evidence={"requested_seconds": duration_seconds, "health_checks": checks},
            blockers=failures[-20:],
        )
    except Exception as exc:
        return ProofResult(
            name=f"infrastructure_soak_{duration_seconds}s",
            status="failed",
            started_at=started_at,
            duration_seconds=time.perf_counter() - started,
            blockers=[f"{type(exc).__name__}: {exc}"],
        )


def external_scheduler_proofs() -> list[ProofResult]:
    results: list[ProofResult] = []
    started_at = now_iso()
    started = time.perf_counter()
    try:
        cluster = command_evidence(["kubectl", "cluster-info"], timeout=20)
        nodes = command_evidence(["kubectl", "get", "nodes", "-o", "json"], timeout=20)
        crd = command_evidence(["kubectl", "get", "crd", "pytorchjobs.kubeflow.org", "-o", "name"], timeout=20)
        passed = all(item["exit_code"] == 0 for item in (cluster, nodes, crd))
        results.append(
            ProofResult(
                name="kubernetes_pytorchjob_cluster",
                status="passed" if passed else "blocked",
                started_at=started_at,
                duration_seconds=time.perf_counter() - started,
                evidence={"cluster": cluster, "nodes": nodes, "pytorchjob_crd": crd},
                blockers=[] if passed else ["reachable Kubernetes cluster with Kubeflow PyTorchJob CRD is required"],
            )
        )
    except Exception as exc:
        results.append(
            ProofResult(
                name="kubernetes_pytorchjob_cluster",
                status="blocked",
                started_at=started_at,
                duration_seconds=time.perf_counter() - started,
                blockers=[f"{type(exc).__name__}: {exc}"],
            )
        )

    started_at = now_iso()
    started = time.perf_counter()
    try:
        sinfo = command_evidence(["sinfo", "--json"], timeout=20)
        squeue = command_evidence(["squeue", "--json"], timeout=20)
        passed = sinfo["exit_code"] == 0 and squeue["exit_code"] == 0
        results.append(
            ProofResult(
                name="slurm_cluster",
                status="passed" if passed else "blocked",
                started_at=started_at,
                duration_seconds=time.perf_counter() - started,
                evidence={"sinfo": sinfo, "squeue": squeue},
                blockers=[] if passed else ["reachable Slurm controller and authenticated account are required"],
            )
        )
    except Exception as exc:
        results.append(
            ProofResult(
                name="slurm_cluster",
                status="blocked",
                started_at=started_at,
                duration_seconds=time.perf_counter() - started,
                blockers=[f"{type(exc).__name__}: {exc}"],
            )
        )
    return results


def docker_disaster_recovery_proof(
    *,
    project_name: str,
    redis_url: str,
    object_store_uri: str,
    object_store_endpoint_url: str,
) -> ProofResult:
    started = time.perf_counter()
    started_at = now_iso()
    evidence: dict[str, Any] = {}
    try:
        import boto3
        import docker
        import redis

        client = docker.from_env()

        def service_container(service: str) -> Any:
            matches = client.containers.list(
                all=True,
                filters={
                    "label": [
                        f"com.docker.compose.project={project_name}",
                        f"com.docker.compose.service={service}",
                    ]
                },
            )
            if len(matches) != 1:
                raise RuntimeError(f"expected one {service} proof container, found {len(matches)}")
            return matches[0]

        postgres = service_container("postgres")
        redis_container = service_container("redis")
        minio = service_container("minio")

        def exec_checked(container: Any, command: list[str]) -> str:
            result = container.exec_run(command)
            output = result.output.decode("utf-8", "replace")
            if result.exit_code != 0:
                raise RuntimeError(f"container command failed ({container.name}): {output[-2000:]}")
            return output

        dump_path = f"/var/lib/postgresql/data/aeitron-proof-dr-{uuid.uuid4().hex}.dump"
        try:
            exec_checked(
                postgres,
                ["pg_dump", "-U", "aeitron", "-d", "aeitron_proof", "-Fc", "-f", dump_path],
            )
            exec_checked(postgres, ["dropdb", "-U", "aeitron", "--if-exists", "aeitron_proof_restore"])
            exec_checked(postgres, ["createdb", "-U", "aeitron", "aeitron_proof_restore"])
            exec_checked(
                postgres,
                ["pg_restore", "-U", "aeitron", "-d", "aeitron_proof_restore", "--no-owner", dump_path],
            )
            restored_count = int(
                exec_checked(
                    postgres,
                    ["psql", "-U", "aeitron", "-d", "aeitron_proof_restore", "-Atc", "SELECT count(*) FROM schema_migrations"],
                ).strip()
            )
        finally:
            postgres.exec_run(["rm", "-f", "--", dump_path])
        if restored_count < 1:
            raise RuntimeError("restored Postgres database has no migration history")

        redis_client = redis.from_url(redis_url, decode_responses=True)
        redis_key = f"aeitron:proof:restart:{uuid.uuid4()}"
        redis_client.set(redis_key, "persisted")
        redis_client.bgsave()
        deadline = time.monotonic() + 30
        while redis_client.info("persistence").get("rdb_bgsave_in_progress") and time.monotonic() < deadline:
            time.sleep(0.2)
        redis_container.restart(timeout=15)
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            try:
                if redis_client.ping() and redis_client.get(redis_key) == "persisted":
                    break
            except Exception:
                time.sleep(0.5)
        else:
            raise RuntimeError("Redis did not recover the persisted proof key after restart")
        redis_client.delete(redis_key)

        bucket = urlparse(object_store_uri).netloc
        prefix = urlparse(object_store_uri).path.strip("/")
        object_key = f"{prefix}/proofs/restart-{uuid.uuid4()}.json".strip("/")
        s3 = boto3.client("s3", endpoint_url=object_store_endpoint_url)
        body = json.dumps({"proof": "minio-restart"}, sort_keys=True).encode()
        digest = hashlib.sha256(body).hexdigest()
        s3.put_object(Bucket=bucket, Key=object_key, Body=body, Metadata={"sha256": digest})
        minio.restart(timeout=15)
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            try:
                recovered = s3.get_object(Bucket=bucket, Key=object_key)["Body"].read()
                if hashlib.sha256(recovered).hexdigest() == digest:
                    break
            except Exception:
                time.sleep(0.5)
        else:
            raise RuntimeError("MinIO object did not recover after restart")
        s3.delete_object(Bucket=bucket, Key=object_key)

        postgres.restart(timeout=15)
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            result = postgres.exec_run(
                ["pg_isready", "-U", "aeitron", "-d", "aeitron_proof"],
            )
            if result.exit_code == 0:
                break
            time.sleep(0.5)
        else:
            raise RuntimeError("Postgres did not become ready after restart")
        evidence = {
            "postgres_dump_restore": True,
            "postgres_restored_migrations": restored_count,
            "postgres_restart": True,
            "redis_aof_restart": True,
            "minio_volume_restart": True,
            "containers": {
                "postgres": postgres.name,
                "redis": redis_container.name,
                "minio": minio.name,
            },
        }
        return ProofResult(
            name="docker_disaster_recovery_drill",
            status="passed",
            started_at=started_at,
            duration_seconds=time.perf_counter() - started,
            evidence=evidence,
        )
    except Exception as exc:
        return ProofResult(
            name="docker_disaster_recovery_drill",
            status="failed",
            started_at=started_at,
            duration_seconds=time.perf_counter() - started,
            evidence=evidence,
            blockers=[f"{type(exc).__name__}: {exc}"],
        )


async def worker_loss_recovery_proof(
    *,
    database_url: str,
    redis_url: str,
    object_store_uri: str,
    object_store_endpoint_url: str,
) -> ProofResult:
    started = time.perf_counter()
    started_at = now_iso()
    service: TrainingWorkspaceService | None = None
    container: Any | None = None
    try:
        import docker

        docker_client = docker.from_env()

        class DockerLossScheduler(SchedulerAdapter):
            name = "kubernetes"

            async def validate(self, spec: Any) -> dict[str, Any]:
                return {"status": "ready", "proof_scheduler": "docker-process"}

            async def submit(self, job: TrainingJob, attempt: TrainingAttempt) -> SchedulerBinding:
                nonlocal container
                container = await asyncio.to_thread(
                    docker_client.containers.run,
                    "alpine:3.20",
                    ["sleep", "300"],
                    detach=True,
                    network_disabled=True,
                    read_only=True,
                    mem_limit="64m",
                    nano_cpus=100_000_000,
                    labels={"aeitron.proof": "worker-loss", "aeitron.job-id": job.job_id},
                )
                return SchedulerBinding(
                    scheduler=self.name,
                    external_id=container.id,
                    metadata={"failure_class": "node_loss"},
                )

            async def status(self, binding: SchedulerBinding) -> str:
                active = docker_client.containers.get(binding.external_id)
                await asyncio.to_thread(active.reload)
                if active.status in {"created", "restarting"}:
                    return "provisioning"
                if active.status == "running":
                    return "running"
                result = await asyncio.to_thread(active.wait)
                return "succeeded" if int(result.get("StatusCode", 1)) == 0 else "failed"

            async def cancel(self, binding: SchedulerBinding) -> None:
                active = docker_client.containers.get(binding.external_id)
                await asyncio.to_thread(active.remove, force=True)

        service = TrainingWorkspaceService(
            store=PostgresTrainingStore(database_url),
            events=RedisEventBus(redis_url),
            object_store=S3ObjectStore(object_store_uri, endpoint_url=object_store_endpoint_url),
        )
        from src.aeitron.training_workspace import TrainingJobCreateRequest

        job = await service.create_job(
            TrainingJobCreateRequest(
                profile_id="defensive-10k",
                idempotency_key=f"worker-loss-{uuid.uuid4()}",
                git_commit="abcdef1",
                container_digest="sha256:" + "c" * 64,
            ),
            owner_id=f"worker-loss-{uuid.uuid4()}",
        )
        controller = TrainingController(service, schedulers={"kubernetes": DockerLossScheduler()})
        submitted = await controller.reconcile_job(job)
        running = await controller.reconcile_job(submitted)
        if running.status != JobStatus.RUNNING or container is None:
            raise RuntimeError(f"proof worker did not reach running state: {running.status.value}")
        await asyncio.to_thread(container.kill)
        requeued = await controller.reconcile_job(running)
        attempts = await service.store.list_attempts(job.job_id)
        passed = (
            requeued.status == JobStatus.QUEUED
            and len(attempts) == 1
            and requeued.scheduler_binding.get("retry_failure_class") == "node_loss"
            and float(requeued.scheduler_binding.get("retry_not_before_unix", 0)) > time.time()
        )
        return ProofResult(
            name="worker_loss_retry_recovery",
            status="passed" if passed else "failed",
            started_at=started_at,
            duration_seconds=time.perf_counter() - started,
            evidence={
                "job_id": job.job_id,
                "status_after_loss": requeued.status.value,
                "attempt_count": len(attempts),
                "retry_failure_class": requeued.scheduler_binding.get("retry_failure_class"),
                "retry_not_before_unix": requeued.scheduler_binding.get("retry_not_before_unix"),
            },
        )
    except Exception as exc:
        return ProofResult(
            name="worker_loss_retry_recovery",
            status="failed",
            started_at=started_at,
            duration_seconds=time.perf_counter() - started,
            blockers=[f"{type(exc).__name__}: {exc}"],
        )
    finally:
        if container is not None:
            try:
                await asyncio.to_thread(container.remove, force=True)
            except Exception:
                pass
        if service is not None:
            await service.close()


def distributed_gpu_proofs() -> list[ProofResult]:
    started_at = now_iso()
    started = time.perf_counter()
    evidence: dict[str, Any] = {}
    blockers: list[str] = []
    try:
        import torch

        evidence = {
            "cuda_available": torch.cuda.is_available(),
            "gpu_count": torch.cuda.device_count(),
            "gpu_names": [torch.cuda.get_device_name(index) for index in range(torch.cuda.device_count())],
            "torch_version": torch.__version__,
        }
        if torch.cuda.device_count() < 2:
            blockers.append("at least two visible CUDA GPUs are required for multi-GPU FSDP proof")
        if shutil.which("deepspeed") is None:
            blockers.append("DeepSpeed executable is required for ZeRO-3 proof")
        megatron_root = os.environ.get("AEITRON_MEGATRON_ROOT", "")
        if not megatron_root or not Path(megatron_root).is_dir():
            blockers.append("AEITRON_MEGATRON_ROOT must point to a validated Megatron-LM checkout")
        blockers.append("multi-node and 60B checkpoint/resume require a real scheduler allocation and promoted dataset")
    except Exception as exc:
        blockers.append(f"{type(exc).__name__}: {exc}")
    return [
        ProofResult(
            name="multi_node_fsdp_zero3_megatron_60b_resume",
            status="blocked" if blockers else "passed",
            started_at=started_at,
            duration_seconds=time.perf_counter() - started,
            evidence=evidence,
            blockers=blockers,
        )
    ]


def build_report(results: list[ProofResult]) -> ProductionProofReport:
    counts = {status: sum(item.status == status for item in results) for status in ("passed", "failed", "blocked")}
    status: ProofStatus = "failed" if counts["failed"] else "blocked" if counts["blocked"] else "passed"
    return ProductionProofReport(
        generated_at=now_iso(),
        environment={
            "hostname": os.environ.get("HOSTNAME") or os.environ.get("COMPUTERNAME") or "unknown",
            "python": os.sys.version,
            "git_commit": command_evidence(["git", "rev-parse", "HEAD"])["stdout"].strip(),
        },
        proofs=results,
        passed=counts["passed"],
        failed=counts["failed"],
        blocked=counts["blocked"],
        status=status,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run measured Aeitron training-workspace production proofs.")
    parser.add_argument("--database-url", default=os.environ.get("AEITRON_DATABASE_URL", "postgresql://aeitron:aeitron_proof_only_change_me@127.0.0.1:55432/aeitron_proof"))
    parser.add_argument("--redis-url", default=os.environ.get("AEITRON_REDIS_URL", "redis://127.0.0.1:56379/0"))
    parser.add_argument("--object-store-uri", default=os.environ.get("AEITRON_OBJECT_STORE_URI", "s3://aeitron-proof/training-workspace"))
    parser.add_argument("--object-store-endpoint-url", default=os.environ.get("AEITRON_OBJECT_STORE_ENDPOINT_URL", "http://127.0.0.1:59000"))
    parser.add_argument("--output-dir", default="artifacts/aeitron/production-proofs")
    parser.add_argument("--event-count", type=int, default=0, help="Set to 1000000 for the full ordered-event proof.")
    parser.add_argument("--soak-seconds", type=int, default=0, help="Set to 86400 for the required 24-hour proof.")
    parser.add_argument("--soak-interval-seconds", type=int, default=30)
    parser.add_argument("--skip-infrastructure", action="store_true")
    parser.add_argument("--docker-project", default="aeitron-proof")
    parser.add_argument("--skip-disaster-recovery", action="store_true")
    return parser.parse_args()


async def async_main() -> int:
    args = parse_args()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("AWS_ACCESS_KEY_ID", "aeitron-proof")
    os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "aeitron-proof-password-change-me")
    os.environ.setdefault("AWS_EC2_METADATA_DISABLED", "true")
    results: list[ProofResult] = []
    if not args.skip_infrastructure:
        results.append(
            await infrastructure_lifecycle_proof(
                database_url=args.database_url,
                redis_url=args.redis_url,
                object_store_uri=args.object_store_uri,
                object_store_endpoint_url=args.object_store_endpoint_url,
                output_dir=output_dir,
            )
        )
    if args.event_count:
        results.append(
            await million_event_proof(
                database_url=args.database_url,
                redis_url=args.redis_url,
                object_store_uri=args.object_store_uri,
                object_store_endpoint_url=args.object_store_endpoint_url,
                event_count=args.event_count,
            )
        )
    if args.soak_seconds:
        results.append(
            await soak_proof(
                database_url=args.database_url,
                redis_url=args.redis_url,
                object_store_uri=args.object_store_uri,
                object_store_endpoint_url=args.object_store_endpoint_url,
                duration_seconds=args.soak_seconds,
                interval_seconds=args.soak_interval_seconds,
            )
        )
    if not args.skip_infrastructure and not args.skip_disaster_recovery:
        results.append(
            await asyncio.to_thread(
                docker_disaster_recovery_proof,
                project_name=args.docker_project,
                redis_url=args.redis_url,
                object_store_uri=args.object_store_uri,
                object_store_endpoint_url=args.object_store_endpoint_url,
            )
        )
        results.append(
            await worker_loss_recovery_proof(
                database_url=args.database_url,
                redis_url=args.redis_url,
                object_store_uri=args.object_store_uri,
                object_store_endpoint_url=args.object_store_endpoint_url,
            )
        )
    results.extend(external_scheduler_proofs())
    results.extend(distributed_gpu_proofs())
    report = build_report(results)
    report_path = output_dir / "production_proof_report.json"
    report_path.write_text(json.dumps(report.model_dump(mode="json"), indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(report.model_dump(mode="json"), indent=2, sort_keys=True), flush=True)
    return 2 if report.failed else 0


def main() -> None:
    raise SystemExit(asyncio.run(async_main()))


if __name__ == "__main__":
    main()
