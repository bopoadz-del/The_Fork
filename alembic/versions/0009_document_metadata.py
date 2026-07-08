"""Add metadata JSONB column to documents.

Revision ID: 0009
Revises: 0008
Create Date: 2026-07-08

Stores Drive provenance and other source metadata per document.
Postgres uses JSONB; SQLite gets the column from the ORM bootstrap,
so this migration is a no-op there.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        # SQLite dev/test databases are created by the ORM, which already
        # includes the metadata column. No Alembic-managed migration needed.
        return
    op.add_column(
        "documents",
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        return
    op.drop_column("documents", "metadata")
