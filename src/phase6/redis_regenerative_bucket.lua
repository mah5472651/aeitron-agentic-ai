-- Redis continuous regenerative token bucket.
--
-- Required Redis hash fields per user key:
--   tokens_balance            floating-point current available credits
--   last_updated_timestamp    UNIX epoch timestamp as floating-point seconds
--
-- KEYS[1] = user quota hash key
--
-- ARGV[1] = now timestamp, float seconds
-- ARGV[2] = request cost, float tokens
-- ARGV[3] = replenishment rate R, float tokens per second
-- ARGV[4] = capacity ceiling C, float tokens
-- ARGV[5] = initialize_full, "1" to start new users at capacity, else "0"
--
-- Return:
--   {
--     allowed_integer,          1 allowed, 0 denied
--     remaining_balance_string,
--     regenerated_balance_string,
--     retry_after_seconds_string
--   }

local key = KEYS[1]
local now_ts = tonumber(ARGV[1])
local cost = tonumber(ARGV[2])
local refill_rate = tonumber(ARGV[3])
local capacity = tonumber(ARGV[4])
local initialize_full = tostring(ARGV[5])

if now_ts == nil or cost == nil or refill_rate == nil or capacity == nil then
  return redis.error_reply("invalid numeric quota argument")
end

if cost < 0 then
  return redis.error_reply("cost must be non-negative")
end

if refill_rate < 0 then
  return redis.error_reply("refill_rate must be non-negative")
end

if capacity <= 0 then
  return redis.error_reply("capacity must be positive")
end

local tokens_last = tonumber(redis.call("HGET", key, "tokens_balance"))
local last_updated = tonumber(redis.call("HGET", key, "last_updated_timestamp"))

if tokens_last == nil then
  if initialize_full == "1" then
    tokens_last = capacity
  else
    tokens_last = 0
  end
end

if last_updated == nil then
  last_updated = now_ts
end

if tokens_last < 0 then
  tokens_last = 0
end

if tokens_last > capacity then
  tokens_last = capacity
end

local delta_t = now_ts - last_updated
if delta_t < 0 then
  delta_t = 0
end

local regenerated = tokens_last + (delta_t * refill_rate)
if regenerated > capacity then
  regenerated = capacity
end

local allowed = 0
local remaining = regenerated
local retry_after_seconds = 0

if regenerated >= cost then
  allowed = 1
  remaining = regenerated - cost
else
  local deficit = cost - regenerated
  if refill_rate > 0 then
    retry_after_seconds = deficit / refill_rate
  else
    retry_after_seconds = -1
  end
end

redis.call(
  "HSET",
  key,
  "tokens_balance", string.format("%.17g", remaining),
  "last_updated_timestamp", string.format("%.17g", now_ts)
)

return {
  allowed,
  string.format("%.17g", remaining),
  string.format("%.17g", regenerated),
  string.format("%.17g", retry_after_seconds)
}
