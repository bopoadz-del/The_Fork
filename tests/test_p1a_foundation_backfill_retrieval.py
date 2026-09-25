"""Structural backfill under foundations must reach the degree clause.

Live P1a on 209bc83: 2/6. The runs that passed quoted the earthworks clause
(98% of maximum dry density, Modified Proctor). The runs that failed were
handed duct backfilling in soft ground from Vol 2 parts 2 and 3.

On 7c0b255, after the tool path also searches the operator's own words: 0/6.
Every run cites BS 1377 Part 9, the MOT embankment test, and duct sand cover.
None cites 98%, MDD, or Modified Proctor.

The term rescue treats that miss as a success. Its gate asks only whether
the top-k already co-occurs some pair of query stems. "compacted" and
"backfilling" co-occur in the duct chunk, so the gate never fetches the
degree clause sitting outside the semantic pool. Searching the operator's
words alongside the model's then fills the five document slots with those
distractors, and a lucky model-query hit of the clause is crowded out.

The figure is not invented. A corpus that does not hold the clause still
does not state 98%.
"""
from __future__ import annotations

import pytest

from app.core.rag import retriever
from app.core.rag.vector_store import Chunk

ASK = (
    "Per the project specification, to what degree must structural backfill "
    "under foundations be compacted, and by which test?"
)
CLAUSE = (
    "Section 3 Earthworks. Structural backfill placed under foundations shall "
    "be compacted to a minimum of 98% of maximum dry density (MDD), Modified "
    "Proctor, at near-optimum moisture."
)
CLAUSE_ALT = (
    "Earthworks clause 7.4: Compaction of the backfill to a minimum of 98% of "
    "maximum dry density of the modified proctor test at near optimum moisture."
)
DUCT = (
    "Buried ducts in soft ground. Duct backfilling: after laying, the ducts "
    "shall be covered with sand to ensure a 50 mm cover after tamping, the "
    "trench filled in and compacted, and cable warning marker tape laid at "
    "100 mm below finished ground level. All soils shall be properly compacted "
    "and, where required, a compaction test shall be carried out in accordance "
    "with the method set out in BS 1377: Part 9, 1990."
)
MOT = (
    "The compaction test of the embankment shall be performed as per the MOT "
    "standard and procedures of testing materials, comprising field compaction "
    "and in-situ tests on samples of the compacted fill."
)
DRAIN = (
    "Selected backfill for drainage pipes shall be compacted by hand for the "
    "first 300 mm above the pipe. Stones exceeding 40 mm shall be excluded."
)

SPEC = "DD-2023-118 Vol 2 Specification ({n} of 9).pdf"


def _chunk(cid, doc, text, score):
    return Chunk(
        chunk_id=cid, project_id="p1", doc_id=doc, chunk_index=0,
        text=text, score=score,
    )


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
            # Whole tokens, matching VectorStore.identifier_search.
            tokens = set(retriever.re.findall(r"[a-z0-9]+", low))
            if any(
                all(tok in tokens for tok in ident.lower().split())
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
    names = {
        "spec1": SPEC.format(n=1),
        "spec2": SPEC.format(n=2),
        "spec3": SPEC.format(n=3),
        "spec4": SPEC.format(n=4),
    }
    monkeypatch.setattr(
        retriever, "_doc_name_for_id", lambda did: names.get(did, did),
    )


def _text(chunks):
    return " ".join((c.text or "") for c in chunks).lower()


def test_the_degree_clause_is_retrieved_past_duct_and_mot(monkeypatch):
    semantic = [
        _chunk("duct", "spec2", DUCT, 0.62),
        _chunk("mot", "spec1", MOT, 0.57),
        _chunk("drain", "spec3", DRAIN, 0.51),
    ]
    clause = _chunk("clause", "spec4", CLAUSE, 0.0)
    _install(monkeypatch, semantic, semantic + [clause])

    chunks, _noise = retriever.retrieve_with_filter(ASK, "p1", k=5)
    assert any(c.chunk_id == "clause" for c in chunks), (
        "foundation-backfill degree clause was not in the top-k: "
        f"{[c.chunk_id for c in chunks]}"
    )
    assert "98%" in _text(chunks)
    assert "modified proctor" in _text(chunks)


def test_the_older_backfill_wording_is_enough(monkeypatch):
    """The indexed sentence need not repeat 'structural' or 'foundations'."""
    semantic = [
        _chunk("duct", "spec2", DUCT, 0.62),
        _chunk("mot", "spec1", MOT, 0.57),
    ]
    alt = _chunk("alt", "spec4", CLAUSE_ALT, 0.0)
    _install(monkeypatch, semantic, semantic + [alt])

    chunks, _noise = retriever.retrieve_with_filter(ASK, "p1", k=5)
    assert any(c.chunk_id == "alt" for c in chunks), [c.chunk_id for c in chunks]


def test_a_missing_clause_is_not_invented(monkeypatch):
    semantic = [
        _chunk("duct", "spec2", DUCT, 0.62),
        _chunk("mot", "spec1", MOT, 0.57),
        _chunk("drain", "spec3", DRAIN, 0.51),
    ]
    _install(monkeypatch, semantic, list(semantic))

    chunks, _noise = retriever.retrieve_with_filter(ASK, "p1", k=5)
    blob = _text(chunks)
    assert "98%" not in blob
    assert "modified proctor" not in blob
    assert "mdd" not in blob


def test_an_unrelated_question_does_not_pull_the_clause(monkeypatch):
    semantic = [_chunk("duct", "spec2", DUCT, 0.62)]
    clause = _chunk("clause", "spec4", CLAUSE, 0.0)
    _install(monkeypatch, semantic, semantic + [clause])

    chunks, _noise = retriever.retrieve_with_filter(
        "What is the Defects Notification Period under this contract?",
        "p1", k=5,
    )
    assert all(c.chunk_id != "clause" for c in chunks)


def test_a_clause_already_visible_is_not_lifted_again(monkeypatch):
    """A degree clause already in the top-k must not take this rescue's bonus.

    Other ranking (the S1/S2 numeric-requirement lift, source class) may
    still move the score. This rescue's own switch must not.
    """
    clause = _chunk("clause", "notes", CLAUSE, 0.91)
    _install(monkeypatch, [clause], [clause])

    on, _noise = retriever.retrieve_with_filter(ASK, "p1", k=5)
    monkeypatch.setenv("RAG_FOUNDATION_BACKFILL_RESCUE", "0")
    off, _noise = retriever.retrieve_with_filter(ASK, "p1", k=5)
    assert [c.chunk_id for c in on] == ["clause"]
    assert [c.chunk_id for c in off] == ["clause"]
    assert on[0].score == pytest.approx(off[0].score, abs=1e-6)


async def test_the_merged_tool_search_still_shows_the_clause(monkeypatch):
    """The operator's words are searched too. That merge must not bury the clause.

    A model query that does not itself say "structural backfill" still
    receives the clause, because the operator's question does, and the
    clause's lift outranks the duct volume.
    """
    from app.core import doc_index

    semantic = [
        _chunk("duct", "spec2", DUCT, 0.91),
        _chunk("mot", "spec1", MOT, 0.88),
        _chunk("drain", "spec3", DRAIN, 0.84),
        _chunk("fill", "spec2", "General soils compacted to BS 1377 Part 9.", 0.80),
        _chunk("emb", "spec1", "Embankment tested to the MOT standard.", 0.79),
    ]
    clause = _chunk("clause", "spec4", CLAUSE, 0.0)
    _install(monkeypatch, semantic, semantic + [clause])
    monkeypatch.setattr(doc_index, "_load_index", lambda _pid: {"documents": []})

    results = await doc_index.search_project_documents(
        "p1", "backfill compaction requirements", top_k=5, also_query=ASK,
    )
    blob = " ".join(r["snippet"] for r in results).lower()
    assert "modified proctor" in blob
    assert "98%" in blob
    assert results[0]["document_id"] == "spec4"


def test_the_kill_switch_leaves_the_distractors(monkeypatch):
    monkeypatch.setenv("RAG_FOUNDATION_BACKFILL_RESCUE", "0")
    semantic = [
        _chunk("duct", "spec2", DUCT, 0.62),
        _chunk("mot", "spec1", MOT, 0.57),
    ]
    clause = _chunk("clause", "spec4", CLAUSE, 0.0)
    _install(monkeypatch, semantic, semantic + [clause])

    chunks, _noise = retriever.retrieve_with_filter(ASK, "p1", k=5)
    assert all(c.chunk_id != "clause" for c in chunks)
