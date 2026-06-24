#!/usr/bin/env python
"""JWT authentication middleware connected to the Phase 6 Redis quota engine."""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from starlette.middleware.base import BaseHTTPMiddleware

from src.phase6.redis_quota_engine import (
    QuotaBackendError,
    QuotaDenied,
    QuotaPolicy,
    RedisRegenerativeQuotaEngine,
    RequestCostEstimator,
    enforce_quota,
    quota_headers,
)


class AuthError(Exception):
    def __init__(self, code: str, detail: str, status_code: int = 401) -> None:
        self.code = code
        self.detail = detail
        self.status_code = status_code
        super().__init__(detail)


@dataclass(frozen=True)
class AuthConfig:
    enabled: bool = False
    quota_enabled: bool = False
    jwt_secret: str | None = None
    issuer: str = "mythos-local"
    audience: str = "mythos-api"
    leeway_seconds: int = 30
    redis_url: str = "redis://127.0.0.1:6379/0"
    quota_capacity: float = 100.0
    quota_refill_rate: float = 1.0
    quota_fail_open: bool = True
    protected_prefixes: tuple[str, ...] = ("/v1",)
    exempt_paths: tuple[str, ...] = (
        "/",
        "/metrics",
        "/openapi.json",
        "/docs",
        "/redoc",
        "/brand/logo",
        "/health/live",
        "/health/ready",
        "/v1/auth/status",
        "/v1/auth/token",
    )

    @classmethod
    def from_env(cls) -> "AuthConfig":
        return cls(
            enabled=os.environ.get("PHASE34_AUTH_ENABLED", "0") == "1",
            quota_enabled=os.environ.get("PHASE34_QUOTA_ENABLED", "0") == "1",
            jwt_secret=os.environ.get("PHASE34_JWT_SECRET"),
            issuer=os.environ.get("PHASE34_JWT_ISSUER", "mythos-local"),
            audience=os.environ.get("PHASE34_JWT_AUDIENCE", "mythos-api"),
            redis_url=os.environ.get("PHASE34_REDIS_URL", os.environ.get("PHASE11_REDIS_URL", "redis://127.0.0.1:6379/0")),
            quota_capacity=float(os.environ.get("PHASE34_QUOTA_CAPACITY", "100")),
            quota_refill_rate=float(os.environ.get("PHASE34_QUOTA_REFILL_RATE", "1")),
            quota_fail_open=os.environ.get("PHASE34_QUOTA_FAIL_OPEN", "1") == "1",
        )


def b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def b64url_decode(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode((text + padding).encode("ascii"))


def json_b64(data: dict[str, Any]) -> str:
    return b64url_encode(json.dumps(data, separators=(",", ":"), sort_keys=True).encode("utf-8"))


def sign_hs256(message: str, secret: str) -> str:
    digest = hmac.new(secret.encode("utf-8"), message.encode("ascii"), hashlib.sha256).digest()
    return b64url_encode(digest)


def create_jwt(
    *,
    subject: str,
    secret: str,
    issuer: str,
    audience: str,
    scopes: list[str] | None = None,
    ttl_seconds: int = 3600,
    now: int | None = None,
) -> str:
    issued_at = int(time.time()) if now is None else now
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {
        "sub": subject,
        "iss": issuer,
        "aud": audience,
        "iat": issued_at,
        "nbf": issued_at,
        "exp": issued_at + ttl_seconds,
        "scopes": scopes or ["api"],
    }
    signing_input = f"{json_b64(header)}.{json_b64(payload)}"
    return f"{signing_input}.{sign_hs256(signing_input, secret)}"


def verify_jwt(token: str, *, secret: str, issuer: str, audience: str, leeway_seconds: int = 30) -> dict[str, Any]:
    parts = token.split(".")
    if len(parts) != 3:
        raise AuthError("invalid_token", "JWT must have three segments.")
    header_raw, payload_raw, signature = parts
    signing_input = f"{header_raw}.{payload_raw}"
    expected = sign_hs256(signing_input, secret)
    if not hmac.compare_digest(signature, expected):
        raise AuthError("invalid_signature", "JWT signature verification failed.")
    try:
        header = json.loads(b64url_decode(header_raw))
        payload = json.loads(b64url_decode(payload_raw))
    except (json.JSONDecodeError, ValueError) as exc:
        raise AuthError("invalid_payload", f"JWT payload is invalid: {exc}") from exc
    if header.get("alg") != "HS256":
        raise AuthError("unsupported_alg", "Only HS256 JWTs are accepted.")
    now = int(time.time())
    if payload.get("iss") != issuer:
        raise AuthError("invalid_issuer", "JWT issuer mismatch.")
    if payload.get("aud") != audience:
        raise AuthError("invalid_audience", "JWT audience mismatch.")
    if int(payload.get("nbf", 0)) > now + leeway_seconds:
        raise AuthError("not_yet_valid", "JWT is not active yet.")
    if int(payload.get("exp", 0)) < now - leeway_seconds:
        raise AuthError("expired_token", "JWT has expired.")
    if not payload.get("sub"):
        raise AuthError("missing_subject", "JWT subject is required.")
    return payload


def is_exempt(path: str, config: AuthConfig) -> bool:
    if path in config.exempt_paths:
        return True
    return path.startswith("/static/")


def is_protected(path: str, config: AuthConfig) -> bool:
    return any(path.startswith(prefix) for prefix in config.protected_prefixes)


class AuthQuotaMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: FastAPI, config: AuthConfig) -> None:
        super().__init__(app)
        self.config = config
        self.estimator = RequestCostEstimator()
        self._quota_engine: RedisRegenerativeQuotaEngine | None = None

    async def _engine(self) -> RedisRegenerativeQuotaEngine:
        if self._quota_engine is None:
            import redis.asyncio as redis

            client = redis.from_url(self.config.redis_url, decode_responses=True)
            policy = QuotaPolicy(
                capacity=self.config.quota_capacity,
                refill_rate=self.config.quota_refill_rate,
                initialize_full=True,
                tenant="phase34",
            )
            self._quota_engine = RedisRegenerativeQuotaEngine(redis_client=client, policy=policy)
            await self._quota_engine.initialize()
        return self._quota_engine

    async def dispatch(self, request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        path = request.url.path
        if is_exempt(path, self.config) or not is_protected(path, self.config):
            response = await call_next(request)
            response.headers["X-Auth-Mode"] = "exempt"
            return response
        if not self.config.enabled:
            response = await call_next(request)
            response.headers["X-Auth-Mode"] = "disabled"
            return response
        if not self.config.jwt_secret:
            return JSONResponse(status_code=503, content={"error": "auth_secret_missing", "detail": "PHASE34_JWT_SECRET is required when auth is enabled."})

        auth_header = request.headers.get("authorization", "")
        if not auth_header.lower().startswith("bearer "):
            return JSONResponse(status_code=401, content={"error": "missing_bearer_token"})
        try:
            claims = verify_jwt(
                auth_header.split(" ", 1)[1].strip(),
                secret=self.config.jwt_secret,
                issuer=self.config.issuer,
                audience=self.config.audience,
                leeway_seconds=self.config.leeway_seconds,
            )
        except AuthError as exc:
            return JSONResponse(status_code=exc.status_code, content={"error": exc.code, "detail": exc.detail})

        request.state.user_id = str(claims["sub"])
        request.state.jwt_claims = claims
        quota_decision = None
        body = await request.body()
        if self.config.quota_enabled:
            try:
                quota_decision = await enforce_quota(
                    await self._engine(),
                    user_id=str(claims["sub"]),
                    cost=self.estimator.estimate(body, request.headers),
                    fail_open=self.config.quota_fail_open,
                )
            except QuotaDenied as exc:
                return JSONResponse(
                    status_code=429,
                    headers=quota_headers(exc.decision),
                    content={"error": "quota_denied", "remaining_balance": exc.decision.remaining_balance, "retry_after_seconds": exc.decision.retry_after_seconds},
                )
            except (QuotaBackendError, ValueError) as exc:
                return JSONResponse(status_code=503, content={"error": "quota_backend_error", "detail": str(exc)})

        async def receive() -> dict[str, Any]:
            return {"type": "http.request", "body": body, "more_body": False}

        response = await call_next(Request(request.scope, receive))
        response.headers["X-Auth-Mode"] = "jwt"
        response.headers["X-Auth-User"] = str(claims["sub"])
        if quota_decision is not None:
            for key, value in quota_headers(quota_decision).items():
                response.headers[key] = value
        return response


def install_auth_quota(app: FastAPI, config: AuthConfig | None = None) -> AuthConfig:
    active = config or AuthConfig.from_env()
    app.add_middleware(AuthQuotaMiddleware, config=active)
    return active


def auth_status(config: AuthConfig | None = None) -> dict[str, Any]:
    active = config or AuthConfig.from_env()
    data = asdict(active)
    data["jwt_secret"] = bool(active.jwt_secret)
    return data


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate or verify Phase 34 JWT tokens.")
    parser.add_argument("--user-id", default="local-user")
    parser.add_argument("--secret", default=os.environ.get("PHASE34_JWT_SECRET"))
    parser.add_argument("--issuer", default=os.environ.get("PHASE34_JWT_ISSUER", "mythos-local"))
    parser.add_argument("--audience", default=os.environ.get("PHASE34_JWT_AUDIENCE", "mythos-api"))
    parser.add_argument("--ttl-seconds", type=int, default=3600)
    parser.add_argument("--scope", action="append", default=["api"])
    parser.add_argument("--verify-token")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not args.secret:
        raise SystemExit("Provide --secret or PHASE34_JWT_SECRET.")
    if args.verify_token:
        claims = verify_jwt(args.verify_token, secret=args.secret, issuer=args.issuer, audience=args.audience)
        print(json.dumps({"valid": True, "claims": claims}, indent=2))
        return
    token = create_jwt(subject=args.user_id, secret=args.secret, issuer=args.issuer, audience=args.audience, scopes=args.scope, ttl_seconds=args.ttl_seconds)
    print(json.dumps({"token_type": "Bearer", "access_token": token, "expires_in": args.ttl_seconds, "user_id": args.user_id}, indent=2))  # nosec B105


if __name__ == "__main__":
    main()
