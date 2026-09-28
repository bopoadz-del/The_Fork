"""CYCLE 2 (0b1d13a) S1: the 75 mm soil-contact cover chunks reach the model.

Live trace (e-c2s1-r1..r6, ids and scores only): 5 retrieved, 5 handed,
every run the same - three footing-cover drawing chunks (100 mm), a 50 mm
"not in contact" drawing note and the specification clause that defers the
cover to the drawings. The chunks that state 75 mm for structure in contact
with soil (a drawings-volume general note and a sheet) were never retrieved.

Why, in ``retrieve_with_filter``:
  * vocabulary: the question says "concrete cover" / "cast directly against
    soil"; those chunks say "cover to reinforcement" / "in contact with
    soil". The cover expansion adds "cast against soil" only, so neither
    50-deep hybrid leg (cosine, BM25) nor the numeric BM25 fetch reaches
    them on a large corpus;
  * depth: with k=5, three slots go to footing-cover chunks that repeat the
    100 mm figure, so a pooled 75 mm note would push the deferral clause
    out instead of joining it.

  * scoring: a chunk pooled by a lexical leg only carries its BM25 rank
    in the score slot, not a cosine (live: the deferral clause scored
    3.754625 = 0.054625 ts_rank + 2.5 + 1.2), so it cannot compete.

Fix (defaults on, RETRIEVAL_SOIL_CONTACT_COVER=0 restores the miss):
pool cover chunks that state a millimetre for the soil-contact condition
in the document's own words, at their own cosine (lexical-only entries of
those chunks and of the deferral clause get their cosine too); hand the
spec-deferred cover ask two extra slots (RAG_SPEC_DEFERRED_COVER_EXTRA_K,
default 2).

Synthetic fixture (tests/fixtures/c2_soil_contact_cover_chunks.json) plus
synthetic fillers that crowd both hybrid legs, as the live corpus does.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

PID = "FIXTURE-e-20260928-project"
FIXTURE = Path(__file__).parent / "fixtures" / "c2_soil_contact_cover_chunks.json"
BGE = "BAAI/bge-small-en-v1.5"

# G7: three phrasings of the failing question + one sibling of the same shape.
S1_ASK = (
    "Per the project specification, what is the minimum concrete cover for "
    "foundations cast directly against soil?"
)
S1_F1 = (
    "According to the specification, what minimum concrete cover is required "
    "where foundations are cast directly against the soil?"
)
S1_F2 = (
    "What does the project specification require as the minimum cover for "
    "footings cast against earth?"
)
S1_SIB = (
    "Per the project specification, what is the minimum cover to "
    "reinforcement for pile caps in contact with the ground?"
)
S1_PHRASINGS = [S1_ASK, S1_F1, S1_F2, S1_SIB]
BLINDING_ASK = (
    "Per the project specification, what is the minimum concrete cover for "
    "foundations cast against blinding?"
)
S2_ASK = (
    "Per the project specification, what compaction is required under road "
    "pavement?"
)

SPEC_DOC = "FIXTURE-e-20260928-spec-vol2-part4"
FOOTING_DOCS = (
    "FIXTURE-e-20260928-dwg-footing-a",
    "FIXTURE-e-20260928-dwg-footing-b",
    "FIXTURE-e-20260928-dwg-footing-c",
)
NOTE_55_DOC = "FIXTURE-e-20260928-dwg-note-5-5"
SHEET_DOC = "FIXTURE-e-20260928-sheet-2000462"
SOIL_75_DOCS = (NOTE_55_DOC, SHEET_DOC)

_ELEMENTS = (
    "north quay wall", "pump house", "outfall chamber", "gate house",
    "fuel farm bund", "workshop", "substation", "stair core", "lift pit",
    "berth 3 apron", "crane rail beam", "fender panel", "security fence",
)
_ACTIVITIES = (
    "first pour", "trial mix", "steel fixing inspection", "shutter check",
    "pre-pour meeting", "method statement review", "curing plan approval",
    "surveyor sign-off", "cube testing", "level check",
)


def _fillers():
    """Synthetic chunks that repeat the question's words, state no cover
    millimetre and never mention contact with soil. On the live corpus
    many chunks outrank the 75 mm notes on cosine and on BM25; these play
    that part so both 50-deep hybrid legs and the numeric fetch fill up."""
    out = []
    n = 0
    for element in _ELEMENTS:
        for activity in _ACTIVITIES:
            n += 1
            out.append(
                f"Example Harbour Works item {n}: the minimum concrete cover for "
                f"foundations cast directly against soil at the {element} per the "
                f"project specification shall be confirmed before the {activity}."
            )
    return out


def _load_fixture():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))["docs"]


def _bge_available() -> bool:
    if os.getenv("HF_HUB_OFFLINE", "").strip() not in ("1", "true", "yes"):
        return False
    try:
        from sentence_transformers import SentenceTransformer

        SentenceTransformer(BGE)
        return True
    except Exception:  # noqa: BLE001 - not cached / not installed -> skip
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
    for var in (
        "RAG_K", "MAX_RAG_TOKENS", "RETRIEVAL_SOIL_CONTACT_COVER",
        "RAG_SPEC_DEFERRED_COVER_EXTRA_K", "RETRIEVAL_SPEC_DEFERRAL",
        "RETRIEVAL_SPEC_BOOST_GUARD", "RETRIEVAL_NUMERIC_REQUIREMENT_BOOST",
    ):
        monkeypatch.delenv(var, raising=False)
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
    fillers = _fillers()
    filler_doc = "FIXTURE-e-20260928-site-notes"
    store.upsert_chunks(PID, filler_doc, fillers, embedder.encode(fillers))
    names[filler_doc] = "FIXTURE-e-20260928 Site notes pour register.pdf"
    monkeypatch.setattr(ret, "_doc_name_for_id", lambda d: names.get(d, ""))
    yield ret
    emb.reset_embedder_cache()
    vs.reset_store_cache()


def _inject(ask):
    from app.core.rag.inject import rag_inject

    msg, audit = rag_inject(
        user_message=ask,
        project_id=PID,
        conversation_id="ws-FIXTURE-e-20260928",
        user_id="fixture",
        agent_name="project-assistant",
        history=[],
    )
    handed = [c["doc_id"] for c in audit.get("chunks") or []]
    return msg, audit, handed


def _retrieved(ret, ask, k=5):
    chunks, _ = ret.retrieve_with_filter(ask, PID, k=k, operator_text=ask)
    return [c.doc_id for c in chunks]


@pytest.mark.parametrize("ask", S1_PHRASINGS, ids=["s1", "f1", "f2", "sibling"])
def test_soil_contact_75mm_note_is_retrieved_and_handed(corpus, ask):
    """The drawings-volume general note (live: note 5.5, REDACTED/REDACTED
    chunk 1955) reaches the model next to the deferral clause and the
    100 mm footing figure."""
    msg, audit, handed = _inject(ask)
    assert msg is not None, audit
    assert NOTE_55_DOC in handed, f"75 mm soil-contact note not handed: {handed}"
    # the specification's answer (it defers to the drawings) and the 100 mm
    # footing figure stay in the handed set - the fix adds, never swaps.
    assert SPEC_DOC in handed, f"deferral clause pushed out: {handed}"
    assert any(d in handed for d in FOOTING_DOCS), f"100 mm footing chunk lost: {handed}"


@pytest.mark.parametrize("ask", S1_PHRASINGS, ids=["s1", "f1", "f2", "sibling"])
def test_soil_contact_chunks_are_retrieved(corpus, ask):
    """Both 75 mm chunks leave the retriever: the note at the handed depth,
    the sheet (file name does not say drawing, so no authority lift; live:
    REDACTED:3) in the ranked list just behind it."""
    from app.core.rag.inject import rag_retrieval_k

    handed_depth = _retrieved(corpus, ask, k=rag_retrieval_k(ask, 5))
    assert NOTE_55_DOC in handed_depth, handed_depth
    deeper = _retrieved(corpus, ask, k=10)
    for doc in SOIL_75_DOCS:
        assert doc in deeper, f"{doc} not retrieved: {deeper}"


def test_kill_switch_restores_the_live_miss(corpus, monkeypatch):
    """RETRIEVAL_SOIL_CONTACT_COVER=0 + extra k 0 is 0b1d13a: the 75 mm
    chunks are not retrieved at all while the footing chunks are handed."""
    monkeypatch.setenv("RETRIEVAL_SOIL_CONTACT_COVER", "0")
    monkeypatch.setenv("RAG_SPEC_DEFERRED_COVER_EXTRA_K", "0")
    _m, _a, handed = _inject(S1_ASK)
    assert not any(d in handed for d in SOIL_75_DOCS), handed
    assert any(d in handed for d in FOOTING_DOCS), handed
    deeper = _retrieved(corpus, S1_ASK, k=10)
    assert not any(d in deeper for d in SOIL_75_DOCS), deeper


def test_blinding_ask_is_unchanged_by_the_soil_contact_pool(corpus, monkeypatch):
    """A blinding cover ask names no soil contact: same handed set either way."""
    _m, _a, on = _inject(BLINDING_ASK)
    monkeypatch.setenv("RETRIEVAL_SOIL_CONTACT_COVER", "0")
    _m, _a, off = _inject(BLINDING_ASK)
    assert on == off


def test_compaction_ask_is_unchanged(corpus, monkeypatch):
    _m, _a, on = _inject(S2_ASK)
    monkeypatch.setenv("RETRIEVAL_SOIL_CONTACT_COVER", "0")
    monkeypatch.setenv("RAG_SPEC_DEFERRED_COVER_EXTRA_K", "0")
    _m, _a, off = _inject(S2_ASK)
    assert on == off
    assert not any(d in on for d in SOIL_75_DOCS), on


# ── detector / depth units (no store) ─────────────────────────────────────

def _ret(monkeypatch):
    for var in ("RETRIEVAL_SOIL_CONTACT_COVER", "RETRIEVAL_SPEC_DEFERRAL",
                "RETRIEVAL_SPEC_BOOST_GUARD", "RAG_SPEC_DEFERRED_COVER_EXTRA_K"):
        monkeypatch.delenv(var, raising=False)
    from app.core.rag import retriever as ret
    return ret


@pytest.mark.parametrize("ask", S1_PHRASINGS, ids=["s1", "f1", "f2", "sibling"])
def test_query_names_soil_contact_cover(monkeypatch, ask):
    ret = _ret(monkeypatch)
    assert ret.query_asks_soil_contact_cover(ask)


@pytest.mark.parametrize("ask", [
    BLINDING_ASK, S2_ASK,
    "Please send the cover letter for the soil investigation report.",
    "What is the minimum concrete cover for slabs above ground?",
    "What is the minimum concrete cover where foundations are not in contact with soil?",
])
def test_other_asks_do_not_take_the_soil_contact_path(monkeypatch, ask):
    ret = _ret(monkeypatch)
    assert not ret.query_asks_soil_contact_cover(ask)


def test_chunk_detector_needs_a_cover_length_and_the_soil_condition(monkeypatch):
    ret = _ret(monkeypatch)
    assert ret.chunk_states_soil_contact_cover(
        "5.5 COVER TO REINFORCEMENT: STRUCTURE IN CONTACT WITH SOIL 75mm."
    )
    assert not ret.chunk_states_soil_contact_cover(
        "Backfill in contact with soil shall be placed in 200mm layers."
    )
    assert not ret.chunk_states_soil_contact_cover(
        "NOMINAL COVER WHERE CAST AGAINST BLINDING : 50mm."
    )
    # Negation is a different condition. A chunk that states both still matches.
    assert not ret.chunk_states_soil_contact_cover(
        "MINIMUM CONCRETE COVER FOR CONCRETE CAST NOT IN CONTACT WITH SOIL : 50mm."
    )
    assert ret.chunk_states_soil_contact_cover(
        "MINIMUM CONCRETE COVER: BOTTOM OF FOOTINGS CAST IN CONTACT WITH SOIL : 100mm. "
        "TOP NOT IN CONTACT WITH SOIL : 50mm."
    )


def test_soil_contact_kill_switch(monkeypatch):
    ret = _ret(monkeypatch)
    monkeypatch.setenv("RETRIEVAL_SOIL_CONTACT_COVER", "0")
    assert not ret.query_asks_soil_contact_cover(S1_ASK)


def test_spec_deferred_cover_ask_gets_two_extra_slots(monkeypatch):
    _ret(monkeypatch)
    from app.core.rag.inject import rag_retrieval_k

    assert rag_retrieval_k(S1_ASK, 5) == 7
    assert rag_retrieval_k(S2_ASK, 5) == 5
    assert rag_retrieval_k("What is the site address?", 5) == 5
    monkeypatch.setenv("RAG_SPEC_DEFERRED_COVER_EXTRA_K", "0")
    assert rag_retrieval_k(S1_ASK, 5) == 5
