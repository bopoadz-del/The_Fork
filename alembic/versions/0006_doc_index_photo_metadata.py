"""doc_index kind + photo_metadata + photos table

Revision ID: 0006
Revises: 0005
Create Date: 2026-06-24
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name

    if dialect == "postgresql":
        op.add_column(
            "doc_index",
            sa.Column("kind", sa.Text(), nullable=False, server_default=sa.text("'text'")),
        )
        op.add_column(
            "doc_index",
            sa.Column("photo_metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        )
        op.create_table(
            "photos",
            sa.Column("sha256", sa.Text(), primary_key=True),
            sa.Column("content_type", sa.Text(), nullable=False),
            sa.Column("size_bytes", sa.Integer(), nullable=False),
            sa.Column("bytes", postgresql.BYTEA(), nullable=False),
            sa.Column("uploaded_at", sa.TIMESTAMP(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        )
    else:
        with op.batch_alter_table("doc_index") as batch:
            batch.add_column(sa.Column("kind", sa.Text(), nullable=False, server_default=sa.text("'text'")))
            batch.add_column(sa.Column("photo_metadata", sa.Text(), nullable=True))
        op.create_table(
            "photos",
            sa.Column("sha256", sa.Text(), primary_key=True),
            sa.Column("content_type", sa.Text(), nullable=False),
            sa.Column("size_bytes", sa.Integer(), nullable=False),
            sa.Column("bytes", sa.LargeBinary(), nullable=False),
            sa.Column("uploaded_at", sa.TIMESTAMP(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        )


def downgrade() -> None:
    op.drop_table("photos")
    bind = op.get_bind()
    dialect = bind.dialect.name
    if dialect == "postgresql":
        op.drop_column("doc_index", "photo_metadata")
        op.drop_column("doc_index", "kind")
    else:
        with op.batch_alter_table("doc_index") as batch:
            batch.drop_column("photo_metadata")
            batch.drop_column("kind")
