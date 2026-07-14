"""Tests for identifier-aware RAG retrieval boost.

The retriever must surface chunks that contain exact construction
reference identifiers (VO/RFI/NCR/PRC/drawing codes/etc.) above
semantically-similar boilerplate that lacks the requested identifier.
"""
from __future__ import annotations

import pytest


@pytest.fixture
def isolated_store(tmp_path, monkeypatch):
    """Fresh vector store + fake embedder."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("RAG_EMBEDDING_MODEL", "fake")
    monkeypatch.setenv("RAG_GENERAL_KNOWLEDGE_PROJECTS", "")
    from app.core.rag import embeddings as _emb, vector_store as _vs
    _emb.reset_embedder_cache()
    _vs.reset_store_cache()
    from app.core.rag.embeddings import Embedder
    from app.core.rag.vector_store import get_store
    from sqlalchemy import delete as _sa_delete
    e = Embedder(model_name="fake")
    # Use the default project database path (honours DATA_DIR) so that
    # retriever.get_store() returns the same cached instance.
    store = get_store(dim=e.dim)
    # On PostgreSQL, DATA_DIR does not change the database URL, so rows
    # written by earlier tests survive into this test's store. Truncate the
    # chunk table up-front to guarantee isolation regardless of backend.
    with store._lock:
        with store._session_factory()() as session:
            session.execute(_sa_delete(store._rag_chunk_cls))
            session.commit()
    yield store, e
    _emb.reset_embedder_cache()
    _vs.reset_store_cache()


def test_extract_identifiers_detects_common_reference_patterns():
    from app.core.rag.retriever import extract_query_identifiers

    ids = extract_query_identifiers('What is the status of VO Ref 31?')
    assert any("vo" in i and "31" in i for i in ids)

    ids = extract_query_identifiers('Is APPROVED valid per PRC-501?')
    assert any("prc-501" in i for i in ids)

    ids = extract_query_identifiers('Show drawing IP-INF-054-0000-JCB-DWG-LI-200-0001056-04')
    assert any("ip-inf-054" in i for i in ids)

    ids = extract_query_identifiers('What about BOQ item D999.46?')
    assert any("d999.46" in i for i in ids)

    ids = extract_query_identifiers('Find RFI 12-A and NCR-007')
    assert any("rfi" in i and "12-a" in i for i in ids)
    assert any("ncr-007" in i for i in ids)

    ids = extract_query_identifiers('Tell me about concrete')
    assert ids == []


def test_extract_identifiers_ignores_common_label_words_without_a_code():
    """Regression (2026-06-30 pilot): the labeled-reference regex treated
    common words in _REFERENCE_LABELS (Contract, Spec, Package, ...) as a
    reference label and grabbed the FOLLOWING plain English word as a 'code'
    — so "contract cover" -> ['contract cover', 'cover'] and "specification"
    -> ['spec ification', 'ification']. Those false identifiers then earned a
    +2.0 retrieval bonus, flooding the top-K with any boilerplate chunk that
    merely contained the word 'cover', and the model answered "I cannot find."

    A real reference code contains a digit (VO 99, RFI 42, Clause 13.1). A
    label followed by a digit-less word is NOT an identifier.
    """
    from app.core.rag.retriever import extract_query_identifiers

    # The two prod questions that broke. Neither contains a real reference.
    assert extract_query_identifiers(
        "What does the DG2 demolition contract cover?"
    ) == []
    ids = extract_query_identifiers(
        "What does the specification say about reinforcement joints "
        "and crack control in concrete?"
    )
    assert ids == [], f"expected no identifiers, got {ids}"

    # A bare label word with no code must not self-extract.
    assert extract_query_identifiers("Tell me about the contract") == []
    assert extract_query_identifiers("Summarize the package scope") == []

    # ...but a label followed by a real (digit-bearing) code STILL extracts.
    assert any("99" in i for i in extract_query_identifiers("status of VO 99?"))
    assert any("42" in i for i in extract_query_identifiers("RFI 42 update"))
    assert any("13.1" in i for i in extract_query_identifiers("see Clause 13.1"))


def test_extract_identifiers_preserves_quoted_phrases():
    from app.core.rag.retriever import extract_query_identifiers

    ids = extract_query_identifiers('What does "Clause 13.1" require for "VO Ref 31"?')
    assert '"clause 13.1"' in ids or 'clause 13.1' in ids
    assert '"vo ref 31"' in ids or 'vo ref 31' in ids


def test_extract_identifiers_excludes_measurement_units():
    """A spec unit in a query is NOT a reference code. Before this guard,
    "concrete (250 kg/cm2)" extracted 'kg/cm2' as an identifier, which earned
    the +2.0 identifier bonus and matched every drawing dimension-table chunk
    containing the same numbers — burying the real rate chunk and inducing a
    fabricated figure lifted from the number-soup (2026-07-14 live incident)."""
    from app.core.rag.retriever import extract_query_identifiers, _looks_like_unit

    # The exact live-incident query must yield NO polluting identifier.
    assert extract_query_identifiers(
        "What is the unit rate for ready-mix concrete (250 kg/cm2) in Saudi Arabia?"
    ) == []
    # Unit-ratios and bare units never become identifiers.
    for unit_q in ("rebar n/mm2 grade", "density kn/m3", "pour 350 kg/cm2",
                   "area in m2", "volume m3"):
        assert extract_query_identifiers(unit_q) == [], unit_q
    for unit in ("kg/cm2", "n/mm2", "kn/m3", "m3", "cm2", "mm2"):
        assert _looks_like_unit(unit), unit

    # ...but real document reference codes are UNTOUCHED (identifier lane intact).
    for code in ("prc-501", "ip-inf-054", "d999.46", "ncr-007", "12-a", "13.1"):
        assert not _looks_like_unit(code), code
    assert any("prc-501" in i for i in extract_query_identifiers("valid per PRC-501?"))
    assert any("d999.46" in i for i in extract_query_identifiers("BOQ item D999.46"))
    assert any("ip-inf-054" in i for i in
               extract_query_identifiers("Show drawing IP-INF-054-0000-JCB-DWG"))


def test_identifier_chunk_outranks_semantic_boilerplate(isolated_store, monkeypatch):
    """A generic chunk with high semantic similarity must not beat the
    chunk that actually contains the requested identifier."""
    from app.core.rag import retriever as ret
    from app.core.rag.embeddings import Embedder

    store, e = isolated_store
    # Generic boilerplate that would score highly on a status query.
    boilerplate = (
        "Status tracking is important for project controls. "
        "The contractor shall maintain a register of all variations, "
        "requests for information, and non-conformance reports."
    )
    # Exact chunk containing the identifier the user asked for.
    exact = (
        "VO Ref: 99 | Status: Closed | Closed date: 2024-02-12 | "
        "Description: additional drainage works"
    )
    store.upsert_chunks("proj_a", "doc_boilerplate", [boilerplate], e.encode([boilerplate]))
    store.upsert_chunks("proj_a", "doc_exact", [exact], e.encode([exact]))

    # Ensure the boilerplate doc is not treated as noise.
    monkeypatch.setattr(ret, "_doc_name_for_id", lambda _id: "doc.pdf")

    chunks, _ = ret.retrieve_with_filter(
        "What was the status of VO Ref 99?", "proj_a", k=2
    )
    assert len(chunks) >= 1
    # The exact identifier chunk must be #1.
    assert "VO Ref: 99" in chunks[0].text
    assert chunks[0].score > chunks[1].score if len(chunks) > 1 else True


def test_retrieval_without_identifier_uses_semantic_ordering(isolated_store, monkeypatch):
    """Non-identifier queries should not be perturbed by an empty identifier leg."""
    from app.core.rag import retriever as ret

    store, e = isolated_store
    chunks = ["concrete pour schedule", "rebar inventory", "drawing revisions"]
    store.upsert_chunks("proj_a", "doc_x", chunks, e.encode(chunks))
    monkeypatch.setattr(ret, "_doc_name_for_id", lambda _id: "doc.pdf")

    results, _ = ret.retrieve_with_filter("concrete schedule", "proj_a", k=3)
    # Semantic ranking should return the three chunks; exact first-place
    # order depends on the deterministic fake embedder, so just assert
    # coverage without over-fitting to a particular hash-based ordering.
    assert len(results) == 3
    assert any("concrete pour schedule" in c.text for c in results)


def test_identifier_search_is_case_insensitive(isolated_store):
    from app.core.rag.embeddings import Embedder

    store, e = isolated_store
    text = "VO Ref: 99 was closed on 2024-02-12"
    store.upsert_chunks("proj_a", "doc_x", [text], e.encode([text]))

    results = store.identifier_search("proj_a", ["vo ref 99"], k=5)
    assert len(results) == 1
    assert results[0].score == 1.0


def test_identifier_search_escapes_like_wildcards(isolated_store):
    """Identifiers containing SQL LIKE wildcards (% or _) still match literally."""
    from app.core.rag.embeddings import Embedder

    store, e = isolated_store
    text = "Item 50% complete, code A_1"
    store.upsert_chunks("proj_a", "doc_x", [text], e.encode([text]))

    results = store.identifier_search("proj_a", ["50%", "A_1"], k=5)
    assert len(results) == 1
    # Both identifiers match.
    assert results[0].score == 1.0
