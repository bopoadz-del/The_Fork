"""The model must not be the only author of a retrieval query.

Live SET5 P1a, six runs on dd4323e: 1/6. On cda1772, after the search pipe
was widened to keep more than one chunk per document: 2/6. The clause is in
the corpus and the platform quotes it verbatim on the runs that reach it --
"compaction of the backfill to minimum 98% of maximum dry density of the
modified proctor test". Widening made it REACHABLE. It did not make the
search LAND on it.

The mechanism: retrieval is deterministic given a query string, but the query
string on the tool path is written by the model at temperature 1. One operator
question therefore becomes a different search on every run. Our own notes
recorded "retrieval is deterministic" -- true of the ranker, false of the path.

Note what does NOT work here, because it was the obvious first idea: a
retry-when-empty. P1a's failing runs were never empty. They returned confident
wrong chunks -- cable-duct trenching, embankment compaction to a highway
standard -- and the answer then said, correctly for what it held, that it did
not have the figure. Emptiness is not the trigger. The operator's own words
have to be searched ALONGSIDE the model's and the results merged.

The pre-injection path already retrieves on the operator's message
(``build_retrieval_query`` in app/core/rag/inject.py). This gives the tool
path the same guarantee.
"""
from __future__ import annotations

import pytest

from app.core import doc_index


class _Chunk:
    def __init__(self, chunk_id, doc_id, text, score):
        self.chunk_id = chunk_id
        self.project_id = "p1"
        self.doc_id = doc_id
        self.chunk_index = 0
        self.text = text
        self.score = score
        self.layer = "own"


CLAUSE = ("compaction of the backfill to minimum 98 percent of maximum dry "
          "density of the modified proctor test at near optimum moisture")

# What the model's own phrasing finds: confidently wrong, never empty.
MODEL_HITS = [
    _Chunk("c1", "spec2", "cable duct trench soft ground sand cover after tamping", 0.88),
    _Chunk("c2", "spec3", "embankment compaction testing to the highway standard", 0.81),
]
# What the operator's own words find.
VERBATIM_HITS = [
    _Chunk("c9", "spec4", CLAUSE, 0.77),
    _Chunk("c1", "spec2", "cable duct trench soft ground sand cover after tamping", 0.70),
]

OPERATOR_ASK = ("Per the project specification, to what degree must structural "
                "backfill under foundations be compacted, and by which test?")
MODEL_QUERY = "backfill compaction requirements"


@pytest.fixture
def two_sided_corpus(monkeypatch):
    """retrieve_with_filter answers differently for each query string."""
    seen = []

    def fake_retrieve(query, project_id, k=5, **_kw):
        seen.append(query)
        if query == OPERATOR_ASK:
            return list(VERBATIM_HITS), 0
        return list(MODEL_HITS), 0

    import app.core.rag.retriever as retr
    monkeypatch.setattr(retr, "retrieve_with_filter", fake_retrieve)
    monkeypatch.setattr(retr, "_doc_name_for_id", lambda d: f"{d}.pdf")
    monkeypatch.setattr(doc_index, "_load_index", lambda _pid: {"documents": []})
    return seen


def _text(results):
    return " ".join(r["snippet"] for r in results)


# ── the live defect ────────────────────────────────────────────────────────

async def test_the_clause_is_found_through_the_operators_words(two_sided_corpus):
    results = await doc_index.search_project_documents(
        "p1", MODEL_QUERY, top_k=5, also_query=OPERATOR_ASK)
    assert "modified proctor" in _text(results).lower()


async def test_the_models_own_hits_are_kept_too(two_sided_corpus):
    # A merge, not a replacement: the model asked for something and may have
    # had a reason. Both result sets survive into the ranking.
    results = await doc_index.search_project_documents(
        "p1", MODEL_QUERY, top_k=5, also_query=OPERATOR_ASK)
    assert "cable duct" in _text(results).lower()


async def test_both_queries_actually_ran(two_sided_corpus):
    await doc_index.search_project_documents(
        "p1", MODEL_QUERY, top_k=5, also_query=OPERATOR_ASK)
    assert OPERATOR_ASK in two_sided_corpus
    assert MODEL_QUERY in two_sided_corpus


async def test_a_chunk_found_by_both_is_not_counted_twice(two_sided_corpus):
    results = await doc_index.search_project_documents(
        "p1", MODEL_QUERY, top_k=5, also_query=OPERATOR_ASK)
    ids = [r["document_id"] for r in results]
    assert len(ids) == len(set(ids))
    # c1 is in both result sets; it keeps its better score, not a doubled one.
    spec2 = next(r for r in results if r["document_id"] == "spec2")
    assert spec2["score"] == pytest.approx(0.88)


# ── it does no extra work when there is nothing to add ─────────────────────

async def test_an_identical_query_is_not_searched_twice(two_sided_corpus):
    await doc_index.search_project_documents(
        "p1", OPERATOR_ASK, top_k=5, also_query=OPERATOR_ASK)
    assert len(two_sided_corpus) == 1


@pytest.mark.parametrize("variant", [
    "  per the project specification, to what degree must structural backfill "
    "under foundations be compacted, and by which test?  ",
    "Per the project specification to what degree must structural backfill "
    "under foundations be compacted and by which test",
])
async def test_a_trivially_different_query_is_not_searched_twice(two_sided_corpus, variant):
    # Case, spacing and punctuation are not a different search.
    await doc_index.search_project_documents(
        "p1", variant, top_k=5, also_query=OPERATOR_ASK)
    assert len(two_sided_corpus) == 1


async def test_no_also_query_is_the_old_path_exactly(two_sided_corpus):
    await doc_index.search_project_documents("p1", MODEL_QUERY, top_k=5)
    assert two_sided_corpus == [MODEL_QUERY]


@pytest.mark.parametrize("empty", [None, "", "   "])
async def test_an_empty_operator_message_is_ignored(two_sided_corpus, empty):
    await doc_index.search_project_documents(
        "p1", MODEL_QUERY, top_k=5, also_query=empty)
    assert two_sided_corpus == [MODEL_QUERY]


# ── switchable, and safe when half of it fails ─────────────────────────────

async def test_the_kill_switch_restores_single_query(two_sided_corpus, monkeypatch):
    monkeypatch.setenv("SEARCH_ALSO_VERBATIM", "0")
    results = await doc_index.search_project_documents(
        "p1", MODEL_QUERY, top_k=5, also_query=OPERATOR_ASK)
    assert two_sided_corpus == [MODEL_QUERY]
    assert "modified proctor" not in _text(results).lower()


async def test_a_failing_second_search_does_not_lose_the_first(monkeypatch):
    def fake_retrieve(query, project_id, k=5, **_kw):
        if query == OPERATOR_ASK:
            raise RuntimeError("retrieval blew up")
        return list(MODEL_HITS), 0

    import app.core.rag.retriever as retr
    monkeypatch.setattr(retr, "retrieve_with_filter", fake_retrieve)
    monkeypatch.setattr(retr, "_doc_name_for_id", lambda d: f"{d}.pdf")
    monkeypatch.setattr(doc_index, "_load_index", lambda _pid: {"documents": []})

    results = await doc_index.search_project_documents(
        "p1", MODEL_QUERY, top_k=5, also_query=OPERATOR_ASK)
    assert "cable duct" in _text(results).lower(), (
        "a broken verbatim search must degrade to the old behaviour, not to nothing")
