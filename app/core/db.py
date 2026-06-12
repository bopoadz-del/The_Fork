"""SQLAlchemy database engine and session helpers.

Phase 1 foundation: single DATABASE_URL for the unified The Fork schema.
Stores are not ported yet — this module exists for Alembic and future
SQLAlchemy-backed stores.

Environment:
    DATABASE_URL  PostgreSQL (or other SQLAlchemy URL). When unset, falls back
                  to sqlite:///{DATA_DIR}/the_fork.db for local dev.
    DATA_DIR      Directory for the SQLite fallback file (default ./data).
"""

from __future__ import annotations

import os
from collections.abc import Generator
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

_DATA_DIR = os.getenv("DATA_DIR", "./data")


def _default_sqlite_url() -> str:
    db_path = os.path.join(_DATA_DIR, "the_fork.db")
    return f"sqlite:///{db_path}"


DATABASE_URL: str = os.getenv("DATABASE_URL") or _default_sqlite_url()

_is_postgres = DATABASE_URL.startswith("postgresql")

_engine_kwargs: dict[str, Any] = {}
if _is_postgres:
    _engine_kwargs["pool_size"] = 10
    _engine_kwargs["pool_pre_ping"] = True

engine = create_engine(DATABASE_URL, **_engine_kwargs)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency: yield a request-scoped SQLAlchemy session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
