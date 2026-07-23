"""chunks: layer + authority columns (layered RAG stage 1)

Adds the persisted layer (L1/L2A/L2B/L3) and authority-scoring columns.
Both nullable so pre-migration rows and the RAG_LAYERED=off path read as
unlayered. See docs/superpowers/plans/2026-07-23-layered-rag.md.

Revision ID: 0011_chunks_layer_authority
Revises: 0010_projects_location
"""
from alembic import op
import sqlalchemy as sa

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("chunks", sa.Column("layer", sa.Text(), nullable=True))
    op.add_column("chunks", sa.Column("authority", sa.Text(), nullable=True))
    op.create_index("idx_chunks_layer", "chunks", ["layer"])


def downgrade() -> None:
    op.drop_index("idx_chunks_layer", table_name="chunks")
    op.drop_column("chunks", "authority")
    op.drop_column("chunks", "layer")
