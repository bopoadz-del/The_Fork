"""Regression: the chunks.project_id btree index must exist even when the
`chunks` table predates the index declaration.

Bug: `RagChunk.__table__.create(checkfirst=True)` skips ALL creation (indexes
included) when the table already exists. A prod `chunks` table created before
`idx_chunks_project` was declared therefore never got the btree, so
COUNT/filter-by-project seq-scanned the whole table (~11s on the master corpus,
while pgvector search stayed fast). _ensure_schema must create the indexes
explicitly and idempotently.
"""
from __future__ import annotations

import os
import shutil
import tempfile

import sqlalchemy as sa


def _index_names(url: str, table: str = "chunks") -> set:
    eng = sa.create_engine(url)
    try:
        insp = sa.inspect(eng)
        if table not in insp.get_table_names():
            return set()
        return {ix["name"] for ix in insp.get_indexes(table)}
    finally:
        eng.dispose()


def _dispose(url: str) -> None:
    """Release every engine that may hold the sqlite file open (Windows unlink)."""
    from app.core.rag import vector_store as vs
    from app.core import db as _db
    try:
        _db._engine_for_url(url).dispose()
    except Exception:
        pass
    vs._INITIALIZED_URLS.discard(url)


def test_ensure_schema_creates_project_index_on_fresh_db():
    from app.core.rag import vector_store as vs
    d = tempfile.mkdtemp()
    url = f"sqlite:///{os.path.join(d, 'fresh.db')}"
    try:
        vs._INITIALIZED_URLS.discard(url)
        vs._ensure_schema(url)
        names = _index_names(url)
        assert "idx_chunks_project" in names
        assert "idx_chunks_doc" in names
    finally:
        _dispose(url)
        shutil.rmtree(d, ignore_errors=True)


def test_index_created_on_preexisting_table_without_it():
    """The real prod case: the table already exists WITHOUT the index."""
    from app.core.rag import vector_store as vs
    d = tempfile.mkdtemp()
    url = f"sqlite:///{os.path.join(d, 'legacy.db')}"
    try:
        eng = sa.create_engine(url)
        with eng.begin() as conn:
            conn.execute(sa.text(
                "CREATE TABLE chunks (chunk_id TEXT PRIMARY KEY, project_id TEXT, "
                "doc_id TEXT, chunk_index INTEGER, text TEXT, embedding BLOB, created_at TEXT)"
            ))
        eng.dispose()
        assert "idx_chunks_project" not in _index_names(url)

        vs._INITIALIZED_URLS.discard(url)
        vs._ensure_schema(url)

        assert "idx_chunks_project" in _index_names(url), \
            "index must be created on a pre-existing table"

        # Idempotent: a second pass must not raise.
        vs._INITIALIZED_URLS.discard(url)
        vs._ensure_schema(url)
    finally:
        _dispose(url)
        shutil.rmtree(d, ignore_errors=True)
