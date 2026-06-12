"""Per-caller rate limiting with optional Redis-backed sliding window.

A fixed 60-second sliding window per identity. This covers EVERY request,
including JWT-authenticated sessions — the per-API-key limiter in
app/core/auth.py only ever saw legacy API keys, so logged-in user traffic
(the bulk of the API) was completely unthrottled.

Controlled by RATE_LIMIT_PER_MINUTE (default 300; 0 or negative disables it).

When REDIS_URL is set and reachable, limits are shared across uvicorn workers
via a Redis sorted-set sliding window. Otherwise falls back to the in-process
limiter (fine for single-worker dev).
"""

import os
import threading
import time
from collections import deque
from typing import Deque, Dict, Optional

_WINDOW_SECONDS = 60.0
_lock = threading.Lock()
_buckets: Dict[str, Deque[float]] = {}

# Set by init_rate_limiter(); None until first check or explicit init.
_use_redis: Optional[bool] = None
_redis_limiter: Optional["RedisRateLimiter"] = None

_SLIDING_WINDOW_LUA = """
local key = KEYS[1]
local now = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local limit = tonumber(ARGV[3])
redis.call('ZREMRANGEBYSCORE', key, 0, now - window)
local count = redis.call('ZCARD', key)
if count >= limit then
    return 0
end
redis.call('ZADD', key, now, tostring(now))
redis.call('EXPIRE', key, math.ceil(window) + 1)
return 1
"""


def _limit() -> int:
    """Requests allowed per identity per minute (read at call time)."""
    try:
        return int(os.getenv("RATE_LIMIT_PER_MINUTE", "300"))
    except (TypeError, ValueError):
        return 300


def _in_memory_check_and_record(identity: str) -> bool:
    limit = _limit()
    if limit <= 0:
        return True
    now = time.time()
    cutoff = now - _WINDOW_SECONDS
    with _lock:
        bucket = _buckets.get(identity)
        if bucket is None:
            bucket = _buckets[identity] = deque()
        while bucket and bucket[0] < cutoff:
            bucket.popleft()
        if len(bucket) >= limit:
            return False
        bucket.append(now)
        if len(_buckets) > 5000:
            stale = [k for k, b in _buckets.items() if not b or b[-1] < cutoff]
            for k in stale:
                _buckets.pop(k, None)
        return True


class RedisRateLimiter:
    """Shared sliding-window limiter backed by a Redis sorted set."""

    _PREFIX = "ratelimit:"

    def __init__(self, redis_url: str):
        import redis  # lazy import — optional when REDIS_URL is unset

        self._client = redis.from_url(redis_url, decode_responses=True)
        self._script = self._client.register_script(_SLIDING_WINDOW_LUA)

    def ping(self) -> None:
        self._client.ping()

    def check_and_record(self, identity: str) -> bool:
        limit = _limit()
        if limit <= 0:
            return True
        key = f"{self._PREFIX}{identity}"
        try:
            allowed = self._script(
                keys=[key],
                args=[time.time(), _WINDOW_SECONDS, limit],
            )
            return bool(allowed)
        except Exception:
            # Redis hiccup — fail open so a cache outage doesn't brick the API.
            return True


def init_rate_limiter() -> str:
    """Select and warm the active backend. Returns a short label for logging."""
    global _use_redis, _redis_limiter
    redis_url = os.getenv("REDIS_URL", "").strip()
    if redis_url:
        try:
            limiter = RedisRateLimiter(redis_url)
            limiter.ping()
            _redis_limiter = limiter
            _use_redis = True
            return "redis"
        except Exception:
            pass
    _redis_limiter = None
    _use_redis = False
    return "in-memory"


def _ensure_backend() -> None:
    if _use_redis is None:
        init_rate_limiter()


def reset_for_tests() -> None:
    """Reset backend selection and in-memory buckets (tests only)."""
    global _use_redis, _redis_limiter
    _use_redis = None
    _redis_limiter = None
    _buckets.clear()


def check_and_record(identity: str) -> bool:
    """Return True if a request from ``identity`` is within its per-minute
    limit — recording it — or False if the identity is over the limit."""
    _ensure_backend()
    if _use_redis and _redis_limiter is not None:
        return _redis_limiter.check_and_record(identity)
    return _in_memory_check_and_record(identity)
