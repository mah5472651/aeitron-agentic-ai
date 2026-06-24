# Phase 6 Redis Continuous Regenerative Quota Blueprint

## Redis Hash Contract

Each user maps to a persistent Redis hash:

```text
quota:{tenant}:{sha256(user_id)}
```

Required fields:

```text
tokens_balance             floating-point current usage credits
last_updated_timestamp     floating-point UNIX epoch seconds
```

No static reset windows are used.

## Replenishment Formula

On each API request:

```text
Delta_t = T_now - T_last
Tokens_current = min(C, Tokens_last + Delta_t * R)
```

Where:

- `C` is the hard maximum capacity ceiling.
- `R` is the replenishment rate in tokens per second.
- request `cost` is decremented only if `Tokens_current >= cost`.

## Atomic Lua Execution

Source:

```text
src/phase6/redis_regenerative_bucket.lua
```

The Lua script runs entirely inside Redis single-threaded execution space:

1. `HGET tokens_balance`
2. `HGET last_updated_timestamp`
3. calculate delta and regenerated balance
4. compare regenerated balance against request cost
5. decrement if allowed
6. `HSET tokens_balance last_updated_timestamp`
7. return `[allowed, remaining_balance, regenerated_balance, retry_after]`

This prevents distributed race conditions and split-brain quota calculations.

## Python Middleware

Source:

```text
src/phase6/redis_quota_engine.py
```

The FastAPI middleware:

- extracts user identity from `X-User-ID`
- estimates request cost from body size or `X-Quota-Cost`
- calls the Lua script through `EVALSHA`
- injects response headers:
  - `X-Quota-Allowed`
  - `X-Quota-Remaining`
  - `X-Quota-Regenerated`
  - `X-Quota-Capacity`
  - `X-Quota-Refill-Rate`
  - `X-Quota-Cost`
  - `Retry-After` on denial

## Example Policy

```python
QuotaPolicy(
    capacity=300.0,
    refill_rate=5.0 / 60.0,  # 5 tokens per minute
    initialize_full=True,
    tenant="production",
)
```
