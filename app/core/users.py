"""User accounts store — Stream A (User Accounts & Multi-Tenancy).

SQLite-backed, stdlib only. Mirrors app/core/projects.py DB conventions.
A singleton 'system' user is auto-created; legacy API keys resolve to it.
"""
import hashlib
import hmac
import os
import secrets
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

SYSTEM_USER_ID = "system"

_lock = threading.Lock()
_initialized = False

_PBKDF2_ITERATIONS = 240_000


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
    return os.path.join(data_dir, "users.db")


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path())
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    """Create the users schema if absent; auto-create the system user."""
    global _initialized
    with _lock:
        with _connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id            TEXT PRIMARY KEY,
                    email         TEXT NOT NULL UNIQUE,
                    password_hash TEXT,
                    salt          TEXT,
                    display_name  TEXT,
                    role          TEXT NOT NULL DEFAULT 'user',
                    created_at    TEXT NOT NULL
                );
                """
            )
            conn.execute(
                "INSERT OR IGNORE INTO users "
                "(id, email, password_hash, salt, display_name, role, created_at) "
                "VALUES (?, ?, NULL, NULL, ?, 'admin', ?)",
                (SYSTEM_USER_ID, "system@local", "System", _now()),
            )
        _initialized = True


def _ensure_db() -> None:
    if not _initialized:
        init_db()


def get_user_by_id(user_id: str) -> Optional[Dict[str, Any]]:
    _ensure_db()
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE id = ?", (user_id,)
        ).fetchone()
    return dict(row) if row else None


def get_user_by_email(email: str) -> Optional[Dict[str, Any]]:
    _ensure_db()
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE email = ?", ((email or "").lower(),)
        ).fetchone()
    return dict(row) if row else None


# ── password hashing (PBKDF2-HMAC-SHA256, stdlib only) ──────────────────────

def hash_password(password: str, salt: Optional[str] = None) -> Dict[str, str]:
    """Return {'salt', 'hash'} using PBKDF2-HMAC-SHA256 (stdlib, no native deps)."""
    if salt is None:
        salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"),
        bytes.fromhex(salt), _PBKDF2_ITERATIONS,
    )
    return {"salt": salt, "hash": dk.hex()}


def verify_password(password: str, password_hash: str, salt: str) -> bool:
    if not password_hash or not salt:
        return False
    candidate = hash_password(password, salt)["hash"]
    return hmac.compare_digest(candidate, password_hash)


def _public(row: Dict[str, Any]) -> Dict[str, Any]:
    """Strip secret columns from a user row before returning to callers."""
    return {k: v for k, v in row.items() if k not in ("password_hash", "salt")}


def create_user(
    email: str, password: str, display_name: Optional[str] = None,
    role: str = "user",
) -> Dict[str, Any]:
    _ensure_db()
    email = (email or "").strip().lower()
    if not email:
        raise ValueError("email is required")
    if not password:
        raise ValueError("password is required")
    if get_user_by_email(email) is not None:
        raise ValueError(f"email '{email}' is already registered")
    uid = str(uuid.uuid4())[:8]
    creds = hash_password(password)
    with _lock, _connect() as conn:
        conn.execute(
            "INSERT INTO users (id, email, password_hash, salt, "
            "display_name, role, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (uid, email, creds["hash"], creds["salt"],
             display_name or email, role, _now()),
        )
    return _public(get_user_by_id(uid))
