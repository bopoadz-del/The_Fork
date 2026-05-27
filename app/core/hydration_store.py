"""Hydration store — persistent record of nightly hydration runs.

Stores one row per (date, scope, project) tuple. ``scope`` is ``project`` for
per-project summaries and ``global`` for the cross-tenant rollup. Querying is
cheap: the index on (scope, project_id, run_date DESC) makes ``latest`` and
``history`` lookups direct.

DB lives at ``$DATA_DIR/hydration.db`` (default ``./data/hydration.db``) so it
follows the same convention as ``agent_memory.db`` and ``doc_index.db``.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


_lock = threading.Lock()


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _data_dir() -> str:
    return os.getenv("DATA_DIR", "./data")


def _db_path() -> str:
    return os.path.join(_data_dir(), "hydration.db")


def _connect() -> sqlite3.Connection:
    os.makedirs(_data_dir(), exist_ok=True)
    conn = sqlite3.connect(_db_path())
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    """Idempotent schema creation. Safe to call on every app startup."""
    with _lock, _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS hydration_runs (
                id TEXT PRIMARY KEY,
                run_date TEXT NOT NULL,
                scope TEXT NOT NULL CHECK(scope IN ('project','global')),
                project_id TEXT,
                summary_md TEXT NOT NULL,
                facts_json TEXT NOT NULL,
                provider TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_hydration_lookup "
            "ON hydration_runs(scope, project_id, run_date DESC)"
        )


def _ensure_db() -> None:
    if not os.path.exists(_db_path()):
        init_db()


def record_run(
    run_date: str,
    scope: str,
    project_id: Optional[str],
    summary_md: str,
    facts: Dict[str, Any],
    provider: str,
) -> str:
    """Insert one hydration row. Returns the generated id.

    ``scope`` must be ``"project"`` (project_id required) or ``"global"``
    (project_id should be None). The store does not enforce uniqueness on
    (run_date, scope, project_id) — re-running the same date appends a new
    row so audit history is preserved; ``get_latest`` returns the newest by
    created_at.
    """
    if scope not in ("project", "global"):
        raise ValueError(f"invalid scope: {scope!r}")
    if scope == "project" and not project_id:
        raise ValueError("project scope requires project_id")
    rid = str(uuid.uuid4())
    _ensure_db()
    with _lock, _connect() as conn:
        conn.execute(
            "INSERT INTO hydration_runs "
            "(id, run_date, scope, project_id, summary_md, facts_json, provider, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                rid,
                run_date,
                scope,
                project_id if scope == "project" else None,
                summary_md,
                json.dumps(facts, ensure_ascii=False),
                provider,
                _now_iso(),
            ),
        )
    return rid


def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    d = dict(row)
    try:
        d["facts"] = json.loads(d.pop("facts_json"))
    except Exception:
        d["facts"] = {}
    return d


def get_latest(scope: str, project_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Return the most recently inserted run for the given scope/project."""
    _ensure_db()
    with _connect() as conn:
        if scope == "global":
            row = conn.execute(
                "SELECT * FROM hydration_runs WHERE scope='global' "
                "ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT * FROM hydration_runs WHERE scope='project' AND project_id = ? "
                "ORDER BY created_at DESC LIMIT 1",
                (project_id,),
            ).fetchone()
    return _row_to_dict(row) if row else None


def list_history(
    scope: Optional[str] = None,
    project_id: Optional[str] = None,
    limit: int = 20,
) -> List[Dict[str, Any]]:
    _ensure_db()
    q = "SELECT * FROM hydration_runs WHERE 1=1"
    params: List[Any] = []
    if scope is not None:
        q += " AND scope = ?"
        params.append(scope)
    if project_id is not None:
        q += " AND project_id = ?"
        params.append(project_id)
    q += " ORDER BY created_at DESC LIMIT ?"
    params.append(max(1, min(int(limit), 200)))
    with _connect() as conn:
        rows = conn.execute(q, params).fetchall()
    return [_row_to_dict(r) for r in rows]
