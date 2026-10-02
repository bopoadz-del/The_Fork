"""A pooled stem pair is not the whole query.

The lookup used to stop once any stem pair was already in the pool. A
query has more than one content constituent. A decoy that holds a pair
from the first, including a stem that only modifies the second, must
not count as the second constituent. That constituent is only in
another chunk, and that chunk has to enter the pool.

Synthetic chunks only.
"""
from __future__ import annotations

from app.core.rag import retriever
from app.core.rag.vector_store import Chunk

ASK = "Which north canopy and which tinted primer?"
DECOY = "The north canopy was tinted at the factory shed."
HELD = "The tinted primer sits on the shelf."


def _install(monkeypatch, semantic, lexical):
    monkeypatch.setenv("RAG_EMBEDDING_MODEL", "fake")
    monkeypatch.setenv("RAG_GENERAL_KNOWLEDGE_PROJECTS", "")

    def fake_search(self, project_id, qvec, k, query_text=None):
        return [c for c in semantic if c.project_id == project_id][:k]

    def fake_identifier_search(self, project_id, identifiers, k=20):
        out = []
        for chunk in lexical:
            if chunk.project_id != project_id:
                continue
            low = (chunk.text or "").lower()
            if any(
                all(tok in low for tok in ident.lower().split())
                for ident in identifiers
            ):
                out.append(chunk)
        return out[:k]

    monkeypatch.setattr(
        "app.core.rag.vector_store.VectorStore.search", fake_search,
    )
    monkeypatch.setattr(
        "app.core.rag.vector_store.VectorStore.identifier_search",
        fake_identifier_search,
    )
    monkeypatch.setattr(
        retriever, "_doc_name_for_id", lambda did: "synthetic-note.pdf",
        raising=False,
    )


def _chunk(cid, text, score):
    return Chunk(
        chunk_id=cid, project_id="p1", doc_id=f"d-{cid}", chunk_index=0,
        text=text, score=score,
    )


def test_a_pooled_stem_pair_does_not_cover_the_other_constituent(monkeypatch):
    decoy = _chunk("decoy", DECOY, 0.84)
    held = _chunk("held", HELD, 0.0)
    _install(monkeypatch, semantic=[decoy], lexical=[decoy, held])

    heads = retriever.content_constituent_heads(ASK)
    assert "primer" in heads
    assert "tinted" not in heads
    assert retriever.missing_constituent_heads(ASK, DECOY) == ["primer"]

    chunks, _noise = retriever.retrieve_with_filter(ASK, "p1", k=5)
    ids = [c.chunk_id for c in chunks]
    assert "decoy" in ids
    assert "held" in ids, ids
