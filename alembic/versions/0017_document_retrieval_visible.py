"""Retrieval visibility, supersede chain, and extractor version.

A documents row is archive identity. Hard-deleting it is forbidden. When a
stale extract is replaced by a corrected copy of the same bytes, the old row
stays and retrieval hides it: ``retrieval_visible = false`` and
``superseded_by`` points at the live id.

This is the first ingest-ledger flag that actually gates hybrid search
(BM25 and vector). ingest_status never did.

Live seed (no-op when either id is absent): the sparse letter extract
``b5033ec2`` is superseded by the corrected copy ``93982d45``. Reversible
by flipping the flag. See ``scripts/seed_d1_supersede.py``.

Revision ID: 0017
Revises: 0016
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None

# Opaque live ids. Do not expand into client names here or in the seed.
_STALE_ID = "b5033ec2"
_LIVE_ID = "93982d45"


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        # SQLite dev/test DBs are built by the ORM + _patch_legacy_columns.
        return

    op.execute(sa.text(
        "ALTER TABLE documents ADD COLUMN IF NOT EXISTS superseded_by TEXT"
    ))
    op.execute(sa.text(
        "ALTER TABLE documents DROP CONSTRAINT IF EXISTS "
        "fk_documents_superseded_by"
    ))
    op.execute(sa.text(
        "ALTER TABLE documents ADD CONSTRAINT fk_documents_superseded_by "
        "FOREIGN KEY (superseded_by) REFERENCES documents(id) ON DELETE SET NULL"
    ))
    op.execute(sa.text(
        "ALTER TABLE documents ADD COLUMN IF NOT EXISTS retrieval_visible "
        "BOOLEAN NOT NULL DEFAULT TRUE"
    ))
    op.execute(sa.text(
        "ALTER TABLE documents ADD COLUMN IF NOT EXISTS extractor_version TEXT"
    ))
    op.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS idx_documents_superseded_by "
        "ON documents (superseded_by)"
    ))
    op.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS idx_documents_retrieval_hidden "
        "ON documents (id) WHERE retrieval_visible IS FALSE"
    ))

    # Seed only when both rows exist. Missing ids must not fail deploy.
    op.execute(sa.text(
        "UPDATE documents SET superseded_by = :live, retrieval_visible = FALSE "
        "WHERE id = :stale AND EXISTS ("
        "  SELECT 1 FROM documents WHERE id = :live"
        ")"
    ).bindparams(live=_LIVE_ID, stale=_STALE_ID))


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        return
    op.execute(sa.text(
        "ALTER TABLE documents DROP CONSTRAINT IF EXISTS "
        "fk_documents_superseded_by"
    ))
    op.execute(sa.text(
        "DROP INDEX IF EXISTS idx_documents_retrieval_hidden"
    ))
    op.execute(sa.text(
        "DROP INDEX IF EXISTS idx_documents_superseded_by"
    ))
    for col in ("extractor_version", "retrieval_visible", "superseded_by"):
        op.execute(sa.text(f"ALTER TABLE documents DROP COLUMN IF EXISTS {col}"))
