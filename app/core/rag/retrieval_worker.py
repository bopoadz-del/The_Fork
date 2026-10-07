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
``1``/``0`` force it. Worker count: ``RAG_RETRIEVAL_WORKERS`` when set, else
the task's available vCPUs (its CPU quota, not the host's cores) times
``RAG_RETRIEVAL_WORKERS_PER_VCPU`` (default 2: a retrieval spends much of its
time waiting on the database, when another can use the CPU). Callers beyond
them wait in the pool's queue, which the chat endpoint's turn gate bounds
(``app.core.turn_gate``). Workers run at a lower CPU priority
(``RAG_RETRIEVAL_WORKER_NICE``, default 5) so the web process answers first.

The models are loaded once, in the model server (app.core.rag.model_server),
and shared by every worker, so memory does not grow with the worker count.

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


def available_vcpus() -> float:
    """CPUs this task may use: the cgroup CPU quota when there is one (a
    1-vCPU Fargate task on a bigger host has a quota of 1), else the CPUs
    this process may run on."""
    try:
        with open("/sys/fs/cgroup/cpu.max", encoding="ascii") as fh:  # cgroup v2
            quota, period = fh.read().split()[:2]
        if quota != "max":
            return max(0.1, int(quota) / int(period))
    except (OSError, ValueError):
        pass
    try:
        with open("/sys/fs/cgroup/cpu/cpu.cfs_quota_us", encoding="ascii") as fh:  # cgroup v1
            quota_us = int(fh.read())
        with open("/sys/fs/cgroup/cpu/cpu.cfs_period_us", encoding="ascii") as fh:
            period_us = int(fh.read())
        if quota_us > 0:
            return max(0.1, quota_us / period_us)
    except (OSError, ValueError):
        pass
    try:
        return float(len(os.sched_getaffinity(0)))
    except (AttributeError, OSError):
        return float(os.cpu_count() or 1)


def _workers() -> int:
    raw = (os.getenv("RAG_RETRIEVAL_WORKERS") or "").strip()
    if raw:
        try:
            return max(1, int(raw))
        except ValueError:
            _LOG.warning("RAG_RETRIEVAL_WORKERS=%r is not a number; sizing from vCPUs", raw)
    try:
        per = float(os.getenv("RAG_RETRIEVAL_WORKERS_PER_VCPU") or "2")
    except ValueError:
        per = 2.0
    return max(1, round(available_vcpus() * per))


def _worker_start(model_server: Optional[tuple]) -> None:
    """Runs once in each new worker."""
    try:
        nice = int(os.getenv("RAG_RETRIEVAL_WORKER_NICE") or "5")
    except ValueError:
        nice = 5
    if nice and hasattr(os, "nice"):
        try:
            os.nice(nice)
        except OSError:
            _LOG.warning("retrieval worker could not lower its CPU priority", exc_info=True)
    if model_server is not None:
        try:
            from app.core.rag import model_server as ms

            ms.install_in_this_process(*model_server)
        except Exception:  # noqa: BLE001 -- the worker still answers, with its own model
            _LOG.warning("retrieval worker could not reach the model server; loading its own model",
                         exc_info=True)


def _model_server_address() -> Optional[tuple]:
    if (os.getenv("RAG_MODEL_SERVER") or "1").strip().lower() in ("0", "false", "no", "off"):
        return None
    try:
        from app.core.rag import model_server as ms

        return ms.start()
    except Exception:  # noqa: BLE001
        _LOG.warning("retrieval model server did not start; each worker loads its own model",
                     exc_info=True)
        return None


def _pool() -> ProcessPoolExecutor:
    global _POOL
    with _POOL_LOCK:
        if _POOL is None:
            n = _workers()
            _POOL = ProcessPoolExecutor(max_workers=n,
                                        mp_context=multiprocessing.get_context("spawn"),
                                        initializer=_worker_start,
                                        initargs=(_model_server_address(),))
            _LOG.info("retrieval worker pool: %d processes (%.2f vCPUs available)", n, available_vcpus())
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
            result, cpu_s = pool.submit(_measured, job, *args).result()
            _log_cpu(job, cpu_s)
            return result
        except BrokenProcessPool:
            _LOG.warning("retrieval worker died; starting a new one (attempt %d)", attempt)
            _reset_pool(pool)
            if attempt == 2:
                raise


async def call(job: Callable[..., Any], *args: Any) -> Any:
    """``call_sync`` without blocking the event loop."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, functools.partial(call_sync, job, *args))


def _measured(job: Callable[..., Any], *args: Any) -> tuple:
    """Run ``job`` in the worker and report the CPU time it used there."""
    import time

    t = time.process_time()
    result = job(*args)
    return result, time.process_time() - t


def _log_cpu(job: Callable[..., Any], cpu_s: float) -> None:
    from app.core import turn_timing

    if job is rag_inject_job:
        turn_timing.log_stage("retrieval_cpu", cpu_s)


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


def _pid_after_warm() -> int:
    warm_job()
    return os.getpid()


def warm_all() -> int:
    """At boot: start every worker (each warms through the model server) and
    route this process's own embedder to the model server too, so the model
    exists once in the task. Returns the number of workers started."""
    import time

    pool = _pool()
    n = _workers()
    pids = set()
    deadline = time.monotonic() + 300
    # Each job holds its worker briefly, so n concurrent jobs reach n workers.
    while len(pids) < n and time.monotonic() < deadline:
        futures = [pool.submit(_pid_after_warm) for _ in range(n)]
        pids.update(f.result() for f in futures)
    address = _model_server_address()
    if address is not None:
        try:
            from app.core.rag import model_server as ms

            ms.install_in_this_process(*address)
        except Exception:  # noqa: BLE001
            _LOG.warning("web process could not reach the model server; keeping its own embedder",
                         exc_info=True)
    return len(pids)
