"""Saved workflows — Roadmap V2 · Epic 7 (power-user chaining).

The chain mechanism already works but was JSON/dev-only. This makes chains
first-class: name them, save them, re-run them. SQLite-backed (shares the
projects DB), optionally scoped to a project.
"""

import json
import os
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

_lock = threading.Lock()
_initialized = False


def _db_path() -> str:
    data_dir = os.getenv("DATA_DIR", "./data")
    try:
        os.makedirs(data_dir, exist_ok=True)
    except OSError:
        import tempfile
        data_dir = tempfile.gettempdir()
    return os.path.join(data_dir, "projects.db")


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path())
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    global _initialized
    with _lock, _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS workflows (
                id         TEXT PRIMARY KEY,
                name       TEXT NOT NULL,
                project_id TEXT,
                steps      TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
    _initialized = True


def _ensure() -> None:
    if not _initialized:
        init_db()


def _row(r: sqlite3.Row) -> Dict[str, Any]:
    w = dict(r)
    w["steps"] = json.loads(w["steps"])
    return w


def save_workflow(
    name: str, steps: List[Dict[str, Any]], project_id: Optional[str] = None
) -> Dict[str, Any]:
    _ensure()
    wid = str(uuid.uuid4())[:8]
    with _lock, _connect() as conn:
        conn.execute(
            "INSERT INTO workflows (id, name, project_id, steps, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (wid, name, project_id, json.dumps(steps),
             datetime.now(timezone.utc).isoformat()),
        )
    return get_workflow(wid)


def get_workflow(workflow_id: str) -> Optional[Dict[str, Any]]:
    _ensure()
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM workflows WHERE id = ?", (workflow_id,)
        ).fetchone()
    return _row(row) if row else None


def list_workflows(project_id: Optional[str] = None) -> List[Dict[str, Any]]:
    _ensure()
    with _connect() as conn:
        if project_id:
            rows = conn.execute(
                "SELECT * FROM workflows WHERE project_id = ? "
                "ORDER BY created_at DESC", (project_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM workflows ORDER BY created_at DESC"
            ).fetchall()
    return [_row(r) for r in rows]


def delete_workflow(workflow_id: str) -> bool:
    _ensure()
    with _lock, _connect() as conn:
        cur = conn.execute(
            "DELETE FROM workflows WHERE id = ?", (workflow_id,)
        )
        return cur.rowcount > 0
