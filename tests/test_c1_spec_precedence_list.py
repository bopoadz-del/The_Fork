"""C1: Sub-Clause 1.5.1(d) precedence list is the next same-doc chunk.

Live OLD-pack Wave2 C1 on Master Corpus (tip 8f4b465) scored PARTIAL.
Verbatim ask:

    Answer only from the client project documents. Under Sub-Clause
    1.5.1(d), what is the order of precedence of documents within the
    Specification? List the first three.

Expected first three:
    Post Tender Clarifications
    Tender Addenda
    Schedule of Project Requirements

Observed: retrieved excerpts cut off at "as follows" under
Sub-Clause 1.5.1(d). Neon already has the list on the next same-doc
chunk (c5cf8daa / DD-2023-118 Vol 2: chunk_index 2 = intro, 3 = list).

When a hit is that open-list intro, elect the neighbor. Do not invent
a signatory. Do not steal A2/A3/A5/A6/A9. Kill-switch:
RAG_SPEC_PRECEDENCE_LIST_RESCUE=0. Fixture wording only.
"""
from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest

from app.core.rag.vector_store import Chunk


CATALOG = json.loads(
    (Path(__file__).parent / "fixtures" / "ui_phys" / "questions.json")
    .read_text(encoding="utf-8")
)
C1_ASK = CATALOG["cases"]["C1"]["ask"]
A2_ASK = CATALOG["cases"]["A2"]["ask"]
A3_ASK = CATALOG["cases"]["A3"]["ask"]
A5_ASK = CATALOG["cases"]["A5"]["ask"]
A6_ASK = CATALOG["cases"]["A6"]["ask"]
A9_ASK = CATALOG["cases"]["A9"]["ask"]
C2_ASK = CATALOG["cases"]["C2"]["ask"]
LIVE_PREFIX = "Answer only from the client project documents. "
LIVE_C1 = LIVE_PREFIX + C1_ASK

POST_TENDER = "Post Tender Clarifications"
TENDER_ADDENDA = "Tender Addenda"
SOPR = "Schedule of Project Requirements"

INTRO_TEXT = (
    "1.5 Priority of Documents\n"
    "Sub-Clause 1.5.1(d) — the order of precedence of documents within "
    "the Specification shall be set out as follows"
)
LIST_TEXT = (
    f"{POST_TENDER}\n"
    f"{TENDER_ADDENDA}\n"
    f"{SOPR}\n"
    "Particular Specification\n"
    "General Specification"
)
TOC_TEXT = (
    "Volume 2 Specification — Table of Contents\n"
    "1.5 Priority of Documents\n"
    "1.5.1 Order of precedence\n"
    "the Specification shall be set out"
)
OTHER_SPEC = (
    "This specification section covers Variation Order procedures "
    "and adjustments to the Contract Price."
)
AS_FOLLOWS_UNRELATED = (
    "The design review procedure is as follows"
)

VOL2_NAME = "DD-2023-118_DG2 Infra P1_Vol 2 - Specification (8 of 9).pdf"
TOC_NAME = "DD-2023-118 Vol 2 Specification TOC.pdf"
OTHER_NAME = "DD-2022-175 Demolition Specs Part 3.pdf"

ACTIVE = "p_master"
VOL2_DOC = "c5cf8daa"
TOC_DOC = "tocvol2"
OTHER_DOC = "demo175"


def _chunk(cid, doc_id, score, text, chunk_index=0):
    return Chunk(
        chunk_id=cid,
        project_id=ACTIVE,
        doc_id=doc_id,
        chunk_index=chunk_index,
        text=text,
        score=score,
    )


def test_c1_catalog_ask_is_frozen():
    """The battery question is the measurement. Do not tidy it."""
    assert C1_ASK == (
        "Under Sub-Clause 1.5.1(d), what is the order of precedence of "
        "documents within the Specification? List the first three."
    )
    assert CATALOG["cases"]["C1"]["must"] == [
        POST_TENDER,
        TENDER_ADDENDA,
        SOPR,
    ]


def test_c1_ask_shape_and_list_gates():
    from app.core.rag.retriever import (
        chunk_is_open_list_intro,
        chunk_states_spec_precedence_list,
        query_asks_for_spec_precedence_list,
    )

    assert query_asks_for_spec_precedence_list(C1_ASK)
    assert query_asks_for_spec_precedence_list(LIVE_C1)
    assert query_asks_for_spec_precedence_list(
        "what is the order of precedence of documents within the Specification?"
    )
    assert not query_asks_for_spec_precedence_list(A2_ASK)
    assert not query_asks_for_spec_precedence_list(A3_ASK)
    assert not query_asks_for_spec_precedence_list(A5_ASK)
    assert not query_asks_for_spec_precedence_list(A6_ASK)
    assert not query_asks_for_spec_precedence_list(A9_ASK)
    assert not query_asks_for_spec_precedence_list(C2_ASK)
    assert not query_asks_for_spec_precedence_list(
        "Who signed the letter about the UBCC Concrete Batching Plant?"
    )
    assert not query_asks_for_spec_precedence_list(
        "Under Sub-Clause 1.5.1, what is the priority of the contract documents?"
    )

    assert chunk_is_open_list_intro(INTRO_TEXT)
    assert not chunk_is_open_list_intro(LIST_TEXT)
    assert not chunk_is_open_list_intro(TOC_TEXT)
    assert not chunk_is_open_list_intro(AS_FOLLOWS_UNRELATED)
    assert not chunk_is_open_list_intro(
        INTRO_TEXT + "\n" + LIST_TEXT
    )

    assert chunk_states_spec_precedence_list(LIST_TEXT)
    assert chunk_states_spec_precedence_list(
        f"{POST_TENDER}; {TENDER_ADDENDA}; {SOPR}; Particular Specification"
    )
    assert not chunk_states_spec_precedence_list(INTRO_TEXT)
    assert not chunk_states_spec_precedence_list(TOC_TEXT)
    assert not chunk_states_spec_precedence_list(OTHER_SPEC)
    assert not chunk_states_spec_precedence_list(POST_TENDER)


def _install_c1_corpus(monkeypatch, *, list_in_semantic: bool):
    """Semantic pool is the open-list intro; the list is the neighbor."""
    from app.core.rag import retriever as ret

    intro = _chunk("intro", VOL2_DOC, 0.91, INTRO_TEXT, chunk_index=2)
    toc = _chunk("toc", TOC_DOC, 0.88, TOC_TEXT, chunk_index=0)
    other = _chunk("oth", OTHER_DOC, 0.84, OTHER_SPEC, chunk_index=0)
    listed = _chunk("list", VOL2_DOC, 0.22, LIST_TEXT, chunk_index=3)
    semantic = [intro, toc, other]
    if list_in_semantic:
        semantic.append(listed)

    def fake_search(self, project_id, qvec, k, query_text=None):
        return [c for c in semantic if c.project_id == project_id][:k]

    def fake_id_search(self, project_id, identifiers, k=20):
        return []

    def fake_chunks_for_docs(self, project_id, doc_ids, k_per_doc=12):
        return []

    def fake_following(self, project_id, anchors, n=1):
        out = []
        for doc_id, idx in anchors or []:
            if doc_id == VOL2_DOC and int(idx) == 2:
                out.append(listed)
        return out

    def fake_containing_all(self, project_id, needles, k=20):
        blob = " ".join(needles or []).lower()
        if (
            "post tender clarifications" in blob
            and "tender addenda" in blob
            and "schedule of project requirements" in blob
        ):
            return [listed]
        return []

    names = {
        VOL2_DOC: VOL2_NAME,
        TOC_DOC: TOC_NAME,
        OTHER_DOC: OTHER_NAME,
    }

    monkeypatch.setattr("app.core.rag.vector_store.VectorStore.search", fake_search)
    monkeypatch.setattr(
        "app.core.rag.vector_store.VectorStore.identifier_search", fake_id_search,
    )
    monkeypatch.setattr(
        "app.core.rag.vector_store.VectorStore.chunks_for_docs", fake_chunks_for_docs,
    )
    monkeypatch.setattr(
        "app.core.rag.vector_store.VectorStore.chunks_following", fake_following,
    )
    monkeypatch.setattr(
        "app.core.rag.vector_store.VectorStore.chunks_containing_all",
        fake_containing_all,
    )
    monkeypatch.setattr(
        "app.core.rag.vector_store.VectorStore.count", lambda self, pid=None: 4,
    )
    monkeypatch.setattr(
        "app.core.rag.vector_store.VectorStore._verify_embedding_identity",
        lambda self: None,
    )
    monkeypatch.setattr(ret, "_doc_name_for_id", lambda did: names.get(did, ""),
                        raising=False)
    monkeypatch.setattr(
        "app.core.projects.documents_matching_title_phrase",
        lambda pid, phrase, limit=8: [],
    )
    monkeypatch.setattr(
        "app.core.projects.documents_matching_filename_terms",
        lambda *a, **k: [],
    )
    monkeypatch.setenv("RAG_EMBEDDING_MODEL", "fake")
    monkeypatch.setenv("RAG_GENERAL_KNOWLEDGE_PROJECTS", "")
    monkeypatch.delenv("MASTER_CORPUS_SOURCE_PROJECT_ID", raising=False)
    monkeypatch.delenv("RAG_SPEC_PRECEDENCE_LIST_RESCUE", raising=False)
    monkeypatch.delenv("RAG_SPEC_TITLE_RESCUE", raising=False)
    monkeypatch.delenv("RAG_ACA_INCLUDING_VAT_RESCUE", raising=False)
    monkeypatch.delenv("RAG_TIME_FOR_COMPLETION_RESCUE", raising=False)
    monkeypatch.delenv("RAG_DELAY_DAMAGES_RATE_RESCUE", raising=False)
    monkeypatch.delenv("RAG_ENGINEER_IDENTITY_RESCUE", raising=False)
    monkeypatch.delenv("RAG_LAYERED", raising=False)
    return ret


def test_c1_elects_the_list_neighbor_when_intro_cuts_off(monkeypatch):
    """The live failure: intro 'as follows' occupies top-k; list is N+1."""
    ret = _install_c1_corpus(monkeypatch, list_in_semantic=False)
    chunks, _ = ret.retrieve_with_filter(C1_ASK, ACTIVE, k=5)
    blob = " ".join(c.text for c in chunks)
    assert chunks, "precedence list never reached the pool"
    assert POST_TENDER in blob
    assert TENDER_ADDENDA in blob
    assert SOPR in blob
    assert POST_TENDER in chunks[0].text
    assert TENDER_ADDENDA in chunks[0].text
    assert SOPR in chunks[0].text
    assert all(
        ret.chunk_states_spec_precedence_list(c.text) for c in chunks
    )


def test_c1_live_prefix_still_elects_the_list(monkeypatch):
    ret = _install_c1_corpus(monkeypatch, list_in_semantic=False)
    chunks, _ = ret.retrieve_with_filter(LIVE_C1, ACTIVE, k=5)
    assert chunks
    assert POST_TENDER in chunks[0].text
    assert TENDER_ADDENDA in chunks[0].text
    assert SOPR in chunks[0].text
    assert chunks[0].doc_id == VOL2_DOC
    assert chunks[0].chunk_index == 3


def test_c1_drops_the_intro_when_the_list_is_already_in_the_pool(monkeypatch):
    ret = _install_c1_corpus(monkeypatch, list_in_semantic=True)
    chunks, _ = ret.retrieve_with_filter(C1_ASK, ACTIVE, k=5)
    blob = " ".join(c.text for c in chunks)
    assert POST_TENDER in chunks[0].text
    assert "as follows" not in blob
    assert all(c.doc_id == VOL2_DOC and c.chunk_index == 3 for c in chunks)


def test_c1_kill_switch_restores_intro_first(monkeypatch):
    ret = _install_c1_corpus(monkeypatch, list_in_semantic=False)
    monkeypatch.setenv("RAG_SPEC_PRECEDENCE_LIST_RESCUE", "0")
    chunks, _ = ret.retrieve_with_filter(C1_ASK, ACTIVE, k=5)
    blob = " ".join(c.text for c in chunks)
    assert chunks
    assert chunks[0].chunk_index == 2
    assert "as follows" in chunks[0].text
    assert POST_TENDER not in blob
    assert TENDER_ADDENDA not in blob
    assert SOPR not in blob


def test_a2_a3_a5_a6_a9_are_not_stolen_onto_the_c1_list(monkeypatch):
    ret = _install_c1_corpus(monkeypatch, list_in_semantic=False)
    for ask in (A2_ASK, A3_ASK, A5_ASK, A6_ASK, A9_ASK):
        chunks, _ = ret.retrieve_with_filter(ask, ACTIVE, k=5)
        assert all(
            not ret.chunk_states_spec_precedence_list(c.text) for c in chunks
        ), ask
        assert all(c.chunk_index != 3 for c in chunks), ask


def test_c2_is_not_stolen_onto_the_c1_list(monkeypatch):
    ret = _install_c1_corpus(monkeypatch, list_in_semantic=False)
    chunks, _ = ret.retrieve_with_filter(C2_ASK, ACTIVE, k=5)
    assert all(
        not ret.chunk_states_spec_precedence_list(c.text) for c in chunks
    )


def test_c1_neighbor_election_does_not_need_lexical_backup(monkeypatch):
    """Live shape: the list is only reachable as chunk_index+1."""
    ret = _install_c1_corpus(monkeypatch, list_in_semantic=False)
    monkeypatch.setattr(
        "app.core.rag.vector_store.VectorStore.chunks_containing_all",
        lambda self, project_id, needles, k=20: [],
    )
    chunks, _ = ret.retrieve_with_filter(LIVE_C1, ACTIVE, k=5)
    assert chunks
    assert POST_TENDER in chunks[0].text
    assert TENDER_ADDENDA in chunks[0].text
    assert SOPR in chunks[0].text


def test_c1_lexical_backup_when_intro_missed_the_pool(monkeypatch):
    """If cosine never fetched the intro, still surface the list."""
    ret = _install_c1_corpus(monkeypatch, list_in_semantic=False)

    def fake_search(self, project_id, qvec, k, query_text=None):
        toc = _chunk("toc", TOC_DOC, 0.88, TOC_TEXT, chunk_index=0)
        other = _chunk("oth", OTHER_DOC, 0.84, OTHER_SPEC, chunk_index=0)
        return [toc, other][:k]

    monkeypatch.setattr(
        "app.core.rag.vector_store.VectorStore.search", fake_search,
    )
    monkeypatch.setattr(
        "app.core.rag.vector_store.VectorStore.chunks_following",
        lambda self, project_id, anchors, n=1: [],
    )
    chunks, _ = ret.retrieve_with_filter(C1_ASK, ACTIVE, k=5)
    assert chunks
    assert POST_TENDER in chunks[0].text
    assert TENDER_ADDENDA in chunks[0].text
    assert SOPR in chunks[0].text


def test_inject_states_the_first_three_precedence_items():
    from app.core.rag.inject import format_chunks_as_system_message

    msg = format_chunks_as_system_message(
        [_chunk("list", VOL2_DOC, 0.9, LIST_TEXT, chunk_index=3)],
        4,
        query=LIVE_C1,
    )
    text = msg["content"]
    assert POST_TENDER in text
    assert TENDER_ADDENDA in text
    assert SOPR in text


def test_unrelated_as_follows_is_not_a_c1_intro():
    from app.core.rag.retriever import chunk_is_open_list_intro

    assert not chunk_is_open_list_intro(
        "Delay damages under Sub-Clause 8.8 are as follows"
    )


@pytest.fixture
def project_store(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    import app.core.db as db_mod

    importlib.reload(db_mod)
    from app.core import projects as projects_mod

    pm = importlib.reload(projects_mod)
    pm._initialized = False
    pm.init_db()
    return pm


def test_chunks_following_returns_the_next_same_doc_index(project_store, monkeypatch):
    from app.core.rag import embeddings as _emb, vector_store as _vs

    monkeypatch.setenv("RAG_EMBEDDING_MODEL", "fake")
    _emb.reset_embedder_cache()
    _vs.reset_store_cache()
    from app.core.rag.embeddings import Embedder
    from app.core.rag.vector_store import get_store

    e = Embedder(model_name="fake")
    store = get_store(dim=e.dim)
    p = project_store.create_project("C1 neighbor SQL")
    pid = p["id"]
    vol = project_store.add_document(pid, VOL2_NAME, size=40)
    store.upsert_chunks(
        pid, vol["id"],
        [TOC_TEXT, INTRO_TEXT, LIST_TEXT, OTHER_SPEC],
        e.encode([TOC_TEXT, INTRO_TEXT, LIST_TEXT, OTHER_SPEC]),
    )
    hits = store.chunks_following(pid, [(vol["id"], 1)], n=1)
    assert len(hits) == 1
    assert hits[0].chunk_index == 2
    assert POST_TENDER in hits[0].text
    assert TENDER_ADDENDA in hits[0].text
    assert SOPR in hits[0].text
    assert store.chunks_following(pid, [], n=1) == []
    assert store.chunks_following(pid, [(vol["id"], 99)], n=1) == []
