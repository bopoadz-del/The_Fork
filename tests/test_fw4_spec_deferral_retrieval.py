"""FW4 #2: specification-scoped cover asks reach the injected context.

S1 "Per the project specification, what is the minimum concrete cover for
foundations cast directly against soil?" The specification gives no
figure: clause 3.1.25.8 sends the cover to the drawings. The footing
drawing note gives 100 mm at the bottom of footings in contact with
soil, about 120 characters after the cover phrase. Without the deferral
path neither chunk is injected.

Cause, in ``retrieve_with_filter`` (called by ``rag_inject`` before the
model answers):
  * the deferral clause is never a candidate: it states no cover
    millimetre, so the numeric fetch rejects it, and its cosine is low;
  * the 100 mm is a list item about 120 characters after the cover
    phrase, past the 64-character window, so it gets no lift.

These tests run the real pre-injection pipeline (``build_retrieval_query``
-> ``retrieve_with_filter`` -> token cap -> system message) over the
synthetic fixture (tests/fixtures/fw4_spec_deferral_chunks.json).
``bge`` runs with BAAI/bge-small-en-v1.5 when it is cached locally
(HF_HUB_OFFLINE=1) and is skipped otherwise; ``fake`` runs everywhere.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

PID = "FIXTURE-e-20260927-project"
FIXTURE = Path(__file__).parent / "fixtures" / "fw4_spec_deferral_chunks.json"

S1_ASK = (
    "Per the project specification, what is the minimum concrete cover for "
    "foundations cast directly against soil?"
)
S2_ASK = (
    "Per the project specification, what compaction is required under road "
    "pavement?"
)
SPEC_DOC = "FIXTURE-e-20260927-spec-vol-a"
ST200_DOC = "FIXTURE-e-20260927-dwg-footing-cover"
BGE = "BAAI/bge-small-en-v1.5"


def _load_fixture():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))["docs"]


def _bge_available() -> bool:
    if os.getenv("HF_HUB_OFFLINE", "").strip() not in ("1", "true", "yes"):
        return False
    try:
        from sentence_transformers import SentenceTransformer

        SentenceTransformer(BGE)
        return True
    except Exception:  # noqa: BLE001 — not cached / not installed -> skip
        return False


EMBEDDERS = [
    "fake",
    pytest.param(
        BGE,
        marks=pytest.mark.skipif(
            not _bge_available(),
            reason="BAAI/bge-small-en-v1.5 not cached offline (HF_HUB_OFFLINE=1)",
        ),
        id="bge",
    ),
]


@pytest.fixture(params=EMBEDDERS, ids=lambda p: "fake" if p == "fake" else "bge")
def corpus(request, tmp_path, monkeypatch):
    model = request.param
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("RAG_EMBEDDING_MODEL", model)
    monkeypatch.setenv("RAG_GENERAL_KNOWLEDGE_PROJECTS", "")
    monkeypatch.delenv("MASTER_CORPUS_SOURCE_PROJECT_ID", raising=False)
    monkeypatch.delenv("RAG_K", raising=False)
    monkeypatch.delenv("MAX_RAG_TOKENS", raising=False)
    from app.core.rag import embeddings as emb
    from app.core.rag import vector_store as vs

    emb.reset_embedder_cache()
    vs.reset_store_cache()
    from app.core.rag import retriever as ret
    from app.core.rag.embeddings import get_embedder
    from app.core.rag.vector_store import get_store

    embedder = get_embedder()
    store = get_store(dim=embedder.dim)
    names = {}
    for doc in _load_fixture():
        texts = [c["text"] for c in doc["chunks"]]
        store.upsert_chunks(PID, doc["doc_id"], texts, embedder.encode(texts))
        names[doc["doc_id"]] = doc["name"]
    monkeypatch.setattr(ret, "_doc_name_for_id", lambda d: names.get(d, ""))
    yield ret
    emb.reset_embedder_cache()
    vs.reset_store_cache()


def _inject(ask):
    from app.core.rag.inject import rag_inject

    msg, audit = rag_inject(
        user_message=ask,
        project_id=PID,
        conversation_id="ws-FIXTURE-e-20260927",
        user_id="fixture",
        agent_name="project-assistant",
        history=[],
    )
    injected = [c["doc_id"] for c in audit.get("chunks") or []]
    return msg, audit, injected


def _rank(injected, doc_id):
    return injected.index(doc_id) + 1 if doc_id in injected else None


def test_s1_spec_deferral_and_st200_reach_injected_context(corpus):
    msg, audit, injected = _inject(S1_ASK)
    assert msg is not None, audit
    content = msg["content"]
    assert _rank(injected, SPEC_DOC), (
        "3.1.25.8 (spec defers cover to the drawings) not injected: "
        f"{injected}"
    )
    assert "3.1.25.8" in content
    assert "on the Drawings" in content
    assert _rank(injected, ST200_DOC), (
        f"footing-cover drawing (100 mm bottom of footings) not injected: {injected}"
    )
    assert "BOTTOM OF FOOTINGS IN CONTACT WITH SOIL ON THIS SHEET : 100mm" in content


def test_s2_subgrade_95pct_clause_stays_in_injected_context(corpus):
    """The earthworks clause (95% of MDD, CBR 25) stays injected.

    The deferral path does not touch a compaction ask (next test).
    """
    msg, audit, injected = _inject(S2_ASK)
    assert msg is not None, audit
    content = msg["content"]
    assert "95% of maximum dry density" in content, injected
    assert "CBR is 25" in content, injected
    assert SPEC_DOC not in injected and ST200_DOC not in injected, injected


def test_s2_injection_is_unchanged_by_the_deferral_path(corpus, monkeypatch):
    """A compaction ask never takes the cover-deferral path."""
    _m, _a, on = _inject(S2_ASK)
    monkeypatch.setenv("RETRIEVAL_SPEC_DEFERRAL", "0")
    _m, _a, off = _inject(S2_ASK)
    assert on == off


def test_kill_switch_restores_the_miss(corpus, monkeypatch):
    """RETRIEVAL_SPEC_DEFERRAL=0 is b13aed07: neither chunk is injected."""
    monkeypatch.setenv("RETRIEVAL_SPEC_DEFERRAL", "0")
    _m, _a, injected = _inject(S1_ASK)
    assert SPEC_DOC not in injected, injected
    assert ST200_DOC not in injected, injected


# ── detector units (no store) ──────────────────────────────────────────────

def _ret(monkeypatch):
    monkeypatch.delenv("RETRIEVAL_SPEC_DEFERRAL", raising=False)
    monkeypatch.delenv("RETRIEVAL_SPEC_BOOST_GUARD", raising=False)
    from app.core.rag import retriever as ret
    return ret


def test_cover_list_item_under_heading_is_a_stated_length(monkeypatch):
    ret = _ret(monkeypatch)
    text = (
        "THE CLEAR CONCRETE COVER TO STEEL REINFORCEMENT SHALL NOT BE LESS "
        "THAN THE FOLLOWING: FOR BURIED WORK THE BOTTOM OF FOOTINGS "
        "IN CONTACT WITH SOIL : 100mm"
    )
    assert ret.chunk_states_cover_length(text)
    monkeypatch.setenv("RETRIEVAL_SPEC_DEFERRAL", "0")
    assert not ret.chunk_states_cover_length(text)


def test_cover_heading_does_not_reach_past_a_new_note(monkeypatch):
    ret = _ret(monkeypatch)
    text = (
        "4. CLEAR COVER TO REINFORCEMENT SHALL BE AS SHOWN ON THE SCHEDULE "
        "OF STRUCTURAL ELEMENTS FOR EACH POUR.\n5. MARKER POSTS : 200mm"
    )
    assert not ret.chunk_states_cover_length(text)


def test_lid_size_is_not_a_cover_length(monkeypatch):
    ret = _ret(monkeypatch)
    text = (
        "The inspection pit shall be protected by a concrete housing with a "
        "concrete cover of 250mm x 250mm x 10mm at grade level."
    )
    assert not ret.chunk_states_cover_length(text)


def test_deferral_sentence_detector(monkeypatch):
    ret = _ret(monkeypatch)
    assert ret.chunk_defers_cover_to_drawings(
        "Spacer blocks fixed to the reinforcement shall give the cover "
        "specified in this volume, on the Drawings or as the engineer directs."
    )
    assert ret.chunk_defers_cover_to_drawings(
        "Concrete cover to reinforcement shall be as shown on the drawings."
    )
    # A manhole cover is not the concrete cover.
    assert not ret.chunk_defers_cover_to_drawings(
        "Access hatch covers and frames shall be as shown on the Drawings."
    )
    # Cover and drawings in different sentences do not defer.
    assert not ret.chunk_defers_cover_to_drawings(
        "The specified minimum concrete cover shall be checked. Bars shall "
        "be fixed as shown on the Drawings."
    )


def test_deferral_path_needs_a_specification_cover_ask(monkeypatch):
    ret = _ret(monkeypatch)
    assert ret.query_asks_spec_deferred_cover(S1_ASK)
    assert not ret.query_asks_spec_deferred_cover(S2_ASK)
    assert not ret.query_asks_spec_deferred_cover(
        "What is the minimum concrete cover for foundations?"
    )
    monkeypatch.setenv("RETRIEVAL_SPEC_DEFERRAL", "0")
    assert not ret.query_asks_spec_deferred_cover(S1_ASK)
