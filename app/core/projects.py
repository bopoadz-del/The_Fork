"""Project entity — groups documents and gates project-level analytics.

Roadmap V2 · Part 0.1 (Project entity) + 0.2 (readiness gate).

A Project is the backbone the platform was missing: documents are no longer
processed in isolation, and project-level analytics (progress tracking, earned
value) stay inert until the project is genuinely set up.

SQLite-backed, stdlib only — no new dependency.
"""

import os
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

# ── document roles that feed the readiness gate ─────────────────────────────
ROLE_BASELINE = "baseline_schedule"
ROLE_DAILY = "daily_report"
ROLE_WEEKLY = "weekly_report"
ROLE_OTHER = "other"
VALID_ROLES = {ROLE_BASELINE, ROLE_DAILY, ROLE_WEEKLY, ROLE_OTHER}

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
    return os.path.join(data_dir, "projects.db")


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
                CREATE TABLE IF NOT EXISTS projects (
                    id               TEXT PRIMARY KEY,
                    name             TEXT NOT NULL,
                    client           TEXT,
                    status           TEXT NOT NULL DEFAULT 'active',
                    aconex_connected INTEGER NOT NULL DEFAULT 0,
                    user_id          TEXT NOT NULL DEFAULT 'system',
                    created_at       TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS documents (
                    id            TEXT PRIMARY KEY,
                    project_id    TEXT NOT NULL
                                  REFERENCES projects(id) ON DELETE CASCADE,
                    original_name TEXT NOT NULL,
                    stored_as     TEXT,
                    file_path     TEXT,
                    doc_type      TEXT NOT NULL DEFAULT 'document',
                    doc_role      TEXT NOT NULL DEFAULT 'other',
                    size          INTEGER NOT NULL DEFAULT 0,
                    uploaded_at   TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS project_facts (
                    id              TEXT PRIMARY KEY,
                    project_id      TEXT NOT NULL
                                    REFERENCES projects(id) ON DELETE CASCADE,
                    key             TEXT NOT NULL,
                    value           TEXT NOT NULL,
                    source_document TEXT,
                    confidence      REAL,
                    updated_at      TEXT NOT NULL,
                    UNIQUE(project_id, key)
                );
                """
            )
            # Migration for legacy DBs that don't have the user_id column yet.
            cols = [r[1] for r in conn.execute(
                "PRAGMA table_info(projects)"
            ).fetchall()]
            if "user_id" not in cols:
                conn.execute(
                    "ALTER TABLE projects ADD COLUMN user_id TEXT"
                )
                conn.execute(
                    "UPDATE projects SET user_id = 'system' WHERE user_id IS NULL"
                )
        _initialized = True


def _ensure_db() -> None:
    if not _initialized:
        init_db()


# ── classification ──────────────────────────────────────────────────────────

def classify_doc_type(filename: str) -> str:
    """Coarse document-type guess from the filename (display only)."""
    n = (filename or "").lower()
    _, ext = os.path.splitext(n)
    if ext in {".xer", ".mpp"} or "primavera" in n or "p6" in n:
        return "schedule"
    if ext == ".ifc":
        return "bim"
    if ext in {".jpg", ".jpeg", ".png", ".gif", ".webp"}:
        return "photo"
    if "boq" in n or "bill of quant" in n:
        return "boq"
    if "contract" in n or "agreement" in n:
        return "contract"
    if "spec" in n:
        return "specification"
    if "drawing" in n or ext == ".dwg":
        return "drawing"
    if "schedule" in n or "programme" in n or "program" in n:
        return "schedule"
    return "document"


def classify_doc_role(filename: str) -> str:
    """Map a filename to a readiness role. Conservative — defaults to 'other'."""
    n = (filename or "").lower()
    if "baseline" in n:
        return ROLE_BASELINE
    if "daily" in n:
        return ROLE_DAILY
    if "weekly" in n:
        return ROLE_WEEKLY
    return ROLE_OTHER


# ── projects ────────────────────────────────────────────────────────────────

def create_project(name: str, client: Optional[str] = None, user_id: str = "system") -> Dict[str, Any]:
    _ensure_db()
    pid = str(uuid.uuid4())[:8]
    with _lock, _connect() as conn:
        conn.execute(
            "INSERT INTO projects (id, name, client, status, aconex_connected, user_id, created_at) "
            "VALUES (?, ?, ?, 'active', 0, ?, ?)",
            (pid, name, client, user_id, _now()),
        )
    return get_project(pid)


def list_projects(user_id: Optional[str] = None) -> List[Dict[str, Any]]:
    _ensure_db()
    with _connect() as conn:
        if user_id is not None:
            rows = conn.execute(
                "SELECT * FROM projects WHERE user_id = ? ORDER BY created_at DESC",
                (user_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM projects ORDER BY created_at DESC"
            ).fetchall()
    out = []
    for r in rows:
        p = dict(r)
        p["aconex_connected"] = bool(p["aconex_connected"])
        p["readiness"] = compute_readiness(p["id"])
        out.append(p)
    return out


def get_project(project_id: str, user_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    _ensure_db()
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM projects WHERE id = ?", (project_id,)
        ).fetchone()
    if not row:
        return None
    if user_id is not None and row["user_id"] != user_id:
        return None
    proj = dict(row)
    proj["aconex_connected"] = bool(proj["aconex_connected"])
    proj["documents"] = list_documents(project_id)
    proj["readiness"] = compute_readiness(project_id)
    return proj


def project_owner(project_id: str) -> Optional[str]:
    """Return the user_id that owns the project, or None if the project doesn't exist."""
    _ensure_db()
    with _connect() as conn:
        row = conn.execute(
            "SELECT user_id FROM projects WHERE id = ?", (project_id,)
        ).fetchone()
    return row["user_id"] if row else None


def delete_project(project_id: str) -> bool:
    """Delete a project and (via ON DELETE CASCADE) all its document rows."""
    _ensure_db()
    with _lock, _connect() as conn:
        cur = conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
        return cur.rowcount > 0


def set_aconex(project_id: str, connected: bool) -> bool:
    """Set the Aconex connection flag. Stub for the full connector (Roadmap V2)."""
    _ensure_db()
    with _lock, _connect() as conn:
        cur = conn.execute(
            "UPDATE projects SET aconex_connected = ? WHERE id = ?",
            (1 if connected else 0, project_id),
        )
        return cur.rowcount > 0


# ── documents ───────────────────────────────────────────────────────────────

def add_document(
    project_id: str,
    original_name: str,
    stored_as: Optional[str] = None,
    file_path: Optional[str] = None,
    size: int = 0,
    role: Optional[str] = None,
) -> Dict[str, Any]:
    """Register a document under a project. Storing only — runs no analysis."""
    _ensure_db()
    did = str(uuid.uuid4())[:8]
    doc_type = classify_doc_type(original_name)
    doc_role = role if role in VALID_ROLES else classify_doc_role(original_name)
    with _lock, _connect() as conn:
        conn.execute(
            "INSERT INTO documents "
            "(id, project_id, original_name, stored_as, file_path, doc_type, "
            " doc_role, size, uploaded_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (did, project_id, original_name, stored_as, file_path,
             doc_type, doc_role, size, _now()),
        )
    with _connect() as conn:
        row = conn.execute("SELECT * FROM documents WHERE id = ?", (did,)).fetchone()
    return dict(row)


def list_documents(project_id: str) -> List[Dict[str, Any]]:
    _ensure_db()
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM documents WHERE project_id = ? ORDER BY uploaded_at",
            (project_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_document(doc_id: str) -> Optional[Dict[str, Any]]:
    _ensure_db()
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM documents WHERE id = ?", (doc_id,)
        ).fetchone()
    return dict(row) if row else None


def delete_document(doc_id: str) -> Optional[Dict[str, Any]]:
    """Delete a document row. Returns the deleted row (so the caller can
    purge the file from disk), or None if it did not exist."""
    doc = get_document(doc_id)
    if not doc:
        return None
    with _lock, _connect() as conn:
        conn.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
    return doc


def purge_documents_older_than(days: int) -> List[Dict[str, Any]]:
    """Delete document rows older than `days`. Returns the purged rows
    (Roadmap V2 · Epic 6 — data retention)."""
    _ensure_db()
    from datetime import timedelta
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM documents WHERE uploaded_at < ?", (cutoff,)
        ).fetchall()
        purged = [dict(r) for r in rows]
    with _lock, _connect() as conn:
        conn.execute("DELETE FROM documents WHERE uploaded_at < ?", (cutoff,))
    return purged


# ── readiness gate (Roadmap V2 · 0.2) ───────────────────────────────────────

def compute_readiness(project_id: str) -> Dict[str, Any]:
    """A project is 'ready' for progress tracking only once it has a baseline
    schedule, at least one daily and one weekly report, and Aconex connected."""
    docs = list_documents(project_id)
    roles = [d["doc_role"] for d in docs]
    with _connect() as conn:
        row = conn.execute(
            "SELECT aconex_connected FROM projects WHERE id = ?", (project_id,)
        ).fetchone()
    aconex = bool(row["aconex_connected"]) if row else False

    baseline = ROLE_BASELINE in roles
    daily = roles.count(ROLE_DAILY)
    weekly = roles.count(ROLE_WEEKLY)

    missing: List[str] = []
    if not baseline:
        missing.append("baseline_schedule")
    if daily < 1:
        missing.append("daily_reports")
    if weekly < 1:
        missing.append("weekly_reports")
    if not aconex:
        missing.append("aconex")

    return {
        "baseline_schedule": baseline,
        "daily_reports": daily,
        "weekly_reports": weekly,
        "aconex_connected": aconex,
        "ready": not missing,
        "missing": missing,
    }


# ── project memory / durable facts (Roadmap V2 · Epic 3) ────────────────────

def set_fact(
    project_id: str,
    key: str,
    value: str,
    source_document: Optional[str] = None,
    confidence: Optional[float] = None,
) -> Optional[Dict[str, Any]]:
    """Upsert a durable fact for a project (one row per project+key)."""
    _ensure_db()
    fid = str(uuid.uuid4())[:8]
    with _lock, _connect() as conn:
        conn.execute(
            "INSERT INTO project_facts "
            "(id, project_id, key, value, source_document, confidence, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(project_id, key) DO UPDATE SET "
            "value=excluded.value, source_document=excluded.source_document, "
            "confidence=excluded.confidence, updated_at=excluded.updated_at",
            (fid, project_id, key, str(value), source_document, confidence, _now()),
        )
    return get_fact(project_id, key)


def get_fact(project_id: str, key: str) -> Optional[Dict[str, Any]]:
    _ensure_db()
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM project_facts WHERE project_id = ? AND key = ?",
            (project_id, key),
        ).fetchone()
    return dict(row) if row else None


def list_facts(project_id: str) -> List[Dict[str, Any]]:
    _ensure_db()
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM project_facts WHERE project_id = ? ORDER BY key",
            (project_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def search_facts(project_id: str, query: str) -> List[Dict[str, Any]]:
    """Keyword search over fact keys + values (case-insensitive, any-term)."""
    facts = list_facts(project_id)
    terms = (query or "").lower().split()
    if not terms:
        return facts
    return [
        f for f in facts
        if any(t in f"{f['key']} {f['value']}".lower() for t in terms)
    ]


def delete_fact(project_id: str, key: str) -> bool:
    _ensure_db()
    with _lock, _connect() as conn:
        cur = conn.execute(
            "DELETE FROM project_facts WHERE project_id = ? AND key = ?",
            (project_id, key),
        )
        return cur.rowcount > 0
