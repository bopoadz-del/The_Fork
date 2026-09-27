"""S1 through retrieval and the first-line hedge together.

Agent E pools the specification clause that defers cover, and the drawing
that states it. Agent D states each figure with its own condition and the
drawing that contains it. On the synthetic S1 fixture the joined path
must still produce both lengths: 75 mm in contact with soil, and 100 mm
at the bottom of footings. Neither figure is credited to the specification.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from app.agents.first_line_hard_rule import apply_first_line_hard_rule

PID = "FIXTURE-e-20260927-project"
FIXTURE = Path(__file__).parent / "fixtures" / "fw4_spec_deferral_chunks.json"
SPEC_DOC = "FIXTURE-e-20260927-spec-vol-a"
FOOT_DOC = "FIXTURE-e-20260927-dwg-footing-cover"
SPEC_NAME = "FIXTURE-e-20260927 Specification Volume A.pdf"
FOOT_NAME = "FIXTURE-e-20260927 DWG footing cover note.pdf"
S1_ASK = (
    "Per the project specification, what is the minimum concrete cover for "
    "foundations cast directly against soil?"
)
NARRATIVE = "I will answer from the retrieved context."


def _load():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))["docs"]


def _corpus(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("RAG_EMBEDDING_MODEL", "fake")
    monkeypatch.setenv("RAG_GENERAL_KNOWLEDGE_PROJECTS", "")
    monkeypatch.delenv("MASTER_CORPUS_SOURCE_PROJECT_ID", raising=False)
    monkeypatch.delenv("RAG_K", raising=False)
    monkeypatch.delenv("MAX_RAG_TOKENS", raising=False)
    monkeypatch.delenv("RETRIEVAL_SPEC_DEFERRAL", raising=False)
    monkeypatch.delenv("FIRST_LINE_HARD_RULE", raising=False)
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
    for doc in _load():
        texts = [c["text"] for c in doc["chunks"]]
        store.upsert_chunks(PID, doc["doc_id"], texts, embedder.encode(texts))
        names[doc["doc_id"]] = doc["name"]
    monkeypatch.setattr(ret, "_doc_name_for_id", lambda d: names.get(d, ""))
    return emb, vs, names


def _sentences(text: str) -> list[str]:
    return [part.strip() for part in re.split(r"(?<=\.)\s+", text) if part.strip()]


def _mm_sentence(text: str, number: str) -> str:
    rx = re.compile(rf"(?i)\b{re.escape(number)}\s*mm\b")
    for sentence in _sentences(text):
        if rx.search(sentence):
            return sentence
    return ""


def test_s1_fixture_states_both_cover_figures_from_drawings(tmp_path, monkeypatch):
    """75 mm soil contact and 100 mm at the bottom of footings.

    Each figure names its condition and a drawing. The specification
    clause that defers cover is retrieved and is not the source of either
    figure.
    """
    from app.core.rag.inject import rag_inject

    emb, vs, _names = _corpus(tmp_path, monkeypatch)
    try:
        _assert_s1(rag_inject)
    finally:
        emb.reset_embedder_cache()
        vs.reset_store_cache()


def _assert_s1(rag_inject):
    msg, audit = rag_inject(
        user_message=S1_ASK,
        project_id=PID,
        conversation_id="ws-FIXTURE-e-20260927",
        user_id="fixture",
        agent_name="project-assistant",
        history=[],
    )
    injected = [c["doc_id"] for c in audit.get("chunks") or []]
    assert SPEC_DOC in injected, injected
    assert FOOT_DOC in injected, injected
    out = apply_first_line_hard_rule(
        NARRATIVE,
        msg,
        [{"role": "user", "content": S1_ASK}],
    )
    assert "which document" not in out.lower(), out
    soil = _mm_sentence(out, "75")
    assert soil, out
    assert "contact with soil" in soil.lower(), soil
    assert "DWG" in soil and "soil contact" in soil.lower(), soil
    assert SPEC_NAME not in soil, soil
    foot = _mm_sentence(out, "100")
    assert foot, out
    assert "bottom of footing" in foot.lower(), foot
    assert FOOT_NAME in foot, foot
    assert SPEC_NAME not in foot, foot
    assert SPEC_NAME not in out, out
    assert "3.1.25.8" not in out, out
