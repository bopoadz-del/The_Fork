"""Stage 1 / Task 2 — chunks.layer + chunks.authority persisted columns.

Nullable so pre-migration rows and the flag-off path read as unlayered.
"""
from app.core.models import RagChunk


def test_ragchunk_has_layer_and_authority():
    cols = {c.name for c in RagChunk.__table__.columns}
    assert "layer" in cols
    assert "authority" in cols


def test_layer_columns_are_nullable():
    by = {c.name: c for c in RagChunk.__table__.columns}
    assert by["layer"].nullable is True
    assert by["authority"].nullable is True
