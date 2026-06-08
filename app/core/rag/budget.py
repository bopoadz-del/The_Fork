"""Daily RAG injection token budget.

Layered on top of the per-turn ``MAX_RAG_TOKENS`` cap and the
``RAG_CONFIDENCE_THRESHOLD`` short-circuit so RAG token spend has
three independent degradation paths. When the day's consumed sum
reaches the budget INCLUSIVELY (``consumed >= budget``), the
injector degrades to ``K=2`` for the remaining turns of the day.

Day rollover is implicit: callers pass ``day=utc.strftime("%Y-%m-%d")``
and a fresh row materialises on the first read of a new date.

Schema lives in its own SQLite file at ``${DATA_DIR}/rag/budget.db``
so wipes are local and the audit DB stays focused.
"""
from __future__ import annotations

import os
import sqlite3
import threading
from contextlib import closing
from typing import Dict

_LOCK = threading.RLock()


def _db_path() -> str:
    base = os.getenv("DATA_DIR", "./data")
    d = os.path.join(base, "rag")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "budget.db")


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path())
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _ensure_db() -> None:
    with _LOCK, closing(_connect()) as conn, conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS rag_budget (
                day       TEXT PRIMARY KEY,
                consumed  INTEGER NOT NULL DEFAULT 0
            )
            """
        )


def _budget_value() -> int:
    try:
        v = int(os.getenv("RAG_DAILY_TOKEN_BUDGET", "500000"))
    except ValueError:
        return 500000
    return v if v >= 0 else 500000


def snapshot(day: str) -> Dict[str, object]:
    """Return the day's current budget state without mutating it."""
    _ensure_db()
    with _LOCK, closing(_connect()) as conn, conn:
        row = conn.execute(
            "SELECT consumed FROM rag_budget WHERE day = ?", (day,)
        ).fetchone()
    consumed = int(row["consumed"]) if row else 0
    budget = _budget_value()
    return {
        "day": day,
        "consumed": consumed,
        "budget": budget,
        "remaining": max(0, budget - consumed),
        "degraded": consumed >= budget,  # INCLUSIVE boundary (see spec)
    }


def consume(day: str, tokens: int) -> None:
    """Add ``tokens`` to the day's consumed counter atomically."""
    if tokens <= 0:
        return
    _ensure_db()
    with _LOCK, closing(_connect()) as conn, conn:
        conn.execute(
            "INSERT INTO rag_budget (day, consumed) VALUES (?, ?) "
            "ON CONFLICT(day) DO UPDATE SET consumed = consumed + excluded.consumed",
            (day, int(tokens)),
        )
