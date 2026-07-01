"""Consolidated Mythos Gateway API."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from src.phase34.auth_quota import auth_status, create_jwt, install_auth_quota
from src.mythos.db import LocalStore
from src.mythos.indexing import ContextBuilder, RepositoryIndexer
from src.mythos.model_ops.backends import active_model_health, list_model_profiles
from src.mythos.runtime.engine import MythosRuntime
from src.mythos.shared.schemas import MythosRunRequest, MythosRunReport

app = FastAPI(title="Mythos Consolidated Gateway", version="2.0.0")
AUTH_CONFIG = install_auth_quota(app)
STORE = LocalStore()


class AuthTokenRequest(BaseModel):
    user_id: str = Field(default="local-user", min_length=1)
    scopes: list[str] = Field(default_factory=lambda: ["api"])
    ttl_seconds: int = Field(default=3600, ge=60, le=86400)


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


@app.get("/health/ready")
async def health_ready() -> dict[str, object]:
    return {
        "status": "ready",
        "model_ops": active_model_health(),
        "auth": auth_status(AUTH_CONFIG),
        "database": {"ok": True, "engine": "sqlite-local", "path": str(STORE.path)},
    }


@app.get("/v1/auth/status")
async def auth_status_endpoint() -> dict[str, object]:
    return auth_status(AUTH_CONFIG)


@app.post("/v1/auth/token")
async def issue_auth_token(request: AuthTokenRequest) -> dict[str, object]:
    if not AUTH_CONFIG.jwt_secret:
        raise HTTPException(status_code=503, detail="PHASE34_JWT_SECRET is required")
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


@app.get("/v1/projects/{project_id}/index/status")
async def project_index_status(project_id: str) -> dict[str, Any]:
    try:
        return STORE.index_status(project_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="project not found") from exc


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


@app.post("/v1/agent/run", response_model=MythosRunReport)
async def run_agent(request: MythosRunRequest) -> MythosRunReport:
    return await MythosRuntime().run(request)
