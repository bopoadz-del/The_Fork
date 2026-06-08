"""Agent memory — persistent conversations, messages, and durable agent facts.

Phase C4 — Stream C: persistent agent memory.

SQLite-backed, stdlib only — no new dependency.
Mirrors the conventions in app/core/projects.py exactly.
"""

import os
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

_lock = threading.Lock()
_initialized = False


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _db_path() -> str:
    """Resolve the DB path from DATA_DIR at call time (so tests can relocate it)."""
    data_dir = os.getenv("DATA_DIR", "./data")
    try:
        os.makedirs(data_dir, exist_ok=True)
    except OSError:
        import tempfile
        data_dir = tempfile.gettempdir()
    return os.path.join(data_dir, "agent_memory.db")


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path())
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    """Create the schema if absent. Idempotent — safe to call on every startup."""
    global _initialized
    with _lock:
        with _connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS conversations (
                    id           TEXT PRIMARY KEY,
                    agent_name   TEXT NOT NULL,
                    project_id   TEXT,
                    title        TEXT,
                    created_at   TEXT NOT NULL,
                    updated_at   TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS messages (
                    id              TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                    role            TEXT NOT NULL,
                    content         TEXT NOT NULL,
                    created_at      TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_messages_conv
                    ON messages(conversation_id, created_at);
                CREATE TABLE IF NOT EXISTS agent_facts (
                    id              TEXT PRIMARY KEY,
                    agent_name      TEXT NOT NULL,
                    project_id      TEXT NOT NULL DEFAULT '',
                    conversation_id TEXT,
                    key             TEXT NOT NULL,
                    value           TEXT NOT NULL,
                    updated_at      TEXT NOT NULL,
                    UNIQUE(agent_name, project_id, key)
                );
                """
            )
            # Migrate a pre-existing agent_facts table that predates project
            # scoping (UNIQUE(agent_name, key), no project_id column). The
            # constraint change needs a table rebuild; legacy rows become
            # project-less ('').
            cols = {r[1] for r in conn.execute("PRAGMA table_info(agent_facts)")}
            if "project_id" not in cols:
                conn.executescript(
                    """
                    ALTER TABLE agent_facts RENAME TO _agent_facts_old;
                    CREATE TABLE agent_facts (
                        id              TEXT PRIMARY KEY,
                        agent_name      TEXT NOT NULL,
                        project_id      TEXT NOT NULL DEFAULT '',
                        conversation_id TEXT,
                        key             TEXT NOT NULL,
                        value           TEXT NOT NULL,
                        updated_at      TEXT NOT NULL,
                        UNIQUE(agent_name, project_id, key)
                    );
                    INSERT INTO agent_facts
                        (id, agent_name, project_id, conversation_id, key, value, updated_at)
                        SELECT id, agent_name, '', conversation_id, key, value, updated_at
                        FROM _agent_facts_old;
                    DROP TABLE _agent_facts_old;
                    """
                )
        _initialized = True


def _ensure_db() -> None:
    if not _initialized:
        init_db()


# ── conversations ────────────────────────────────────────────────────────────

def get_or_create_conversation(
    conversation_id: str,
    agent_name: str,
    project_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Return the existing conversation row or create it with the given id. Idempotent."""
    _ensure_db()
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM conversations WHERE id = ?", (conversation_id,)
        ).fetchone()
        if row:
            existing = dict(row)
            # Hygiene: backfill a NULL project_id when a real one is now known.
            # Never overwrite a non-NULL stored value (would re-tenant the row).
            if existing.get("project_id") is None and project_id is not None:
                with _lock:
                    conn.execute(
                        "UPDATE conversations SET project_id = ?, updated_at = ? "
                        "WHERE id = ? AND project_id IS NULL",
                        (project_id, _now(), conversation_id),
                    )
                    conn.commit()
                existing["project_id"] = project_id
            return existing
    # Not found — create it
    now = _now()
    with _lock, _connect() as conn:
        # Double-check inside the lock to guard against races
        row = conn.execute(
            "SELECT * FROM conversations WHERE id = ?", (conversation_id,)
        ).fetchone()
        if row:
            return dict(row)
        conn.execute(
            "INSERT INTO conversations (id, agent_name, project_id, title, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (conversation_id, agent_name, project_id, None, now, now),
        )
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM conversations WHERE id = ?", (conversation_id,)
        ).fetchone()
    return dict(row)


def get_conversation(conversation_id: str) -> Optional[Dict[str, Any]]:
    """Return the conversation row for the given id, or None if it does not exist."""
    _ensure_db()
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM conversations WHERE id = ?", (conversation_id,)
        ).fetchone()
    return dict(row) if row else None


def list_conversations(
    agent_name: Optional[str] = None,
    project_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    _ensure_db()
    query = "SELECT * FROM conversations WHERE 1=1"
    params: List[Any] = []
    if agent_name is not None:
        query += " AND agent_name = ?"
        params.append(agent_name)
    if project_id is not None:
        query += " AND project_id = ?"
        params.append(project_id)
    query += " ORDER BY updated_at DESC"
    with _connect() as conn:
        rows = conn.execute(query, params).fetchall()
    return [dict(r) for r in rows]


def delete_conversation(conversation_id: str) -> bool:
    _ensure_db()
    with _lock, _connect() as conn:
        cur = conn.execute(
            "DELETE FROM conversations WHERE id = ?", (conversation_id,)
        )
        return cur.rowcount > 0


def clear_conversation(conversation_id: str) -> Dict[str, int]:
    """Wipe the conversation's messages and agent_facts without dropping
    the conversation row itself. Used by the UI's "Clear history" button
    to escape a thread poisoned by prior hallucinated turns while keeping
    the conversation_id stable (so the React composer doesn't need to
    remount).

    Returns ``{"messages": N, "facts": M}`` so the caller can surface
    how much was removed. Idempotent — clearing an empty / nonexistent
    conversation returns zeros without raising.
    """
    _ensure_db()
    with _lock, _connect() as conn:
        msgs = conn.execute(
            "DELETE FROM messages WHERE conversation_id = ?",
            (conversation_id,),
        ).rowcount
        facts = conn.execute(
            "DELETE FROM agent_facts WHERE conversation_id = ?",
            (conversation_id,),
        ).rowcount
        # Bump updated_at so the UI can detect the clear via list_conversations.
        conn.execute(
            "UPDATE conversations SET updated_at = ? WHERE id = ?",
            (_now(), conversation_id),
        )
    return {"messages": int(msgs or 0), "facts": int(facts or 0)}


# ── messages ─────────────────────────────────────────────────────────────────

def append_message(conversation_id: str, role: str, content: str) -> Dict[str, Any]:
    """Insert a message and bump the conversation's updated_at."""
    _ensure_db()
    mid = str(uuid.uuid4())
    now = _now()
    with _lock, _connect() as conn:
        conn.execute(
            "INSERT INTO messages (id, conversation_id, role, content, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (mid, conversation_id, role, content, now),
        )
        conn.execute(
            "UPDATE conversations SET updated_at = ? WHERE id = ?",
            (now, conversation_id),
        )
    with _connect() as conn:
        row = conn.execute("SELECT * FROM messages WHERE id = ?", (mid,)).fetchone()
    return dict(row)


def get_messages(conversation_id: str, limit: int = 40) -> List[Dict[str, Any]]:
    """Return messages oldest-first. If limit is set, return the most recent `limit` rows, still oldest-first."""
    _ensure_db()
    with _connect() as conn:
        # Fetch the most recent `limit` rows by (created_at DESC, rowid DESC),
        # then re-order them oldest-first for the caller.
        rows = conn.execute(
            "SELECT id, conversation_id, role, content, created_at,"
            "       rowid AS _rid"
            " FROM messages WHERE conversation_id = ?"
            " ORDER BY created_at DESC, rowid DESC LIMIT ?",
            (conversation_id, limit),
        ).fetchall()
    # Reverse to get oldest-first
    rows = list(reversed(rows))
    return [
        {k: row[k] for k in ("id", "conversation_id", "role", "content", "created_at")}
        for row in rows
    ]


# ── agent facts ───────────────────────────────────────────────────────────────

def set_agent_fact(
    agent_name: str,
    key: str,
    value: str,
    conversation_id: Optional[str] = None,
    project_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Upsert a durable fact for an agent, scoped to a project.

    Facts are keyed by (agent_name, project_id, key) so a fact remembered in
    one project is not visible to the same agent in another project. A
    project-less conversation uses the '' scope.
    """
    _ensure_db()
    project_id = project_id or ""
    fid = str(uuid.uuid4())
    now = _now()
    with _lock, _connect() as conn:
        conn.execute(
            "INSERT INTO agent_facts "
            "(id, agent_name, project_id, conversation_id, key, value, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(agent_name, project_id, key) DO UPDATE SET "
            "value=excluded.value, conversation_id=excluded.conversation_id, "
            "updated_at=excluded.updated_at",
            (fid, agent_name, project_id, conversation_id, key, value, now),
        )
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM agent_facts "
            "WHERE agent_name = ? AND project_id = ? AND key = ?",
            (agent_name, project_id, key),
        ).fetchone()
    return dict(row)


def list_agent_facts(
    agent_name: str, project_id: Optional[str] = None
) -> List[Dict[str, Any]]:
    """List an agent's facts for one project scope ('' = project-less)."""
    _ensure_db()
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM agent_facts "
            "WHERE agent_name = ? AND project_id = ? ORDER BY key",
            (agent_name, project_id or ""),
        ).fetchall()
    return [dict(r) for r in rows]
