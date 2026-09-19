"""Formula-style asks on a fixture project must not go no-tool / Master Corpus.

Phase 2 live: a formula audit fixture project received asks such as
rebar lap, pe_unit_convert, and formwork striking. The turn often
(1) answered without calling construction_calc, and/or (2) cited the
Master Corpus fallback instead of staying on the active project_id.

This file is synthetic only — no client names, no live fixture ids.
Agent A owns no-tool + master_corpus bleed (routing / runtime / inject).
"""
from __future__ import annotations

import pytest

from app.agents.runtime import (
    _apply_rag_context,
    _build_sources_from_audit,
    _forced_specific_tool,
)


FIXTURE_PID = "proj_formula_fixture_scope"
MASTER_PID = "master_src_formula_scope"
GK_PID = "curated_kb_formula_scope"

AVAILABLE = {"construction_calc", "search_project_documents"}

# Formula-shaped asks that do not carry L×W×D. The live miss class:
# intent_map / dimension heuristic never forced construction_calc.
FORMULA_ASKS = (
    "compute rebar lap for 20 mm bar fy 420",
    "pe_unit_convert 10 m to ft",
    "formwork striking time for a slab",
)

LOOKUP_ASKS = (
    "what is the backfilling specification for soft ground",
    "what is the concrete volume in the BOQ",
    "list the documents in this project",
)


def _tail(text: str):
    return [{"role": "user", "content": text}]


def _chunk(chunk_id, project_id, score, text="filler content"):
    from app.core.rag.vector_store import Chunk
    return Chunk(
        chunk_id=chunk_id,
        project_id=project_id,
        doc_id=f"doc-{chunk_id}",
        chunk_index=0,
        text=text,
        score=score,
    )


def _install_empty_fixture_with_master(monkeypatch, *, record=None):
    """Empty fixture project + a high-scoring Master Corpus row.

    Same harness as tests/test_step0_retrieval_isolation.py — fake
    VectorStore.search so scores are exact and no live embedder is needed.
    """
    from app.core.rag import retriever as ret

    per_project = {
        FIXTURE_PID: [],
        MASTER_PID: [_chunk("m_bleed", MASTER_PID, 0.95, "generic formula notes")],
        GK_PID: [],
    }

    def fake_search(self, project_id, qvec, k, query_text=None):
        if record is not None:
            record.append(project_id)
        return list(per_project.get(project_id, []))

    monkeypatch.setattr("app.core.rag.vector_store.VectorStore.search", fake_search)
    monkeypatch.setattr(
        "app.core.rag.vector_store.VectorStore._verify_embedding_identity",
        lambda self: None,
    )
    monkeypatch.setattr(ret, "_doc_name_for_id", lambda _id: "real.pdf", raising=False)
    monkeypatch.setenv("RAG_EMBEDDING_MODEL", "fake")
    monkeypatch.setenv("RAG_GENERAL_KNOWLEDGE_PROJECTS", GK_PID)
    monkeypatch.setenv("MASTER_CORPUS_SOURCE_PROJECT_ID", MASTER_PID)
    monkeypatch.setenv("RAG_CONFIDENCE_THRESHOLD", "0.4")
    for var in (
        "RAG_GK_SCORE_MARGIN", "RAG_OWN_DOC_BOOST", "RAG_GK_TOPK_CAP",
        "RAG_GK_LEXICAL_FOLD",
    ):
        monkeypatch.delenv(var, raising=False)
    return ret


# ── 1. formula-style ask must route to construction_calc ──────────────────


@pytest.mark.parametrize("q", FORMULA_ASKS)
def test_formula_style_ask_forces_construction_calc(q):
    """A formula ask on any project must force the calculator tool.

    Live miss: project-assistant answered in prose (no-tool) because
    tool_choice stayed auto and the dimension heuristic never fired.
    """
    assert (
        _forced_specific_tool(_tail(q), AVAILABLE) == "construction_calc"
    ), q


@pytest.mark.parametrize("q", LOOKUP_ASKS)
def test_document_lookups_are_not_forced_onto_the_calculator(q):
    """Lookups stay on RAG. Do not steal BOQ / spec questions."""
    assert _forced_specific_tool(_tail(q), AVAILABLE) is None, q


def test_formula_force_requires_the_tool_to_be_available():
    q = FORMULA_ASKS[0]
    assert _forced_specific_tool(_tail(q), {"search_project_documents"}) is None


# ── 2. retrieved / cited sources stay on the fixture project_id ───────────


@pytest.mark.parametrize("q", FORMULA_ASKS)
def test_formula_ask_on_fixture_does_not_query_master_corpus(monkeypatch, q):
    asked: list = []
    ret = _install_empty_fixture_with_master(monkeypatch, record=asked)
    chunks, _ = ret.retrieve_with_filter(q, FIXTURE_PID, k=5)
    assert MASTER_PID not in asked, (
        f"formula ask queried Master Corpus fallback: {asked}"
    )
    assert all(getattr(c, "layer", "own") != "master_corpus" for c in chunks)
    assert all(c.project_id != MASTER_PID for c in chunks)


@pytest.mark.parametrize("q", FORMULA_ASKS)
def test_rag_inject_formula_ask_on_fixture_does_not_bleed_master(monkeypatch, q):
    _install_empty_fixture_with_master(monkeypatch)
    from app.core.rag import inject as inj

    sys_msg, audit = inj.rag_inject(
        user_message=q,
        project_id=FIXTURE_PID,
        conversation_id=None,
        user_id=None,
        agent_name="project-assistant",
    )
    assert audit.get("project_id") == FIXTURE_PID
    assert audit.get("fallback_used") is not True
    chunks = audit.get("chunks") or []
    assert all(c.get("layer") != "master_corpus" for c in chunks)
    assert all(c.get("project_id") != MASTER_PID for c in chunks)
    sources = _build_sources_from_audit(audit, final_text="")
    assert all(s.get("layer") != "master_corpus" for s in sources)
    assert all(s.get("project_id") != MASTER_PID for s in sources)
    # Empty fixture + no fallback: nothing to fold. That is correct —
    # the calculator tool is the answer path, not Master Corpus prose.
    if sys_msg and sys_msg.get("content"):
        assert "master_corpus" not in (sys_msg.get("content") or "").lower()


def test_document_lookup_on_empty_project_still_may_use_master_fallback(monkeypatch):
    """STEP 0b stays for lookups. Only formula asks drop the fallback."""
    asked: list = []
    ret = _install_empty_fixture_with_master(monkeypatch, record=asked)
    chunks, _ = ret.retrieve_with_filter(LOOKUP_ASKS[0], FIXTURE_PID, k=5)
    assert MASTER_PID in asked
    assert chunks and chunks[0].layer == "master_corpus"


# ── 3. RAG fold must not clamp a formula ask to corpus-only / no-tool ─────


@pytest.mark.parametrize("q", FORMULA_ASKS)
def test_formula_ask_is_not_told_to_answer_only_from_corpus(q):
    """The strict lookup clamp is why the model answered no-tool from RAG.

    `_apply_rag_context` used to treat 'rebar lap' as a document lookup
    and append 'using ONLY the reference context' — so when fallback
    excerpts were present the model quoted Master Corpus and never
    called construction_calc.
    """
    context = {
        "content": (
            "[doc_id=abcd chunk=0] master_corpus notes about generic laps"
        ),
    }
    msgs = [{"role": "user", "content": q}]
    assert _apply_rag_context(msgs, context) is True
    folded = msgs[-1]["content"]
    assert "using ONLY the reference context" not in folded
    assert "CALCULATION REQUEST" in folded
    assert q in folded
