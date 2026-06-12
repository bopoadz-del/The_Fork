"""User accounts store — Stream A (User Accounts & Multi-Tenancy).

SQLAlchemy-backed via app.core.db. Mirrors legacy store API.
A singleton 'system' user is auto-created; legacy API keys resolve to it.
"""
import hashlib
import hmac
import os
import secrets
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.db import SessionLocal, engine, get_database_url
from app.core.models import User

SYSTEM_USER_ID = "system"

_lock = threading.Lock()
_initialized = False

_PBKDF2_ITERATIONS = 240_000


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_sqlite_parent_dir() -> None:
    url = get_database_url()
    if url.startswith("sqlite:///"):
        parent = os.path.dirname(url[len("sqlite:///") :])
        if parent:
            os.makedirs(parent, exist_ok=True)


def _as_dict(user: User) -> Dict[str, Any]:
    return {
        "id": user.id,
        "email": user.email,
        "password_hash": user.password_hash,
        "salt": user.salt,
        "display_name": user.display_name,
        "role": user.role,
        "created_at": user.created_at,
    }


def init_db() -> None:
    """Create the users schema if absent; auto-create the system user."""
    global _initialized
    with _lock:
        _ensure_sqlite_parent_dir()
        User.__table__.create(bind=engine, checkfirst=True)
        with SessionLocal() as session:
            if session.get(User, SYSTEM_USER_ID) is None:
                session.add(
                    User(
                        id=SYSTEM_USER_ID,
                        email="system@local",
                        password_hash=None,
                        salt=None,
                        display_name="System",
                        role="admin",
                        created_at=_now(),
                    )
                )
                session.commit()
        _initialized = True


def _ensure_db() -> None:
    if not _initialized:
        init_db()


def get_user_by_id(user_id: str) -> Optional[Dict[str, Any]]:
    _ensure_db()
    with SessionLocal() as session:
        user = session.get(User, user_id)
    return _as_dict(user) if user else None


def get_user_by_email(email: str) -> Optional[Dict[str, Any]]:
    _ensure_db()
    with SessionLocal() as session:
        user = session.scalars(
            select(User).where(User.email == (email or "").lower())
        ).one_or_none()
    return _as_dict(user) if user else None


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
    with _lock:
        session = SessionLocal()
        try:
            session.add(
                User(
                    id=uid,
                    email=email,
                    password_hash=creds["hash"],
                    salt=creds["salt"],
                    display_name=display_name or email,
                    role=role,
                    created_at=_now(),
                )
            )
            session.commit()
        except IntegrityError:
            session.rollback()
            raise ValueError(f"email '{email}' is already registered")
        finally:
            session.close()
    return _public(get_user_by_id(uid))
