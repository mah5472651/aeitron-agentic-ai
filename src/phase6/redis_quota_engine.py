#!/usr/bin/env python
"""High-throughput Redis continuous regenerative quota engine.

Redis data contract per user:
  HSET quota:{tenant}:{sha256(user_id)}
    tokens_balance            float
    last_updated_timestamp    float UNIX epoch seconds

The Lua script atomically applies:
  Tokens_current = min(C, Tokens_last + Delta_t * R)

Then it checks whether Tokens_current >= request_cost, decrements if allowed,
updates Redis, and returns an allowed/denied decision plus remaining balance.

The module includes a FastAPI middleware factory. FastAPI is imported lazily so
the quota core can be reused without installing web framework dependencies.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping

import redis.asyncio as redis
from redis.exceptions import NoScriptError, RedisError


SCRIPT_PATH = Path(__file__).with_name("redis_regenerative_bucket.lua")


@dataclass(frozen=True)
class QuotaPolicy:
    capacity: float
    refill_rate: float
    initialize_full: bool = True
    tenant: str = "default"

    def validate(self) -> None:
        if self.capacity <= 0:
            raise ValueError("capacity must be positive")
        if self.refill_rate < 0:
            raise ValueError("refill_rate must be non-negative")
        if not self.tenant:
            raise ValueError("tenant cannot be empty")


@dataclass(frozen=True)
class QuotaDecision:
    allowed: bool
    user_id: str
    key: str
    cost: float
    remaining_balance: float
    regenerated_balance: float
    retry_after_seconds: float
    capacity: float
    refill_rate: float
    timestamp: float


class QuotaDenied(Exception):
    def __init__(self, decision: QuotaDecision) -> None:
        self.decision = decision
        super().__init__(
            f"quota denied for {decision.user_id}; remaining={decision.remaining_balance:.6f}"
        )


class QuotaBackendError(RuntimeError):
    """Raised when Redis quota execution fails and fail-open is disabled."""


def now_seconds() -> float:
    return time.time()


def stable_user_key(user_id: str, tenant: str) -> str:
    digest = hashlib.sha256(user_id.encode("utf-8")).hexdigest()
    safe_tenant = tenant.replace(":", "_")
    return f"quota:{safe_tenant}:{digest}"


def parse_float(value: Any) -> float:
    if isinstance(value, bytes):
        value = value.decode("utf-8")
    return float(value)


def load_lua_script() -> str:
    return SCRIPT_PATH.read_text(encoding="utf-8")


class RedisRegenerativeQuotaEngine:
    """Async Redis-backed continuous token bucket engine."""

    def __init__(
        self,
        redis_client: redis.Redis,
        policy: QuotaPolicy,
        script_source: str | None = None,
    ) -> None:
        policy.validate()
        self.redis = redis_client
        self.policy = policy
        self.script_source = script_source or load_lua_script()
        self._script_sha: str | None = None
        self._script_lock = asyncio.Lock()

    async def initialize(self) -> None:
        async with self._script_lock:
            self._script_sha = await self.redis.script_load(self.script_source)

    def key_for_user(self, user_id: str) -> str:
        return stable_user_key(user_id, self.policy.tenant)

    async def seed_account(
        self,
        user_id: str,
        tokens_balance: float | None = None,
        last_updated_timestamp: float | None = None,
    ) -> None:
        balance = self.policy.capacity if tokens_balance is None else tokens_balance
        balance = max(0.0, min(self.policy.capacity, float(balance)))
        timestamp = now_seconds() if last_updated_timestamp is None else float(last_updated_timestamp)
        await self.redis.hset(
            self.key_for_user(user_id),
            mapping={
                "tokens_balance": f"{balance:.17g}",
                "last_updated_timestamp": f"{timestamp:.17g}",
            },
        )

    async def get_raw_account(self, user_id: str) -> dict[str, float | None]:
        raw = await self.redis.hgetall(self.key_for_user(user_id))
        return {
            "tokens_balance": parse_float(raw["tokens_balance"]) if "tokens_balance" in raw else None,
            "last_updated_timestamp": parse_float(raw["last_updated_timestamp"])
            if "last_updated_timestamp" in raw
            else None,
        }

    async def check_and_consume(
        self,
        user_id: str,
        cost: float,
        timestamp: float | None = None,
    ) -> QuotaDecision:
        if cost < 0:
            raise ValueError("cost must be non-negative")
        if self._script_sha is None:
            await self.initialize()
        return await self._eval(user_id=user_id, cost=cost, timestamp=timestamp or now_seconds())

    async def _eval(self, user_id: str, cost: float, timestamp: float) -> QuotaDecision:
        key = self.key_for_user(user_id)
        args = [
            f"{timestamp:.17g}",
            f"{cost:.17g}",
            f"{self.policy.refill_rate:.17g}",
            f"{self.policy.capacity:.17g}",
            "1" if self.policy.initialize_full else "0",
        ]
        try:
            result = await self.redis.evalsha(self._script_sha, 1, key, *args)
        except NoScriptError:
            await self.initialize()
            result = await self.redis.evalsha(self._script_sha, 1, key, *args)
        allowed_raw, remaining, regenerated, retry_after = result
        return QuotaDecision(
            allowed=bool(int(allowed_raw)),
            user_id=user_id,
            key=key,
            cost=cost,
            remaining_balance=parse_float(remaining),
            regenerated_balance=parse_float(regenerated),
            retry_after_seconds=parse_float(retry_after),
            capacity=self.policy.capacity,
            refill_rate=self.policy.refill_rate,
            timestamp=timestamp,
        )


class RequestCostEstimator:
    """Simple request cost estimator for AI API payloads."""

    def __init__(
        self,
        base_cost: float = 1.0,
        chars_per_token: int = 4,
        output_weight: float = 0.5,
    ) -> None:
        self.base_cost = base_cost
        self.chars_per_token = chars_per_token
        self.output_weight = output_weight

    def estimate(self, payload: bytes, headers: Mapping[str, str] | None = None) -> float:
        headers = headers or {}
        explicit = headers.get("x-quota-cost")
        if explicit is not None:
            return max(0.0, float(explicit))
        text = payload.decode("utf-8", errors="replace")
        input_tokens = max(1, math.ceil(len(text) / self.chars_per_token))
        output_tokens = self._extract_output_tokens(text)
        multiplier = self._complexity_multiplier(text)
        return round(
            self.base_cost + multiplier * ((input_tokens / 1000.0) + (output_tokens * self.output_weight / 1000.0)),
            6,
        )

    @staticmethod
    def _extract_output_tokens(text: str) -> int:
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return 0
        for key in ("max_tokens", "max_output_tokens", "output_tokens"):
            if isinstance(payload, dict) and key in payload:
                try:
                    return max(0, int(payload[key]))
                except (TypeError, ValueError):
                    return 0
        return 0

    @staticmethod
    def _complexity_multiplier(text: str) -> float:
        lower = text.lower()
        markers = [
            "architecture",
            "compile",
            "sandbox",
            "security",
            "vulnerability",
            "self-healing",
            "fine-tuning",
            "swarm",
            "qdrant",
            "postgres",
            "docker",
        ]
        return 1.0 + min(2.5, 0.25 * sum(1 for marker in markers if marker in lower))


def normalize_headers(headers: Any) -> dict[str, str]:
    return {str(key).lower(): str(value) for key, value in dict(headers).items()}


async def enforce_quota(
    engine: RedisRegenerativeQuotaEngine,
    user_id: str,
    cost: float,
    fail_open: bool = False,
) -> QuotaDecision:
    try:
        decision = await engine.check_and_consume(user_id=user_id, cost=cost)
    except RedisError as exc:
        if fail_open:
            return QuotaDecision(
                allowed=True,
                user_id=user_id,
                key=engine.key_for_user(user_id),
                cost=cost,
                remaining_balance=-1.0,
                regenerated_balance=-1.0,
                retry_after_seconds=0.0,
                capacity=engine.policy.capacity,
                refill_rate=engine.policy.refill_rate,
                timestamp=now_seconds(),
            )
        raise QuotaBackendError(f"Redis quota backend failed: {exc}") from exc
    if not decision.allowed:
        raise QuotaDenied(decision)
    return decision


def quota_headers(decision: QuotaDecision) -> dict[str, str]:
    headers = {
        "X-Quota-Allowed": "1" if decision.allowed else "0",
        "X-Quota-Remaining": f"{decision.remaining_balance:.6f}",
        "X-Quota-Regenerated": f"{decision.regenerated_balance:.6f}",
        "X-Quota-Capacity": f"{decision.capacity:.6f}",
        "X-Quota-Refill-Rate": f"{decision.refill_rate:.6f}",
        "X-Quota-Cost": f"{decision.cost:.6f}",
    }
    if not decision.allowed and decision.retry_after_seconds >= 0:
        headers["Retry-After"] = str(max(1, math.ceil(decision.retry_after_seconds)))
    return headers


def create_fastapi_app(
    redis_url: str,
    policy: QuotaPolicy,
    protected_prefixes: tuple[str, ...] = ("/api",),
    user_header: str = "x-user-id",
    fail_open: bool = False,
) -> Any:
    """Create a FastAPI app with regenerative quota middleware.

    This function imports FastAPI lazily so non-web deployments can use the
    quota engine without installing FastAPI.
    """

    from fastapi import FastAPI, Request
    from fastapi.responses import JSONResponse, Response
    from starlette.middleware.base import BaseHTTPMiddleware

    app = FastAPI(title="Regenerative Quota API")
    redis_client = redis.from_url(redis_url, decode_responses=True)
    engine = RedisRegenerativeQuotaEngine(redis_client=redis_client, policy=policy)
    estimator = RequestCostEstimator()

    @app.on_event("startup")
    async def startup() -> None:
        await engine.initialize()

    @app.on_event("shutdown")
    async def shutdown() -> None:
        await redis_client.aclose()

    class RegenerativeQuotaMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
            if not request.url.path.startswith(protected_prefixes):
                return await call_next(request)

            body = await request.body()
            headers = normalize_headers(request.headers)
            user_id = headers.get(user_header.lower())
            if not user_id:
                return JSONResponse(
                    status_code=401,
                    content={"error": "missing_user_identity", "header": user_header},
                )

            try:
                cost = estimator.estimate(body, headers)
                decision = await enforce_quota(engine, user_id=user_id, cost=cost, fail_open=fail_open)
            except QuotaDenied as exc:
                denied_headers = quota_headers(exc.decision)
                return JSONResponse(
                    status_code=429,
                    headers=denied_headers,
                    content={
                        "error": "quota_denied",
                        "remaining_balance": exc.decision.remaining_balance,
                        "retry_after_seconds": exc.decision.retry_after_seconds,
                    },
                )
            except (QuotaBackendError, ValueError) as exc:
                return JSONResponse(status_code=503, content={"error": "quota_backend_error", "detail": str(exc)})

            response = await call_next(request)
            for key, value in quota_headers(decision).items():
                response.headers[key] = value
            return response

    app.add_middleware(RegenerativeQuotaMiddleware)

    @app.post("/api/example")
    async def example_route() -> dict[str, str]:
        return {"status": "accepted"}

    return app


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Redis continuous regenerative quota engine.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    consume = subparsers.add_parser("consume", help="Atomically consume quota for a user.")
    consume.add_argument("--redis-url", default="redis://localhost:6379/0")
    consume.add_argument("--user-id", required=True)
    consume.add_argument("--cost", type=float, required=True)
    consume.add_argument("--capacity", type=float, default=100.0)
    consume.add_argument("--refill-rate", type=float, default=1.0, help="R tokens per second.")
    consume.add_argument("--tenant", default="default")
    consume.add_argument("--initialize-empty", action="store_true")

    seed = subparsers.add_parser("seed", help="Seed a user hash account.")
    seed.add_argument("--redis-url", default="redis://localhost:6379/0")
    seed.add_argument("--user-id", required=True)
    seed.add_argument("--tokens-balance", type=float)
    seed.add_argument("--capacity", type=float, default=100.0)
    seed.add_argument("--refill-rate", type=float, default=1.0)
    seed.add_argument("--tenant", default="default")

    return parser.parse_args()


async def async_main() -> None:
    args = parse_args()
    policy = QuotaPolicy(
        capacity=args.capacity,
        refill_rate=args.refill_rate,
        initialize_full=not getattr(args, "initialize_empty", False),
        tenant=args.tenant,
    )
    client = redis.from_url(args.redis_url, decode_responses=True)
    try:
        engine = RedisRegenerativeQuotaEngine(redis_client=client, policy=policy)
        await engine.initialize()
        if args.command == "seed":
            await engine.seed_account(args.user_id, tokens_balance=args.tokens_balance)
            output = {"ok": True, "account": await engine.get_raw_account(args.user_id)}
        else:
            output = {"ok": True, "decision": asdict(await engine.check_and_consume(args.user_id, args.cost))}
        print(json.dumps(output, indent=2))
    except (RedisError, OSError) as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "redis_unavailable",
                    "detail": str(exc),
                },
                indent=2,
            )
        )
        raise SystemExit(1)
    finally:
        await client.aclose()


def main() -> None:
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
