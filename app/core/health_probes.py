"""Evaluated capability probes for health/readiness endpoints (audit §8.5/§8.6).

Health endpoints previously reported a HARDCODED ``status:"healthy"`` and a
provider leaderboard seeded at 100% reliability — so they could claim the DB,
embedder, or an LLM provider were up when the container could not actually
reach them. These probes report what is ACTUALLY observed.

Every probe is fast, bounded, and NEVER raises out to the caller — a probe that
can hang or crash the health endpoint is worse than the lie it replaces.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_corpus_cache_lock = threading.Lock()
_corpus_cache_value: Optional[Dict[str, Any]] = None
_corpus_cache_at: Optional[float] = None
_corpus_refreshing = False
_corpus_refresh_thread: Optional[threading.Thread] = None
_corpus_refresh_error: Optional[str] = None


def probe_database(timeout_s: float = 2.0) -> Dict[str, Any]:
    """Real DB round-trip: ``SELECT 1`` via the app's session factory.

    Returns ``{"ok": bool, "latency_ms": int|None, "error": str|None}``.
    Bounded and non-raising: any failure (unreachable, auth, timeout) is
    reported as ``ok=False`` with a short reason, never an exception.
    """
    started = time.monotonic()
    try:
        from sqlalchemy import text
        from app.core.db import SessionLocal

        with SessionLocal() as session:
            # Best-effort per-statement timeout on Postgres; harmless no-op
            # (rolled back) elsewhere.
            try:
                session.execute(text("SET LOCAL statement_timeout = :ms"),
                                {"ms": int(timeout_s * 1000)})
            except Exception:  # noqa: BLE001 — SQLite/other: skip, still probe
                session.rollback()
            session.execute(text("SELECT 1"))
        latency_ms = int((time.monotonic() - started) * 1000)
        return {"ok": True, "latency_ms": latency_ms, "error": None}
    except Exception as exc:  # noqa: BLE001 — probe must never raise
        return {
            "ok": False,
            "latency_ms": int((time.monotonic() - started) * 1000),
            "error": f"{type(exc).__name__}: {exc}"[:200],
        }


def reset_corpus_chunk_cache() -> None:
    """Drop the cached corpus count. Tests use this so a prior probe cannot
    answer for a different database. Does not join a refresh already running.
    """
    global _corpus_cache_value, _corpus_cache_at, _corpus_refreshing, _corpus_refresh_error
    with _corpus_cache_lock:
        _corpus_cache_value = None
        _corpus_cache_at = None
        _corpus_refreshing = False
        _corpus_refresh_error = None


_DEFAULT_CORPUS_TTL_S = 300.0
_DEFAULT_CORPUS_NEGATIVE_TTL_S = 30.0


def _env_float(name: str, default: float) -> float:
    """Same numeric-env reader as the other core watchdogs.

    Missing, blank, or non-numeric values keep ``default``.
    """
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _corpus_cache_config() -> Dict[str, float]:
    """Tunables for the corpus-count probe.

    ``ttl_s`` is how long a successful count is served with no database work.
    ``negative_ttl_s`` is how long a failed refresh is remembered so a down
    database is not queried again on every /health hit.
    Both are read from the environment on each call. Unset values stay at
    ``CORPUS_CHUNK_CACHE_TTL_S`` 300 and ``CORPUS_CHUNK_CACHE_NEGATIVE_TTL_S`` 30.
    """
    return {
        "ttl_s": _env_float("CORPUS_CHUNK_CACHE_TTL_S", _DEFAULT_CORPUS_TTL_S),
        "negative_ttl_s": _env_float(
            "CORPUS_CHUNK_CACHE_NEGATIVE_TTL_S", _DEFAULT_CORPUS_NEGATIVE_TTL_S
        ),
    }


def _corpus_ttl_s() -> float:
    return float(_corpus_cache_config()["ttl_s"])


def _corpus_negative_ttl_s() -> float:
    return float(_corpus_cache_config()["negative_ttl_s"])


def _reltuples(session: Any, table: str) -> Optional[float]:
    """Planner estimate for ``table``, or None when Postgres has no row.

    ``reltuples`` is -1 until the table has been analyzed. A query failure
    (SQLite, missing catalog) is the same as a missing estimate.
    """
    try:
        from sqlalchemy import text

        value = session.execute(
            text(
                "SELECT c.reltuples FROM pg_class c "
                "JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE c.relname = :name AND c.relkind = 'r' "
                "AND n.nspname = current_schema()"
            ),
            {"name": table},
        ).scalar()
    except Exception:  # noqa: BLE001 — not Postgres, or the catalog read failed
        session.rollback()
        return None
    if value is None:
        return None
    return float(value)


def _refresh_corpus_count(timeout_s: float = 1.5) -> Dict[str, Any]:
    """Read the chunk-table size. Estimate first; exact COUNT only as fallback.

    The statement timeout is the same bound the old per-probe COUNT used.
    Never raises: a failure is an ``error`` field.
    """
    started = time.monotonic()
    table = None
    try:
        from sqlalchemy import text

        from app.core.db import SessionLocal
        from app.core.models import rag_chunk_table_name

        ns = os.getenv("RAG_VECTOR_NAMESPACE", "v2").strip()
        table = rag_chunk_table_name(ns)
        with SessionLocal() as session:
            try:
                session.execute(
                    text("SET LOCAL statement_timeout = :ms"),
                    {"ms": int(timeout_s * 1000)},
                )
            except Exception:  # noqa: BLE001 — SQLite: skip
                session.rollback()
            estimate = _reltuples(session, table)
            if estimate is None or estimate == -1:
                n = session.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar()
                chunks = int(n or 0)
                source = "exact"
            else:
                chunks = int(round(estimate))
                source = "estimate"
        return {
            "chunks": chunks,
            "source": source,
            "table": table,
            "latency_ms": int((time.monotonic() - started) * 1000),
            "error": None,
        }
    except Exception as exc:  # noqa: BLE001 — probe must never raise
        return {
            "chunks": None,
            "source": "exact",
            "table": table,
            "latency_ms": int((time.monotonic() - started) * 1000),
            "error": f"{type(exc).__name__}: {exc}"[:200],
        }


def _invoke_refresh(timeout_s: float) -> Dict[str, Any]:
    """Call the refresher. A raised error becomes a payload, never an exception."""
    started = time.monotonic()
    try:
        result = _refresh_corpus_count(timeout_s)
    except Exception as exc:  # noqa: BLE001 — a monkeypatched refresher may raise
        return {
            "chunks": None,
            "source": "exact",
            "table": None,
            "latency_ms": int((time.monotonic() - started) * 1000),
            "error": f"{type(exc).__name__}: {exc}"[:200],
        }
    if not isinstance(result, dict):
        return {
            "chunks": None,
            "source": "exact",
            "table": None,
            "latency_ms": int((time.monotonic() - started) * 1000),
            "error": "corpus refresh returned no payload",
        }
    return result


def _store_corpus_count(result: Dict[str, Any]) -> None:
    global _corpus_cache_value, _corpus_cache_at, _corpus_refresh_error
    _corpus_cache_value = dict(result)
    _corpus_cache_at = time.monotonic()
    _corpus_refresh_error = None


def _background_refresh(timeout_s: float) -> None:
    """One refresh off the request path. Failures stay recorded, never raised."""
    global _corpus_refreshing
    result = _invoke_refresh(timeout_s)
    with _corpus_cache_lock:
        _remember_corpus_result(result)
        _corpus_refreshing = False


def _schedule_background_refresh(timeout_s: float) -> None:
    """Start at most one refresh thread. Caller holds ``_corpus_cache_lock``."""
    global _corpus_refreshing, _corpus_refresh_thread
    if _corpus_refreshing:
        return
    _corpus_refreshing = True
    thread = threading.Thread(
        target=_background_refresh,
        args=(timeout_s,),
        name="corpus-chunk-count-refresh",
        daemon=True,
    )
    _corpus_refresh_thread = thread
    thread.start()


def _from_cache(payload: Dict[str, Any], age_s: float, *, stale: bool, latency_ms: int) -> Dict[str, Any]:
    out = dict(payload)
    out["age_s"] = round(float(age_s), 3)
    out["latency_ms"] = latency_ms
    if stale:
        out["stale"] = True
    else:
        out["source"] = "cached"
        out.pop("stale", None)
    return out


def _warming_payload(latency_ms: int) -> Dict[str, Any]:
    """A concurrent cold caller arrived while the one refresh is in flight."""
    return {
        "chunks": None,
        "source": "warming",
        "table": None,
        "latency_ms": latency_ms,
        "error": None,
        "warming": True,
    }


def _remember_corpus_result(result: Dict[str, Any]) -> None:
    """Store a success for the long TTL, or a failure for the negative TTL.

    Caller holds ``_corpus_cache_lock``. A failure does not replace a good count.
    """
    global _corpus_cache_value, _corpus_cache_at, _corpus_refresh_error
    ok = result.get("error") is None and result.get("chunks") is not None
    if ok:
        _store_corpus_count(result)
        return
    if _corpus_cache_value is not None and _corpus_cache_value.get("error") is None:
        _corpus_refresh_error = str(result.get("error") or "corpus refresh failed")[:200]
        logger.warning("corpus chunk count refresh failed: %s", _corpus_refresh_error)
        return
    _corpus_cache_value = dict(result)
    _corpus_cache_at = time.monotonic()
    _corpus_refresh_error = str(result.get("error") or "corpus refresh failed")[:200]
    logger.warning("corpus chunk count refresh failed: %s", _corpus_refresh_error)


def probe_corpus_chunks(timeout_s: float = 1.5) -> Dict[str, Any]:
    """Chunk-table size for /health — never documents.chunk_count.

    The first call reads Postgres ``pg_class.reltuples`` and falls back to
    ``COUNT(*)`` only when that estimate is missing or -1, under the same
    statement timeout as before. Later calls inside the TTL from the
    environment return the cached figure (``source="cached"``, ``age_s``)
    and do no database work. After that TTL the last figure is returned at
    once with ``stale: true`` while a single background thread refreshes it.
    Concurrent cold callers get one refresh; the rest receive a warming
    payload. A failed refresh is remembered for the negative TTL from the
    environment so a down
    database is not queried on every probe. A probe error is a field on the
    payload. This function does not raise.
    """
    global _corpus_refreshing
    started = time.monotonic()
    leader = False
    try:
        now = time.monotonic()
        with _corpus_cache_lock:
            cached = _corpus_cache_value
            cached_at = _corpus_cache_at
            latency_ms = int((time.monotonic() - started) * 1000)
            if cached is not None and cached_at is not None:
                age = now - cached_at
                failed = cached.get("error") is not None or cached.get("chunks") is None
                if failed and age < _corpus_negative_ttl_s():
                    return _from_cache(cached, age, stale=False, latency_ms=latency_ms)
                if not failed and age < _corpus_ttl_s():
                    return _from_cache(cached, age, stale=False, latency_ms=latency_ms)
                if not failed:
                    _schedule_background_refresh(timeout_s)
                    return _from_cache(cached, age, stale=True, latency_ms=latency_ms)
            if _corpus_refreshing:
                return _warming_payload(latency_ms)
            _corpus_refreshing = True
            leader = True
        try:
            result = _invoke_refresh(timeout_s)
            with _corpus_cache_lock:
                _remember_corpus_result(result)
            return result
        finally:
            with _corpus_cache_lock:
                _corpus_refreshing = False
    except Exception as exc:  # noqa: BLE001 — /health must never fail closed on this probe
        if leader:
            with _corpus_cache_lock:
                _corpus_refreshing = False
        return {
            "chunks": None,
            "source": "exact",
            "table": None,
            "latency_ms": int((time.monotonic() - started) * 1000),
            "error": f"{type(exc).__name__}: {exc}"[:200],
        }


def probe_embedder() -> Dict[str, Any]:
    """Report whether the RAG embedder is warm-loaded (NON-loading probe).

    ``loaded=False`` is not an error — the embedder lazy-loads on first use;
    it means retrieval will pay the cold-load cost on the next query. We do
    NOT trigger the load here (that would make /health slow on a cold box).
    """
    try:
        from app.core.rag.embeddings import embedder_is_loaded, loaded_embedder_identity

        loaded = embedder_is_loaded()
        return {"loaded": loaded, "identity": loaded_embedder_identity() if loaded else None}
    except Exception as exc:  # noqa: BLE001
        return {"loaded": False, "identity": None, "error": f"{type(exc).__name__}: {exc}"[:200]}


def probe_llm() -> Dict[str, Any]:
    """Whether the chat can reach a language model, and whether it can SURVIVE
    losing the primary one -- two booleans, nothing else.

    Live 2026-09-19: LLM_FALLBACK_PROVIDER named `kimi`, a provider the code had
    removed. `_cross_provider_fallback` resolved it back to the primary and
    returned None: no fallback at all, and nothing anywhere said so. An unset
    fallback key does the same. /health is public, so this names no provider,
    model, variable or key -- only whether each is usable.
    """
    try:
        import os

        from app.agents.runtime import _llm_config, _llm_fallback_config

        primary = _llm_config()
        key = primary.get("env_key")
        return {
            "primary_ready": bool(os.getenv(key)) if key else True,
            "fallback_ready": _llm_fallback_config(primary) is not None,
        }
    except Exception as exc:  # noqa: BLE001 - a probe never breaks /health
        return {"primary_ready": False, "fallback_ready": False,
                "error": type(exc).__name__}
