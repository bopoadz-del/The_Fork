# tests/test_migration_0006_doc_index_photo.py
"""Round-trip migration 0006 (SQLite branch) — isolated test.

Migrations 0001/0002 are Postgres-only (CREATE EXTENSION, pg_catalog queries)
so the full Alembic chain cannot run on SQLite. Instead each test:
  1. Creates a fresh SQLite file and points DATABASE_URL at it via monkeypatch.
  2. Creates the minimal pre-0006 schema directly (the tables Alembic would have
     produced up to 0005 on Postgres).
  3. Stamps the alembic_version table to '0005' so Alembic thinks it's there.
  4. Runs upgrade/downgrade against 0006 only, exercising the SQLite branch.

The Postgres branch (JSONB/BYTEA) is exercised by the CI postgres job which
runs command.upgrade(cfg, "head") via test_migrate_sqlite_to_pg.py::pg_engine.
"""
from __future__ import annotations

import importlib
import os
import tempfile

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text


@pytest.fixture
def sqlite_db(monkeypatch, tmp_path):
    """Temp SQLite file with DATABASE_URL pointing at it; yields (url, engine)."""
    db_path = tmp_path / "test_0006.db"
    url = f"sqlite:///{db_path}"
    monkeypatch.setenv("DATABASE_URL", url)
    import app.core.db as db_mod
    importlib.reload(db_mod)

    engine = create_engine(url)

    # Build the minimal schema that Alembic migrations 0001-0005 would have
    # produced on Postgres, but simplified for SQLite (no vector, no GIN).
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                email TEXT NOT NULL UNIQUE,
                password_hash TEXT,
                salt TEXT,
                display_name TEXT,
                role TEXT NOT NULL DEFAULT 'user',
                created_at TEXT NOT NULL
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS projects (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                client TEXT,
                status TEXT NOT NULL DEFAULT 'active',
                aconex_connected INTEGER NOT NULL DEFAULT 0,
                user_id TEXT NOT NULL DEFAULT 'system',
                created_at TEXT NOT NULL,
                is_approved INTEGER NOT NULL DEFAULT 1,
                origin TEXT NOT NULL DEFAULT 'user_create'
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS doc_index (
                project_id TEXT PRIMARY KEY,
                doc_id TEXT NOT NULL,
                chunk_index INTEGER NOT NULL,
                content TEXT NOT NULL
            )
        """))
        # Stamp the alembic_version table so Alembic thinks 0005 is applied.
        conn.execute(text(
            "CREATE TABLE IF NOT EXISTS alembic_version (version_num TEXT NOT NULL)"
        ))
        conn.execute(text("INSERT INTO alembic_version VALUES ('0005')"))

    yield url, engine

    engine.dispose()
    # Restore db_mod so stale URL doesn't leak into the next test.
    importlib.reload(db_mod)


@pytest.fixture
def alembic_cfg(sqlite_db):
    url, _ = sqlite_db
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


def test_upgrade_adds_kind_column_and_photos_table(sqlite_db, alembic_cfg):
    url, _ = sqlite_db
    command.upgrade(alembic_cfg, "0006")
    engine = create_engine(url)
    insp = inspect(engine)
    doc_index_cols = {c["name"] for c in insp.get_columns("doc_index")}
    assert "kind" in doc_index_cols
    assert "photo_metadata" in doc_index_cols
    assert "photos" in insp.get_table_names()
    photos_cols = {c["name"] for c in insp.get_columns("photos")}
    assert photos_cols >= {"sha256", "content_type", "size_bytes", "bytes", "uploaded_at"}
    engine.dispose()


def test_upgrade_backfills_kind_text_for_existing_rows(sqlite_db, alembic_cfg):
    url, engine = sqlite_db
    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO doc_index (project_id, doc_id, chunk_index, content)"
            " VALUES (:p, :d, :i, :c)"
        ), {"p": "test", "d": "doc1", "i": 0, "c": "hello"})
    command.upgrade(alembic_cfg, "0006")
    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT kind FROM doc_index WHERE doc_id = 'doc1'")
        ).fetchone()
    assert row[0] == "text"


def test_downgrade_drops_photos_table(sqlite_db, alembic_cfg):
    url, _ = sqlite_db
    command.upgrade(alembic_cfg, "0006")
    command.downgrade(alembic_cfg, "0005")
    engine = create_engine(url)
    insp = inspect(engine)
    assert "photos" not in insp.get_table_names()
    doc_index_cols = {c["name"] for c in insp.get_columns("doc_index")}
    assert "kind" not in doc_index_cols
    assert "photo_metadata" not in doc_index_cols
    engine.dispose()
