"""SQLAlchemy database engine and session helpers.

Phase 1 foundation: single DATABASE_URL for the unified The Fork schema.

Environment:
    DATABASE_URL  PostgreSQL (or other SQLAlchemy URL). When unset, falls back
                  to sqlite:///{DATA_DIR}/the_fork.db for local dev.
    DATA_DIR      Directory for the SQLite fallback file (default ./data).
"""

from __future__ import annotations

import os
from collections.abc import Generator
from functools import lru_cache
from typing import Any

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker


def _data_dir() -> str:
    return os.getenv("DATA_DIR", "./data")


def get_database_url() -> str:
    """Resolve DATABASE_URL from env, honoring DATA_DIR at call time."""
    explicit = os.getenv("DATABASE_URL")
    if explicit:
        return explicit
    db_path = os.path.join(_data_dir(), "the_fork.db")
    return f"sqlite:///{db_path}"


# Evaluated at import for Alembic; runtime code should call get_database_url().
DATABASE_URL: str = get_database_url()


def _engine_kwargs(url: str) -> dict[str, Any]:
    if url.startswith("postgresql"):
        return {"pool_size": 10, "pool_pre_ping": True}
    if url.startswith("sqlite"):
        return {"connect_args": {"timeout": 30.0}}
    return {}


@lru_cache(maxsize=8)
def _engine_for_url(url: str) -> Engine:
    return create_engine(url, **_engine_kwargs(url))


@event.listens_for(Engine, "connect")
def _sqlite_enable_foreign_keys(dbapi_conn: Any, _connection_record: Any) -> None:
    if dbapi_conn.__class__.__module__ == "sqlite3":
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


def get_engine() -> Engine:
    """Return a cached engine for the current DATABASE_URL / DATA_DIR."""
    return _engine_for_url(get_database_url())


class _LazyEngine:
    """Proxy so ``from app.core.db import engine`` tracks DATA_DIR changes."""

    def __getattr__(self, name: str) -> Any:
        return getattr(get_engine(), name)

    def __repr__(self) -> str:
        return repr(get_engine())


engine = _LazyEngine()  # type: ignore[assignment]


@lru_cache(maxsize=8)
def _session_factory_for_url(url: str) -> sessionmaker[Session]:
    return sessionmaker(autocommit=False, autoflush=False, bind=_engine_for_url(url))


def SessionLocal() -> Session:
    """Open a new SQLAlchemy session bound to the current database URL."""
    return _session_factory_for_url(get_database_url())()


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency: yield a request-scoped SQLAlchemy session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
