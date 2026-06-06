"""LLM usage + soft-cap tracker.

Records every LLM round-trip (`prompt_tokens`, `completion_tokens`,
estimated cost) to ``${DATA_DIR}/usage.db`` and lets the runtime block
calls once a per-user daily budget is exceeded.

Wired in two places:

* ``app/agents/runtime.py::_call_llm`` — records every response's
  ``usage`` field and short-circuits when ``USAGE_DAILY_CAP_USD`` is
  set and the caller is over the cap.
* ``app/routers/usage.py`` — read endpoints for the UI: today, last 7
  days, per-agent + per-provider breakdown.

Cost model is a hardcoded per-provider+model rate table. Free-tier Groq
models are recorded at $0 — total_tokens still tracked so we can warn
before hitting Groq's daily TPD cap.
"""
from __future__ import annotations

import os
import sqlite3
import threading
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional


_LOCK = threading.RLock()


def _db_path() -> str:
    return os.path.join(os.getenv("DATA_DIR", "./data"), "usage.db")


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path())
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db() -> None:
    os.makedirs(os.path.dirname(_db_path()) or ".", exist_ok=True)
    with _LOCK, _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS runs (
                id                 TEXT PRIMARY KEY,
                user_id            TEXT,
                agent_name         TEXT,
                provider           TEXT,
                model              TEXT,
                prompt_tokens      INTEGER,
                completion_tokens  INTEGER,
                total_tokens       INTEGER,
                estimated_cost_usd REAL,
                created_at         TEXT NOT NULL
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_runs_user_created ON runs(user_id, created_at)")


# Per-provider pricing per 1M tokens loaded from config/llm_pricing.json
# at first use. Operator-editable; missing models / providers cost $0
# (tokens still recorded so daily TPD usage stays visible).
_PRICING_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "config", "llm_pricing.json")
_PRICING_OVERRIDE_ENV = "LLM_PRICING_FILE"
_PRICING_CACHE: Optional[Dict[str, Dict[str, Dict[str, float]]]] = None
_PRICING_MTIME: float = 0.0


def _load_pricing() -> Dict[str, Dict[str, Dict[str, float]]]:
    """Reload the pricing table when the on-disk JSON has changed. The
    operator can edit prices without a redeploy: next `record()` call
    picks up the new file."""
    global _PRICING_CACHE, _PRICING_MTIME
    import json
    path = os.getenv(_PRICING_OVERRIDE_ENV) or _PRICING_PATH
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return {}
    if _PRICING_CACHE is not None and mtime == _PRICING_MTIME:
        return _PRICING_CACHE
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        _PRICING_CACHE = data.get("providers", {}) if isinstance(data, dict) else {}
        _PRICING_MTIME = mtime
    except (OSError, ValueError):
        _PRICING_CACHE = _PRICING_CACHE or {}
    return _PRICING_CACHE


def _estimate_cost(provider: str, model: str, prompt_tokens: int, completion_tokens: int) -> float:
    pricing = _load_pricing()
    rates = pricing.get(provider, {}).get(model)
    if not rates:
        return 0.0
    return round(
        (prompt_tokens / 1_000_000) * rates.get("prompt", 0.0)
        + (completion_tokens / 1_000_000) * rates.get("completion", 0.0),
        6,
    )


def record(
    user_id: Optional[str],
    agent_name: str,
    provider: str,
    model: str,
    usage: Optional[Dict[str, Any]],
) -> None:
    """Persist one LLM round-trip's usage. Safe to call with usage=None
    (provider didn't return a usage block — nothing recorded)."""
    if not usage:
        return
    pt = int(usage.get("prompt_tokens") or 0)
    ct = int(usage.get("completion_tokens") or 0)
    tt = int(usage.get("total_tokens") or (pt + ct))
    cost = _estimate_cost(provider, model, pt, ct)
    init_db()
    with _LOCK, _connect() as conn:
        conn.execute(
            "INSERT INTO runs (id, user_id, agent_name, provider, model, "
            "prompt_tokens, completion_tokens, total_tokens, estimated_cost_usd, "
            "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (str(uuid.uuid4()), user_id or "", agent_name or "",
             provider, model, pt, ct, tt, cost,
             datetime.now(timezone.utc).isoformat()),
        )


def daily_total(user_id: Optional[str], day: Optional[str] = None) -> Dict[str, float]:
    """Return ``{tokens, cost_usd}`` for ``user_id`` on ``day`` (UTC date,
    default = today). ``user_id=None`` aggregates across all users."""
    if day is None:
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    init_db()
    with _LOCK, _connect() as conn:
        if user_id is None:
            row = conn.execute(
                "SELECT COALESCE(SUM(total_tokens),0) AS t, "
                "COALESCE(SUM(estimated_cost_usd),0.0) AS c "
                "FROM runs WHERE substr(created_at,1,10) = ?",
                (day,),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT COALESCE(SUM(total_tokens),0) AS t, "
                "COALESCE(SUM(estimated_cost_usd),0.0) AS c "
                "FROM runs WHERE user_id = ? AND substr(created_at,1,10) = ?",
                (user_id, day),
            ).fetchone()
    return {"tokens": int(row["t"]), "cost_usd": float(row["c"])}


def is_over_cap(user_id: Optional[str], cap_usd: float) -> bool:
    """True iff today's spend for ``user_id`` already meets or exceeds
    ``cap_usd``. Caller is expected to short-circuit upstream."""
    if cap_usd is None or cap_usd <= 0:
        return False
    return daily_total(user_id)["cost_usd"] >= cap_usd


def history(user_id: Optional[str], days: int = 7) -> List[Dict[str, Any]]:
    """Return per-day totals + breakdowns for the last ``days`` days."""
    init_db()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
    with _LOCK, _connect() as conn:
        if user_id is None:
            rows = conn.execute(
                "SELECT substr(created_at,1,10) AS day, agent_name, provider, model, "
                "SUM(total_tokens) AS tokens, SUM(estimated_cost_usd) AS cost "
                "FROM runs WHERE substr(created_at,1,10) >= ? "
                "GROUP BY day, agent_name, provider, model "
                "ORDER BY day DESC",
                (cutoff,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT substr(created_at,1,10) AS day, agent_name, provider, model, "
                "SUM(total_tokens) AS tokens, SUM(estimated_cost_usd) AS cost "
                "FROM runs WHERE user_id = ? AND substr(created_at,1,10) >= ? "
                "GROUP BY day, agent_name, provider, model "
                "ORDER BY day DESC",
                (user_id, cutoff),
            ).fetchall()
    return [
        {
            "day": r["day"],
            "agent_name": r["agent_name"],
            "provider": r["provider"],
            "model": r["model"],
            "tokens": int(r["tokens"]),
            "cost_usd": round(float(r["cost"]), 6),
        }
        for r in rows
    ]
