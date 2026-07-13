"""Consolidated Mythos Gateway API."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from src.mythos.db import LocalStore
from src.mythos.evaluation.benchmarks import BenchmarkHarness, built_in_security_tasks
from src.mythos.identity import AuthError, auth_status, create_jwt, install_auth, install_quota, validate_token_issue_request
from src.mythos.indexing import ContextBuilder, RepositoryIndexer, VectorBackendConfig, create_vector_index, vector_capabilities
from src.mythos.learning.versioning import DatasetLedger
from src.mythos.memory import MemoryIngestRequest, UnifiedMemoryManager
from src.mythos.model_ops.backends import active_model_health, list_model_profiles
from src.mythos.model_ops.foundation import PretrainingRunSpec, foundation_status
from src.mythos.observability import METRICS, install_observability
from src.mythos.patches import PatchPreviewRequest, PatchService, PatchVerifyRequest
from src.mythos.runtime.engine import MythosRuntime
from src.mythos.runtime.taskgraph import AgentRunCreateRequest, TaskCompleteRequest, TaskFailRequest, TaskGraphRuntime
from src.mythos.shared.schemas import MythosRunRequest, MythosRunReport
from src.mythos.tools import DockerSandboxRunner, SandboxRunRequest, ToolExecuteRequest, ToolRuntime
from src.mythos.verifier import VerificationRequest, VerifierRuntime

app = FastAPI(title="Aeitron Consolidated Gateway", version="2.0.0")
QUOTA_CONFIG = install_quota(app)
AUTH_CONFIG = install_auth(app)
install_observability(app)
STORE = LocalStore()


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


@app.get("/health/ready")
async def health_ready() -> dict[str, object]:
    return {
        "status": "ready",
        "model_ops": active_model_health(),
        "auth": auth_status(AUTH_CONFIG),
        "quota": {"enabled": QUOTA_CONFIG.enabled, "capacity": QUOTA_CONFIG.capacity},
        "database": {"ok": True, "engine": "sqlite-local", "path": str(STORE.path)},
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
        raise HTTPException(status_code=503, detail="MYTHOS_JWT_SECRET is required")
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
    root = Path(os.environ.get("MYTHOS_DATA_PIPELINE_DIR", "artifacts/aeitron/data-pipeline"))
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
async def execute_tool(request: ToolExecuteRequest) -> dict[str, Any]:
    try:
        return ToolRuntime(STORE).execute(request).model_dump()
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="project not found") from exc
    except FileNotFoundError as exc:
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


@app.post("/v1/agent/run", response_model=MythosRunReport)
async def run_agent(request: MythosRunRequest) -> MythosRunReport:
    return await MythosRuntime().run(request)
