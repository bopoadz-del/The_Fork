"""In-process per-caller rate limiting.

A fixed 60-second sliding window per identity. This covers EVERY request,
including JWT-authenticated sessions — the per-API-key limiter in
app/core/auth.py only ever saw legacy API keys, so logged-in user traffic
(the bulk of the API) was completely unthrottled.

Controlled by RATE_LIMIT_PER_MINUTE (default 300; 0 or negative disables it).
In-process only — a multi-worker deployment would want a shared store (Redis),
but this is a real safety net against runaway clients and basic abuse.
"""

import os
import threading
import time
from collections import deque
from typing import Deque, Dict

_WINDOW_SECONDS = 60.0
_lock = threading.Lock()
_buckets: Dict[str, Deque[float]] = {}


def _limit() -> int:
    """Requests allowed per identity per minute (read at call time)."""
    try:
        return int(os.getenv("RATE_LIMIT_PER_MINUTE", "300"))
    except (TypeError, ValueError):
        return 300


def check_and_record(identity: str) -> bool:
    """Return True if a request from ``identity`` is within its per-minute
    limit — recording it — or False if the identity is over the limit."""
    limit = _limit()
    if limit <= 0:
        return True  # rate limiting disabled
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
        # Opportunistic cleanup so idle identities don't accumulate forever.
        if len(_buckets) > 5000:
            stale = [k for k, b in _buckets.items() if not b or b[-1] < cutoff]
            for k in stale:
                _buckets.pop(k, None)
        return True
