"""Stage 1 / Task 2 — chunks.knowledge_layer + chunks.authority columns.

Persisted on both the static ``RagChunk`` (legacy ``chunks``) and every
dynamic ``chunks_<ns>`` class (prod writes to ``chunks_v2``). Nullable so
pre-migration rows and the flag-off path read as unlayered. ``knowledge_layer``
is deliberately NOT named ``layer`` — the Chunk dataclass already uses ``layer``
for the per-query STEP 0 isolation tag.
"""
from app.core.models import RagChunk, make_rag_chunk_class


def test_ragchunk_has_knowledge_layer_and_authority():
    cols = {c.name for c in RagChunk.__table__.columns}
    assert "knowledge_layer" in cols
    assert "authority" in cols
    # must NOT clash with the STEP 0 retrieval isolation tag name
    assert "layer" not in cols


def test_layer_columns_are_nullable():
    by = {c.name: c for c in RagChunk.__table__.columns}
    assert by["knowledge_layer"].nullable is True
    assert by["authority"].nullable is True


def test_namespaced_chunk_class_also_carries_columns():
    # prod writes to chunks_v2 (RAG_VECTOR_NAMESPACE=v2), not legacy chunks
    cls = make_rag_chunk_class("v2", 256, "fake-model")
    cols = {c.name for c in cls.__table__.columns}
    assert "knowledge_layer" in cols
    assert "authority" in cols
