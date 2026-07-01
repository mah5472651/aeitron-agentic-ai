"""Consolidated Mythos Gateway API."""

from __future__ import annotations

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from src.phase34.auth_quota import auth_status, create_jwt, install_auth_quota
from src.mythos.model_ops.backends import active_model_health, list_model_profiles
from src.mythos.runtime.engine import MythosRuntime
from src.mythos.shared.schemas import MythosRunRequest, MythosRunReport

app = FastAPI(title="Mythos Consolidated Gateway", version="2.0.0")
AUTH_CONFIG = install_auth_quota(app)


class AuthTokenRequest(BaseModel):
    user_id: str = Field(default="local-user", min_length=1)
    scopes: list[str] = Field(default_factory=lambda: ["api"])
    ttl_seconds: int = Field(default=3600, ge=60, le=86400)


@app.get("/health/ready")
async def health_ready() -> dict[str, object]:
    return {
        "status": "ready",
        "model_ops": active_model_health(),
        "auth": auth_status(AUTH_CONFIG),
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


@app.post("/v1/agent/run", response_model=MythosRunReport)
async def run_agent(request: MythosRunRequest) -> MythosRunReport:
    return await MythosRuntime().run(request)
