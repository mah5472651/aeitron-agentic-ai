"""Consolidated Aeitron Gateway API."""

from __future__ import annotations

import os
import json
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import PlainTextResponse, StreamingResponse
from pydantic import BaseModel, Field

from src.aeitron.db import LocalStore
from src.aeitron.evaluation.benchmarks import BenchmarkHarness, built_in_security_tasks
from src.aeitron.identity import AuthError, auth_status, create_jwt, install_auth, install_quota, validate_token_issue_request
from src.aeitron.indexing import ContextBuilder, RepositoryIndexer, VectorBackendConfig, create_vector_index, vector_capabilities
from src.aeitron.learning.versioning import DatasetLedger
from src.aeitron.memory import MemoryIngestRequest, UnifiedMemoryManager
from src.aeitron.model_ops.backends import active_model_health, list_model_profiles
from src.aeitron.model_ops.foundation import PretrainingRunSpec, foundation_status
from src.aeitron.observability import METRICS, install_observability
from src.aeitron.patches import PatchPreviewRequest, PatchService, PatchVerifyRequest
from src.aeitron.runtime.engine import AeitronRuntime
from src.aeitron.runtime.taskgraph import AgentRunCreateRequest, TaskCompleteRequest, TaskFailRequest, TaskGraphRuntime
from src.aeitron.shared.schemas import AeitronRunRequest, AeitronRunReport
from src.aeitron.tools import DockerSandboxRunner, HardenedToolExecutor, SandboxRunRequest, ToolExecuteRequest
from src.aeitron.training_workspace import (
    ArtifactUploadRequest,
    CheckpointCommitRequest,
    EvaluationCommitRequest,
    JobStatus,
    ServiceAccount,
    TrainingArtifact,
    TrainingEventBatch,
    TrainingJob,
    TrainingJobCreateRequest,
    TrainingWorkspaceService,
    workspace_readiness,
)
from src.aeitron.verifier import VerificationRequest, VerifierRuntime

app = FastAPI(title="Aeitron Consolidated Gateway", version="2.0.0")
QUOTA_CONFIG = install_quota(app)
AUTH_CONFIG = install_auth(app)
install_observability(app)
STORE = LocalStore()
TRAINING_WORKSPACE = TrainingWorkspaceService.from_environment()


class AuthTokenRequest(BaseModel):
    user_id: str = Field(default="local-user", min_length=1)
    scopes: list[str] = Field(default_factory=lambda: ["api"])
    ttl_seconds: int = Field(default=3600, ge=60, le=86400)
    issue_key: str | None = Field(default=None, min_length=1)


class ProjectCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    repo_path: str = Field(min_length=1)
    default_branch: str = Field(default="main", min_length=1, max_length=100)


class IndexProjectRequest(BaseModel):
    force: bool = False
    include_suffixes: list[str] | None = None
    max_file_bytes: int = Field(default=1_000_000, ge=10_000, le=10_000_000)
    max_chunk_lines: int = Field(default=120, ge=20, le=400)


class ContextBuildRequest(BaseModel):
    project_id: str = Field(min_length=1)
    query: str = Field(min_length=1)
    token_budget: int = Field(default=24_000, ge=1_000, le=200_000)
    pinned_files: list[str] = Field(default_factory=list)
    max_chunks: int = Field(default=24, ge=1, le=100)


class VectorSearchRequest(BaseModel):
    project_id: str = Field(min_length=1)
    query: str = Field(min_length=1)
    top_k: int = Field(default=12, ge=1, le=100)
    backend: str = Field(default="local_hashing", pattern="^(local_hashing|faiss|hnsw|qdrant|pgvector)$")
    dims: int = Field(default=384, ge=64, le=4096)


class MemoryRetrieveRequest(BaseModel):
    project_id: str | None = None
    query: str = Field(min_length=1)
    limit: int = Field(default=5, ge=1, le=50)
    layers: list[str] = Field(default_factory=list)


class SessionCreateRequest(BaseModel):
    project_id: str = Field(min_length=1)
    title: str = Field(default="Aeitron Session", min_length=1, max_length=200)


class TrainingServiceAccountRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    scopes: list[str] = Field(min_length=1, max_length=16)


class TrainingTokenExchangeRequest(BaseModel):
    bootstrap_token: str = Field(min_length=32, max_length=512)


class TrainingTokenRefreshRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=128)
    refresh_token: str = Field(min_length=32, max_length=512)


class TrainingArtifactRegistrationRequest(BaseModel):
    upload: ArtifactUploadRequest
    uri: str = Field(min_length=1, max_length=4096)


def require_scope(request: Request, scope: str) -> None:
    if not AUTH_CONFIG.enabled:
        return
    claims = getattr(request.state, "jwt_claims", {}) or {}
    scopes = set(str(item) for item in claims.get("scopes", []) if item)
    if scope.startswith("training:"):
        allowed = scope in scopes or "training:admin" in scopes
    else:
        allowed = scope in scopes or "api" in scopes
    if not allowed:
        raise PermissionError(f"missing required scope: {scope}")


def request_scopes(request: Request) -> set[str]:
    claims = getattr(request.state, "jwt_claims", {}) or {}
    return {str(item) for item in claims.get("scopes", []) if item}


def request_owner(request: Request) -> str:
    return str(getattr(request.state, "user_id", "") or "local-user")


async def require_training_job_access(request: Request, job_id: str, scope: str) -> TrainingJob:
    require_scope(request, scope)
    job = await TRAINING_WORKSPACE.get_job(job_id)
    claims = getattr(request.state, "jwt_claims", {}) or {}
    job_bound = str(claims.get("job_id") or "") == job_id
    if AUTH_CONFIG.enabled and "training:admin" not in request_scopes(request) and not job_bound and job.owner_id != request_owner(request):
        raise PermissionError("training job belongs to a different workspace identity")
    return job


@app.get("/health/ready")
async def health_ready() -> dict[str, object]:
    training = workspace_readiness(TRAINING_WORKSPACE)
    return {
        "status": "ready",
        "model_ops": active_model_health(),
        "auth": auth_status(AUTH_CONFIG),
        "quota": {"enabled": QUOTA_CONFIG.enabled, "capacity": QUOTA_CONFIG.capacity},
        "database": {"ok": True, "engine": "sqlite-local", "path": str(STORE.path)},
        "training_workspace": training,
    }


@app.get("/metrics", response_class=PlainTextResponse)
async def metrics() -> str:
    return METRICS.render_prometheus()


@app.get("/v1/auth/status")
async def auth_status_endpoint() -> dict[str, object]:
    return auth_status(AUTH_CONFIG)


@app.post("/v1/auth/token")
async def issue_auth_token(request: AuthTokenRequest) -> dict[str, object]:
    if not AUTH_CONFIG.jwt_secret:
        raise HTTPException(status_code=503, detail="AEITRON_JWT_SECRET is required")
    try:
        validate_token_issue_request(AUTH_CONFIG, request.issue_key)
    except AuthError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    token = create_jwt(
        subject=request.user_id,
        secret=AUTH_CONFIG.jwt_secret,
        issuer=AUTH_CONFIG.issuer,
        audience=AUTH_CONFIG.audience,
        scopes=request.scopes,
        ttl_seconds=request.ttl_seconds,
    )
    return {
        "token_type": "Bearer",  # nosec B105 - OAuth token type label, not a credential.
        "access_token": token,
        "expires_in": request.ttl_seconds,
        "user_id": request.user_id,
    }


def training_access_token(account: ServiceAccount) -> str:
    if not AUTH_CONFIG.jwt_secret:
        raise HTTPException(status_code=503, detail="AEITRON_JWT_SECRET is required for workspace tokens")
    return create_jwt(
        subject=account.service_account_id,
        secret=AUTH_CONFIG.jwt_secret,
        issuer=AUTH_CONFIG.issuer,
        audience=AUTH_CONFIG.audience,
        scopes=account.scopes,
        ttl_seconds=900,
    )


@app.get("/v1/training/readiness")
async def training_workspace_readiness() -> dict[str, Any]:
    return workspace_readiness(TRAINING_WORKSPACE)


@app.post("/v1/training/service-accounts")
async def create_training_service_account(request: TrainingServiceAccountRequest, http_request: Request) -> dict[str, Any]:
    try:
        require_scope(http_request, "training:admin")
        credential = await TRAINING_WORKSPACE.create_service_account(name=request.name, scopes=request.scopes)
        return credential.model_dump(mode="json")
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/v1/training/token/exchange")
async def exchange_training_token(request: TrainingTokenExchangeRequest) -> dict[str, Any]:
    account = await TRAINING_WORKSPACE.authenticate_bootstrap(request.bootstrap_token)
    if account is None:
        raise HTTPException(status_code=401, detail="invalid workspace bootstrap credential")
    session = await TRAINING_WORKSPACE.create_refresh_session(account)
    return {
        "token_type": "Bearer",  # nosec B105 - OAuth token type label, not a credential.
        "access_token": training_access_token(account),
        "expires_in": 900,
        "session_id": session.session_id,
        "refresh_token": session.refresh_token,
        "refresh_expires_at": session.expires_at.isoformat(),
        "service_account_id": account.service_account_id,
        "scopes": account.scopes,
    }


@app.post("/v1/training/token/refresh")
async def refresh_training_token(request: TrainingTokenRefreshRequest) -> dict[str, Any]:
    account = await TRAINING_WORKSPACE.authenticate_refresh(request.session_id, request.refresh_token)
    if account is None:
        raise HTTPException(status_code=401, detail="refresh session is invalid, expired, or revoked")
    return {
        "token_type": "Bearer",  # nosec B105 - OAuth token type label, not a credential.
        "access_token": training_access_token(account),
        "expires_in": 900,
        "service_account_id": account.service_account_id,
        "scopes": account.scopes,
    }


@app.post("/v1/training/token/revoke")
async def revoke_training_token(request: TrainingTokenRefreshRequest, http_request: Request) -> dict[str, Any]:
    try:
        require_scope(http_request, "training:jobs:read")
        revoked = await TRAINING_WORKSPACE.revoke_refresh(request.session_id, request.refresh_token)
        if not revoked:
            raise HTTPException(status_code=404, detail="refresh session was not found")
        return {"revoked": True}
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@app.get("/v1/training/profiles")
async def training_profiles(http_request: Request) -> dict[str, Any]:
    try:
        require_scope(http_request, "training:jobs:read")
        return {"profiles": TRAINING_WORKSPACE.profile_report()}
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@app.post("/v1/training/jobs")
async def create_training_job(request: TrainingJobCreateRequest, http_request: Request) -> dict[str, Any]:
    try:
        require_scope(http_request, "training:jobs:create")
        job = await TRAINING_WORKSPACE.create_job(request, owner_id=request_owner(http_request))
        METRICS.inc("aeitron_training_jobs_total", profile=job.spec.profile_id, status=job.status.value)
        return job.model_dump(mode="json")
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/v1/training/jobs")
async def list_training_jobs(http_request: Request, limit: int = 100) -> dict[str, Any]:
    try:
        require_scope(http_request, "training:jobs:read")
        owner = None if "training:admin" in request_scopes(http_request) else request_owner(http_request)
        jobs = await TRAINING_WORKSPACE.list_jobs(owner_id=owner, limit=limit)
        return {"jobs": [job.model_dump(mode="json") for job in jobs]}
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@app.get("/v1/training/jobs/{job_id}")
async def get_training_job(job_id: str, http_request: Request) -> dict[str, Any]:
    try:
        job = await require_training_job_access(http_request, job_id, "training:jobs:read")
        return job.model_dump(mode="json")
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/v1/training/jobs/{job_id}/cancel")
async def cancel_training_job(job_id: str, http_request: Request) -> dict[str, Any]:
    try:
        await require_training_job_access(http_request, job_id, "training:jobs:cancel")
        job = await TRAINING_WORKSPACE.cancel_job(job_id, actor_id=request_owner(http_request))
        METRICS.inc("aeitron_training_job_transitions_total", target="cancelled")
        return job.model_dump(mode="json")
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/v1/training/jobs/{job_id}/resume")
async def resume_training_job(job_id: str, http_request: Request) -> dict[str, Any]:
    try:
        await require_training_job_access(http_request, job_id, "training:jobs:create")
        job = await TRAINING_WORKSPACE.resume_job(job_id, actor_id=request_owner(http_request))
        METRICS.inc("aeitron_training_job_transitions_total", target="queued")
        return job.model_dump(mode="json")
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/v1/training/jobs/{job_id}/worker-token")
async def issue_training_worker_token(job_id: str, http_request: Request, ttl_seconds: int = 21_600) -> dict[str, Any]:
    try:
        job = await require_training_job_access(http_request, job_id, "training:jobs:create")
        if not AUTH_CONFIG.jwt_secret:
            raise HTTPException(status_code=503, detail="AEITRON_JWT_SECRET is required for worker tokens")
        ttl = min(max(ttl_seconds, 900), 86_400)
        token = create_jwt(
            subject=f"worker:{job.job_id}",
            secret=AUTH_CONFIG.jwt_secret,
            issuer=AUTH_CONFIG.issuer,
            audience=AUTH_CONFIG.audience,
            scopes=["training:events:write", "training:artifacts:write", "training:jobs:read"],
            ttl_seconds=ttl,
            extra_claims={"job_id": job.job_id, "token_class": "training_worker"},
        )
        return {"token_type": "Bearer", "access_token": token, "expires_in": ttl, "job_id": job.job_id}
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/v1/training/jobs/{job_id}/claim")
async def claim_notebook_training_job(job_id: str, http_request: Request) -> dict[str, Any]:
    try:
        await require_training_job_access(http_request, job_id, "training:jobs:create")
        job, attempt = await TRAINING_WORKSPACE.claim_notebook_job(job_id)
        if not AUTH_CONFIG.jwt_secret:
            raise HTTPException(status_code=503, detail="AEITRON_JWT_SECRET is required for notebook worker tokens")
        token = create_jwt(
            subject=f"worker:{job.job_id}",
            secret=AUTH_CONFIG.jwt_secret,
            issuer=AUTH_CONFIG.issuer,
            audience=AUTH_CONFIG.audience,
            scopes=["training:events:write", "training:artifacts:write", "training:jobs:read"],
            ttl_seconds=21_600,
            extra_claims={"job_id": job.job_id, "token_class": "notebook_worker"},
        )
        return {
            "job": job.model_dump(mode="json"),
            "attempt": attempt.model_dump(mode="json"),
            "worker_access_token": token,
            "worker_token_expires_in": 21_600,
        }
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/v1/training/jobs/{job_id}/events:batch")
async def ingest_training_events(job_id: str, request: TrainingEventBatch, http_request: Request) -> dict[str, Any]:
    try:
        await require_training_job_access(http_request, job_id, "training:events:write")
        events = await TRAINING_WORKSPACE.ingest_events(job_id, request)
        METRICS.inc("aeitron_training_events_total", value=float(len(events)), kind="batch")
        if not events:
            METRICS.inc("aeitron_training_event_duplicates_total", value=float(len(request.events)))
            return {"accepted": 0, "duplicates": len(request.events), "first_sequence": None, "last_sequence": None, "last_event_id": None}
        profile_id = (await TRAINING_WORKSPACE.get_job(job_id)).spec.profile_id
        for event in events:
            if event.loss is not None:
                METRICS.observe("aeitron_training_loss", event.loss, profile=profile_id)
            if event.validation_loss is not None:
                METRICS.observe("aeitron_training_validation_loss", event.validation_loss)
            if event.tokens_per_second is not None:
                METRICS.observe("aeitron_training_tokens_per_second", event.tokens_per_second)
        return {
            "accepted": len(events),
            "duplicates": len(request.events) - len(events),
            "first_sequence": events[0].sequence,
            "last_sequence": events[-1].sequence,
            "last_event_id": events[-1].event_id,
        }
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/v1/training/jobs/{job_id}/audit")
async def list_training_audit(job_id: str, http_request: Request, limit: int = 100) -> dict[str, Any]:
    try:
        await require_training_job_access(http_request, job_id, "training:jobs:read")
        events = await TRAINING_WORKSPACE.list_audit_events(job_id, limit=limit)
        return {"audit_events": [event.model_dump(mode="json") for event in events]}
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/v1/training/jobs/{job_id}/events")
async def stream_training_events(job_id: str, http_request: Request, after_sequence: int = 0) -> StreamingResponse:
    try:
        await require_training_job_access(http_request, job_id, "training:jobs:read")
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    header_cursor = http_request.headers.get("last-event-id", "").strip()
    if header_cursor:
        try:
            after_sequence = max(after_sequence, int(header_cursor))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Last-Event-ID must be an integer sequence") from exc

    async def generate() -> Any:
        async for event in TRAINING_WORKSPACE.stream_events(job_id, after_sequence=after_sequence):
            if await http_request.is_disconnected():
                return
            if event is None:
                yield ": heartbeat\n\n"
                continue
            payload = json.dumps(event.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
            yield f"id: {event.sequence}\nevent: {event.kind}\ndata: {payload}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
    )


@app.get("/v1/training/jobs/{job_id}/artifacts")
async def list_training_artifacts(job_id: str, http_request: Request) -> dict[str, Any]:
    try:
        await require_training_job_access(http_request, job_id, "training:artifacts:read")
        artifacts = await TRAINING_WORKSPACE.list_artifacts(job_id)
        return {"artifacts": [artifact.model_dump(mode="json") for artifact in artifacts]}
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/v1/training/jobs/{job_id}/artifacts/presign")
async def presign_training_artifact(job_id: str, request: ArtifactUploadRequest, http_request: Request) -> dict[str, Any]:
    try:
        await require_training_job_access(http_request, job_id, "training:artifacts:write")
        return await TRAINING_WORKSPACE.presign_artifact_upload(job_id, request)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.post("/v1/training/jobs/{job_id}/artifacts/register")
async def register_training_artifact(
    job_id: str,
    request: TrainingArtifactRegistrationRequest,
    http_request: Request,
) -> dict[str, Any]:
    try:
        await require_training_job_access(http_request, job_id, "training:artifacts:write")
        artifact = await TRAINING_WORKSPACE.verify_and_register_artifact(job_id, request.upload, uri=request.uri)
        return artifact.model_dump(mode="json")
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.post("/v1/training/jobs/{job_id}/checkpoints")
async def commit_training_checkpoint(job_id: str, request: CheckpointCommitRequest, http_request: Request) -> dict[str, Any]:
    try:
        await require_training_job_access(http_request, job_id, "training:artifacts:write")
        checkpoint = await TRAINING_WORKSPACE.commit_checkpoint(job_id, request)
        METRICS.inc("aeitron_training_checkpoints_total", status="committed")
        return checkpoint.model_dump(mode="json")
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.get("/v1/training/jobs/{job_id}/checkpoints")
async def list_training_checkpoints(job_id: str, http_request: Request) -> dict[str, Any]:
    try:
        await require_training_job_access(http_request, job_id, "training:artifacts:read")
        rows = await TRAINING_WORKSPACE.list_checkpoints(job_id)
        return {"checkpoints": [item.model_dump(mode="json") for item in rows]}
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/v1/training/jobs/{job_id}/evaluations")
async def commit_training_evaluation(job_id: str, request: EvaluationCommitRequest, http_request: Request) -> dict[str, Any]:
    try:
        await require_training_job_access(http_request, job_id, "training:artifacts:write")
        evaluation = await TRAINING_WORKSPACE.commit_evaluation(job_id, request)
        METRICS.inc("aeitron_training_evaluations_total", decision=evaluation.decision)
        return evaluation.model_dump(mode="json")
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.get("/v1/training/jobs/{job_id}/evaluations")
async def list_training_evaluations(job_id: str, http_request: Request) -> dict[str, Any]:
    try:
        await require_training_job_access(http_request, job_id, "training:artifacts:read")
        rows = await TRAINING_WORKSPACE.list_evaluations(job_id)
        return {"evaluations": [item.model_dump(mode="json") for item in rows]}
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/v1/training/jobs/{job_id}/checkpoints/{checkpoint_id}/promote")
async def promote_training_checkpoint(job_id: str, checkpoint_id: str, http_request: Request) -> dict[str, Any]:
    try:
        await require_training_job_access(http_request, job_id, "training:admin")
        checkpoint = await TRAINING_WORKSPACE.promote_checkpoint(
            job_id,
            checkpoint_id,
            actor_id=request_owner(http_request),
        )
        METRICS.inc("aeitron_training_checkpoints_total", status="promoted")
        return checkpoint.model_dump(mode="json")
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.get("/v1/model/profiles")
async def model_profiles() -> dict[str, object]:
    return {"profiles": list_model_profiles()}


@app.post("/v1/evaluation/security-static")
async def run_security_static_benchmark() -> dict[str, object]:
    return BenchmarkHarness().run_static(built_in_security_tasks()).model_dump()


@app.get("/v1/model/foundation/status")
async def model_foundation_status() -> dict[str, object]:
    return foundation_status()


@app.get("/v1/data/platform/status")
async def data_platform_status() -> dict[str, object]:
    root = Path(os.environ.get("AEITRON_DATA_PIPELINE_DIR", "artifacts/aeitron/data-pipeline"))
    latest = DatasetLedger(root / "versions" / "ledger.jsonl").latest()
    dashboard = root / "dashboard.html"
    return {
        "status": "ready" if latest else "no_dataset_versions",
        "pipeline_dir": str(root),
        "latest_version": latest.model_dump() if latest else None,
        "dashboard_path": str(dashboard) if dashboard.exists() else None,
    }


@app.post("/v1/model/foundation/pretraining/readiness")
async def model_pretraining_readiness(request: PretrainingRunSpec) -> dict[str, object]:
    return request.readiness_report()


@app.post("/v1/projects")
async def create_project(request: ProjectCreateRequest) -> dict[str, Any]:
    repo_path = Path(request.repo_path).expanduser().resolve()
    if not repo_path.exists() or not repo_path.is_dir():
        raise HTTPException(status_code=400, detail=f"repo_path is not a directory: {repo_path}")
    return STORE.create_project(
        name=request.name,
        repo_path=str(repo_path),
        default_branch=request.default_branch,
    )


@app.get("/v1/projects/{project_id}")
async def get_project(project_id: str) -> dict[str, Any]:
    project = STORE.get_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    return project


@app.post("/v1/sessions")
async def create_session(request: SessionCreateRequest) -> dict[str, Any]:
    try:
        return STORE.create_session(project_id=request.project_id, title=request.title)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="project not found") from exc


@app.post("/v1/projects/{project_id}/index")
async def index_project(project_id: str, request: IndexProjectRequest) -> dict[str, Any]:
    project = STORE.get_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    if project["index_status"] == "ready" and not request.force:
        return STORE.index_status(project_id)
    suffixes = set(request.include_suffixes) if request.include_suffixes else None
    try:
        report = RepositoryIndexer(STORE).index_project(
            project_id=project_id,
            include_suffixes=suffixes,
            max_file_bytes=request.max_file_bytes,
            max_chunk_lines=request.max_chunk_lines,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return report.model_dump()


@app.post("/v1/agent/runs")
async def create_agent_run(request: AgentRunCreateRequest) -> dict[str, Any]:
    try:
        return TaskGraphRuntime(STORE).create_agent_run(request).model_dump()
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="project or session not found") from exc


@app.get("/v1/agent/runs/{run_id}")
async def get_agent_run(run_id: str) -> dict[str, Any]:
    run = STORE.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    return run


@app.get("/v1/taskgraphs/{task_graph_id}")
async def get_task_graph(task_graph_id: str) -> dict[str, Any]:
    graph = STORE.get_task_graph(task_graph_id)
    if graph is None:
        raise HTTPException(status_code=404, detail="task graph not found")
    return graph


@app.post("/v1/taskgraphs/{task_graph_id}/advance")
async def advance_task_graph(task_graph_id: str) -> dict[str, Any]:
    try:
        return TaskGraphRuntime(STORE).advance(task_graph_id).model_dump()
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="task graph not found") from exc


@app.post("/v1/tasks/{task_id}/complete")
async def complete_task(task_id: str, request: TaskCompleteRequest) -> dict[str, Any]:
    try:
        return TaskGraphRuntime(STORE).complete_task(task_id, request).model_dump()
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="task not found") from exc


@app.post("/v1/tasks/{task_id}/fail")
async def fail_task(task_id: str, request: TaskFailRequest) -> dict[str, Any]:
    try:
        return TaskGraphRuntime(STORE).fail_task(task_id, request).model_dump()
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="task not found") from exc


@app.get("/v1/projects/{project_id}/index/status")
async def project_index_status(project_id: str) -> dict[str, Any]:
    try:
        return STORE.index_status(project_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="project not found") from exc


@app.get("/v1/projects/{project_id}/symbols")
async def project_symbols(project_id: str) -> dict[str, Any]:
    project = STORE.get_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    symbols: list[dict[str, Any]] = []
    dependencies: dict[str, set[str]] = {}
    for chunk in STORE.list_chunks(project_id):
        metadata = chunk.get("metadata") or {}
        path = str(chunk.get("path") or "")
        dependency_values = metadata.get("dependencies", [])
        if not isinstance(dependency_values, list):
            dependency_values = [dependency_values] if dependency_values else []
        if path:
            dependencies.setdefault(path, set()).update(str(item) for item in dependency_values if item)
        if not chunk.get("symbol_name"):
            continue
        symbols.append(
            {
                "path": path,
                "language": chunk.get("language"),
                "symbol_name": chunk["symbol_name"],
                "kind": chunk["kind"],
                "start_line": chunk["start_line"],
                "end_line": chunk["end_line"],
                "signature": metadata.get("signature", ""),
                "calls": metadata.get("calls", []),
                "state_mutations": metadata.get("state_mutations", []),
            }
        )
    return {
        "project_id": project_id,
        "symbol_count": len(symbols),
        "symbols": symbols,
        "dependencies": {path: sorted(values) for path, values in sorted(dependencies.items())},
    }


@app.post("/v1/context/build")
async def build_context(request: ContextBuildRequest) -> dict[str, Any]:
    try:
        report = ContextBuilder(STORE).build(
            project_id=request.project_id,
            query=request.query,
            token_budget=request.token_budget,
            pinned_files=request.pinned_files,
            max_chunks=request.max_chunks,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="project not found") from exc
    return report.model_dump()


@app.post("/v1/context/vector-search")
async def vector_search(request: VectorSearchRequest) -> dict[str, Any]:
    if STORE.get_project(request.project_id) is None:
        raise HTTPException(status_code=404, detail="project not found")
    try:
        index = create_vector_index(STORE, VectorBackendConfig(backend=request.backend, dims=request.dims))  # type: ignore[arg-type]
        return index.search(project_id=request.project_id, query=request.query, top_k=request.top_k).model_dump()
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/v1/context/vector-capabilities")
async def get_vector_capabilities() -> dict[str, Any]:
    return {"capabilities": [item.model_dump() for item in vector_capabilities()]}


@app.post("/v1/memory/ingest")
async def ingest_memory(request: MemoryIngestRequest, project_id: str | None = None) -> dict[str, Any]:
    if project_id is not None and STORE.get_project(project_id) is None:
        raise HTTPException(status_code=404, detail="project not found")
    try:
        return UnifiedMemoryManager(project_id=project_id, store=STORE).ingest(request).model_dump()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/v1/memory/retrieve")
async def retrieve_memory(request: MemoryRetrieveRequest) -> dict[str, Any]:
    if request.project_id is not None and STORE.get_project(request.project_id) is None:
        raise HTTPException(status_code=404, detail="project not found")
    try:
        return UnifiedMemoryManager(project_id=request.project_id, store=STORE).retrieve_report(
            request.query,
            limit=request.limit,
            layers=request.layers or None,  # type: ignore[arg-type]
        ).model_dump()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/v1/tools/execute")
async def execute_tool(request: ToolExecuteRequest, http_request: Request) -> dict[str, Any]:
    try:
        require_scope(http_request, "tools:execute")
        return HardenedToolExecutor(STORE).execute(request).model_dump()
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="project not found") from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/v1/sandbox/run")
async def run_sandbox(request: SandboxRunRequest) -> dict[str, Any]:
    return DockerSandboxRunner().run(request).model_dump()


@app.post("/v1/patches/preview")
async def preview_patch(request: PatchPreviewRequest) -> dict[str, Any]:
    try:
        return PatchService(STORE).preview(request).model_dump()
    except (KeyError, FileNotFoundError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/v1/patches/verify")
async def verify_patch_loop(request: PatchVerifyRequest) -> dict[str, Any]:
    try:
        return PatchService(STORE).preview_apply_verify(request).model_dump()
    except (KeyError, FileNotFoundError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/v1/patches/{patch_id}/apply")
async def apply_patch(patch_id: str) -> dict[str, Any]:
    try:
        return PatchService(STORE).apply(patch_id).model_dump()
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/v1/patches/{patch_id}/rollback")
async def rollback_patch(patch_id: str) -> dict[str, Any]:
    try:
        return PatchService(STORE).rollback(patch_id).model_dump()
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/v1/verifier/run")
async def run_verifier(request: VerificationRequest) -> dict[str, Any]:
    try:
        return VerifierRuntime(STORE).run(request).model_dump()
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="project not found") from exc


@app.post("/v1/agent/run", response_model=AeitronRunReport)
async def run_agent(request: AeitronRunRequest) -> AeitronRunReport:
    return await AeitronRuntime().run(request)

