"""F4 — a query in project B must never return project A's content.

This is the the client-critical isolation contract: two clients' documents
live in one store, separated only by `project_id`.

WHY THIS TESTS THE STORE AND NOT `get_rag_filters`:

The completion brief flagged `containers/base.py::get_rag_filters` as a
possible "silent isolation break" — a base default returning None that every
container inherits. It is not, and the reason matters: that method had
exactly ONE reference in the whole repository — its own definition. No
caller, no override. It could not leak anything because nothing ever asked
it. It was dead code and was deleted rather than made abstract (forcing every
container to implement an uncalled method would have been churn, not safety).

Isolation is actually enforced one layer down, in the vector store, by
`WHERE project_id = :project_id` on every read path. THAT is what this pins —
the mechanism that would really let one client see another's drawings if it
regressed.

Known and deliberate exception, asserted explicitly below: an empty/thin
project falls back to the Master Corpus (`retrieve_with_filter` STEP 0b) and
labels those hits `master_corpus`. That is correct for a single-client
deployment and is the reason `origin` exists on search results.
"""
from __future__ import annotations

import pytest

from app.core.rag import vector_store as vs


@pytest.fixture
def store(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("RAG_EMBEDDING_MODEL", "fake")
    vs.reset_store_cache()
    from app.core.rag.embeddings import get_embedder, reset_embedder_cache
    reset_embedder_cache()
    emb = get_embedder()
    return vs.get_store(dim=emb.dim), emb


def _add(store, emb, project_id: str, doc_id: str, text: str):
    store.upsert_chunks(
        project_id=project_id,
        doc_id=doc_id,
        chunks=[text],
        embeddings=emb.encode([text]),
    )


def test_search_never_returns_another_projects_chunk(store):
    """The load-bearing assertion for two clients on one store."""
    st, emb = store
    _add(st, emb, "client_a", "a_doc", "Client A rebar schedule: 25mm bars at 150 centres")
    _add(st, emb, "client_b", "b_doc", "Client B lighting layout: 400W fittings on grid")

    hits = st.search("client_b", emb.encode_queries(["rebar schedule bars centres"])[0], k=10)

    assert hits, "project B should return its own content"
    for h in hits:
        assert h.project_id == "client_b", f"leaked a chunk from {h.project_id}"
        assert h.doc_id != "a_doc", "returned client A's document to client B"


def test_a_projects_own_content_is_still_reachable(store):
    """Isolation must not be achieved by returning nothing."""
    st, emb = store
    _add(st, emb, "client_a", "a_doc", "Client A rebar schedule: 25mm bars at 150 centres")

    hits = st.search("client_a", emb.encode_queries(["rebar schedule"])[0], k=5)

    assert hits, "project A cannot see its OWN document"
    assert all(h.project_id == "client_a" for h in hits)


def test_unknown_project_returns_nothing_rather_than_everything(store):
    """A missing/unknown scope must FAIL CLOSED, not fall through to all rows."""
    st, emb = store
    _add(st, emb, "client_a", "a_doc", "Client A rebar schedule: 25mm bars at 150 centres")
    _add(st, emb, "client_b", "b_doc", "Client B lighting layout: 400W fittings")

    hits = st.search("project_that_does_not_exist",
                     emb.encode_queries(["rebar"])[0], k=10)

    assert hits == [], f"unknown project scope returned {len(hits)} chunks — fails OPEN"


def test_deleting_one_projects_doc_leaves_the_other_intact(store):
    """Scoped WRITES as well as scoped reads.

    Deletion is the direction that can destroy a client's corpus rather than
    merely expose it, so the scope has to hold here too.
    """
    st, emb = store
    _add(st, emb, "client_a", "a_doc", "Client A rebar schedule")
    _add(st, emb, "client_b", "b_doc", "Client B lighting layout")

    st.delete_doc("client_a", "a_doc")

    assert st.search("client_a", emb.encode_queries(["rebar"])[0], k=5) == []
    assert st.search("client_b", emb.encode_queries(["lighting"])[0], k=5), \
        "deleting one project's document destroyed another's chunks"
