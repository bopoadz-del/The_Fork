"""Evaluated capability probes for health/readiness endpoints (audit §8.5/§8.6).

Health endpoints previously reported a HARDCODED ``status:"healthy"`` and a
provider leaderboard seeded at 100% reliability — so they could claim the DB,
embedder, or an LLM provider were up when the container could not actually
reach them. These probes report what is ACTUALLY observed.

Every probe is fast, bounded, and NEVER raises out to the caller — a probe that
can hang or crash the health endpoint is worse than the lie it replaces.
"""
from __future__ import annotations

import time
from typing import Any, Dict


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


def probe_corpus_chunks(timeout_s: float = 1.5) -> Dict[str, Any]:
    """COUNT(*) against the active chunk table — never documents.chunk_count.

    The ledger column can sit at 0 after a successful index (the defect
    this probe exists to stop reporting as truth). A missing table is an
    honest zero, not a hang: /health stays a liveness probe.
    """
    started = time.monotonic()
    try:
        import os
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
            n = session.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar()
        return {
            "chunks": int(n or 0),
            "source": "chunk_table_count",
            "table": table,
            "latency_ms": int((time.monotonic() - started) * 1000),
            "error": None,
        }
    except Exception as exc:  # noqa: BLE001 — probe must never raise
        return {
            "chunks": None,
            "source": "chunk_table_count",
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
