"""Drop the retired first-generation ``chunks`` table. HELD for the owner's word.

``chunks`` (``vector(256)``, model2vec era) was retired in place when the
vector store moved to namespaced tables; production reads and writes
``chunks_v2`` (``RAG_VECTOR_NAMESPACE`` default ``v2``). Live 2026-10-03 the
table is empty (0 rows, 88 kB) but still carries a primary key, a unique
key, two duplicate knowledge-layer indexes, an HNSW and a GIN index.

Fails closed: the drop runs only when the table is EMPTY. A row in it means
something still writes there, and that writer has to be found first.

Code that must go with this migration (see the PR): the ``RagChunk`` model
(``app/core/models.py``), the legacy-table fallback in
``projects.delete_document``, the ``namespace=""`` -> ``chunks`` mapping in
``app/core/rag/vector_store.py``, and the tests and owner scripts that read
``chunks`` directly.

Revision ID: 0019
Revises: 0018
"""
from __future__ import annotations

import logging

import sqlalchemy as sa

from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None

log = logging.getLogger("alembic.0019")


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        return
    exists = bind.execute(sa.text("SELECT to_regclass('public.chunks') IS NOT NULL")).scalar()
    if not exists:
        return
    rows = bind.execute(sa.text("SELECT count(*) FROM chunks")).scalar()
    if rows:
        log.warning("0019: chunks holds %s rows -- not dropped; find the writer first", rows)
        return
    op.execute(sa.text("DROP TABLE chunks"))


def downgrade() -> None:
    """Recreate the table EMPTY, as it stood at 0018 (schema only)."""
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        return
    op.execute(sa.text(
        "CREATE TABLE IF NOT EXISTS chunks ("
        "  chunk_id TEXT PRIMARY KEY,"
        "  project_id TEXT REFERENCES projects (id) ON DELETE SET NULL,"
        "  doc_id TEXT NOT NULL REFERENCES documents (id) ON DELETE CASCADE,"
        "  chunk_index INTEGER NOT NULL,"
        "  text TEXT NOT NULL,"
        "  text_search tsvector GENERATED ALWAYS AS (to_tsvector('english'::regconfig, text)) STORED,"
        "  embedding vector(256) NOT NULL,"
        "  created_at TEXT NOT NULL,"
        "  knowledge_layer TEXT,"
        "  authority TEXT,"
        "  UNIQUE (project_id, doc_id, chunk_index)"
        ")"
    ))
    for ddl in (
        "CREATE INDEX IF NOT EXISTS idx_chunks_project ON chunks (project_id)",
        "CREATE INDEX IF NOT EXISTS idx_chunks_doc ON chunks (project_id, doc_id)",
        "CREATE INDEX IF NOT EXISTS idx_chunks_knowledge_layer ON chunks (knowledge_layer)",
        "CREATE INDEX IF NOT EXISTS chunks_fts_gin ON chunks USING gin (text_search)",
        "CREATE INDEX IF NOT EXISTS idx_chunks_embedding ON chunks USING hnsw (embedding vector_cosine_ops)",
    ):
        op.execute(sa.text(ddl))
