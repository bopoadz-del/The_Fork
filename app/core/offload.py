"""Run a chat turn's synchronous work off the event loop, with a bounded queue.

Live 2026-10-07, 15 users at once: /livez waited up to 10.9 s. A turn's
database reads and writes (conversation store, document names, coverage
counts) and its answer post-processing ran synchronously inside the async chat
path, so each one froze the single event loop for every user.

``await off_loop(fn, *args, **kw)`` runs ``fn`` in a dedicated thread pool.
The pool has ``TURN_IO_WORKERS`` threads (default: the database pool size,
``DB_POOL_SIZE``), so offloaded work never asks for more connections than
exist; when every worker is busy, further calls wait in the pool's queue
instead of freezing the server.

Context variables behave as if ``fn`` ran inline: ``fn`` sees the caller's
context (the caller-role gate, the turn's provenance record), and values it
sets are copied back to the caller when it returns.
"""
from __future__ import annotations

import asyncio
import contextvars
import functools
import os
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Optional, TypeVar

T = TypeVar("T")
_MISSING = object()
_POOL: Optional[ThreadPoolExecutor] = None


def _workers() -> int:
    for name, default in (("TURN_IO_WORKERS", None), ("DB_POOL_SIZE", "10")):
        raw = (os.getenv(name) or "").strip() or default
        if raw:
            try:
                return max(1, int(raw))
            except ValueError:
                continue
    return 10


def pool() -> ThreadPoolExecutor:
    global _POOL
    if _POOL is None:
        _POOL = ThreadPoolExecutor(max_workers=_workers(), thread_name_prefix="turn-io")
    return _POOL


async def off_loop(fn: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    """``fn(*args, **kwargs)`` in the turn pool; context variables round-trip."""
    ctx = contextvars.copy_context()
    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(pool(), functools.partial(ctx.run, fn, *args, **kwargs))
    for var, value in ctx.items():
        if var.get(_MISSING) is not value:
            var.set(value)
    return result
