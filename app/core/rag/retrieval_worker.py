"""Retrieval runs in a worker PROCESS, so its CPU work cannot starve the event loop.

Live 2026-10-07, 15 users at once: with every blocking call already moved off
the event loop, /livez still waited up to 3.9 s. The loop thread was idle but
waiting for the GIL while retrieval threads ran pure-Python passes over every
candidate chunk (and matched messages against thousands of file names). Threads
cannot fix that; a process can -- the web process keeps its own GIL.

The worker owns the embedder and runs ``rag_inject``, the document-search tool,
the corpus-readiness check and file-name matching. The web process no longer
loads the embedding model at all on the chat path, so the worker's copy is the
only one (memory moves, it does not double).

``RAG_RETRIEVAL_PROCESS``: ``auto`` (default) = on when ENV is production;
``1``/``0`` force it. ``RAG_RETRIEVAL_WORKERS`` (default 1) worker processes;
callers beyond them wait in the pool's queue, which the chat endpoint's turn
gate bounds (``app.core.turn_gate``).

A worker that dies is replaced and the call retried once in the new one.
Context the retrieval reads (the caller's role) is passed explicitly; context
variables do not cross a process boundary.
"""
from __future__ import annotations

import asyncio
import functools
import logging
import multiprocessing
import os
import threading
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from typing import Any, Callable, Optional

_LOG = logging.getLogger(__name__)
_POOL: Optional[ProcessPoolExecutor] = None
_POOL_LOCK = threading.Lock()


def enabled() -> bool:
    raw = (os.getenv("RAG_RETRIEVAL_PROCESS") or "auto").strip().lower()
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    return (os.getenv("ENV") or "").strip().lower() in ("production", "prod")


def _workers() -> int:
    try:
        return max(1, int(os.getenv("RAG_RETRIEVAL_WORKERS") or "1"))
    except ValueError:
        return 1


def _pool() -> ProcessPoolExecutor:
    global _POOL
    with _POOL_LOCK:
        if _POOL is None:
            _POOL = ProcessPoolExecutor(max_workers=_workers(),
                                        mp_context=multiprocessing.get_context("spawn"))
        return _POOL


def _reset_pool(broken: ProcessPoolExecutor) -> None:
    global _POOL
    with _POOL_LOCK:
        if _POOL is broken:
            _POOL = None
    broken.shutdown(wait=False, cancel_futures=True)


def call_sync(job: Callable[..., Any], *args: Any) -> Any:
    """Run ``job(*args)`` in the worker; blocks the calling thread (not the GIL)."""
    for attempt in (1, 2):
        pool = _pool()
        try:
            return pool.submit(job, *args).result()
        except BrokenProcessPool:
            _LOG.warning("retrieval worker died; starting a new one (attempt %d)", attempt)
            _reset_pool(pool)
            if attempt == 2:
                raise


async def call(job: Callable[..., Any], *args: Any) -> Any:
    """``call_sync`` without blocking the event loop."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, functools.partial(call_sync, job, *args))


# ── jobs: top-level, so the worker can import them by name ───────────────────

def _as_caller(role: Optional[str]) -> None:
    from app.core.privileges import set_caller_role

    set_caller_role(role)


def rag_inject_job(kwargs: dict, caller_role: Optional[str]):
    _as_caller(caller_role)
    from app.core.rag.inject import rag_inject

    return rag_inject(**kwargs)


def search_documents_job(project_id: str, query: str, top_k: int, caller_role: Optional[str]):
    _as_caller(caller_role)
    from app.core.doc_index import _search_project_documents_sync

    return _search_project_documents_sync(project_id, query, top_k)


def project_is_rag_ready_job(project_id: str) -> bool:
    from app.core.rag.retriever import project_is_rag_ready

    return project_is_rag_ready(project_id)


def names_mentioned_job(user_low: str, names: list) -> list:
    from app.core.file_mentions import names_mentioned

    return names_mentioned(user_low, names)


def warm_job() -> bool:
    from app.core.rag.embeddings import get_embedder

    get_embedder().encode(["warmup"])
    return True
