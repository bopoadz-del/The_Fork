"""PR — _build_sources_from_audit derives Sources from agent citations.

Pre-PR: the SSE end event always returned the top-3 retrieved chunks by
score, ignoring what the agent actually cited in its text response.
With the gpt-oss model emitting "[source: <file>, chunk N]" markers,
the Sources panel now reflects what the operator can verify in the
answer rather than the top retrieval slice.
"""
from __future__ import annotations

import pytest

from app.agents.runtime import (
    _build_sources_from_audit,
    _extract_cited_chunk_indexes,
    _normalise_filename,
)


def test_extract_single_citation():
    txt = "These points are listed in the procedure’s checklist [source: PRC-406_HSE.pdf, chunk 65]."
    out = _extract_cited_chunk_indexes(txt)
    assert out == [("PRC-406_HSE.pdf", 65)]


def test_extract_multiple_chunk_numbers():
    txt = "See the policy [source: Vendor PQ.pdf, chunks 16, 34, 55] for full detail."
    out = _extract_cited_chunk_indexes(txt)
    assert out == [
        ("Vendor PQ.pdf", 16),
        ("Vendor PQ.pdf", 34),
        ("Vendor PQ.pdf", 55),
    ]


def test_extract_filename_only_citation():
    txt = "Per the spec [source: Doc.pdf]."
    out = _extract_cited_chunk_indexes(txt)
    assert out == [("Doc.pdf", -1)]


def test_extract_handles_unicode_hyphens():
    """gpt-oss sometimes rewrites file names with non-breaking hyphens.
    The extractor returns whatever the model emitted; matching is then
    handled by _normalise_filename at the lookup step."""
    txt = "[source: PRC‑1000.pdf, chunk 1]"  # non-breaking hyphen
    out = _extract_cited_chunk_indexes(txt)
    assert out[0][1] == 1
    assert "PRC" in out[0][0]


def test_normalise_filename_collapses_dashes():
    a = "PRC‑406_HSE Audit.PDF"  # non-breaking hyphen + uppercase ext
    b = "prc-406_hse audit.pdf"
    assert _normalise_filename(a) == _normalise_filename(b)


def _make_audit(chunks: list[dict]) -> dict:
    return {"chunks": chunks}


def test_build_sources_uses_cited_chunks_when_present(monkeypatch):
    """When the agent's text cites a specific chunk, the Sources list
    returns THAT chunk, not the top-3 by score."""
    audit = _make_audit([
        {"doc_id": "d1", "chunk_index": 0,  "score": 0.78},  # high-score scaffolding
        {"doc_id": "d1", "chunk_index": 4,  "score": 0.74},
        {"doc_id": "d1", "chunk_index": 18, "score": 0.77},
        {"doc_id": "d1", "chunk_index": 65, "score": 0.69},  # the actually-cited one
    ])
    # Stub the doc-name lookup so we don't need a real DB.
    import app.agents.runtime as rt
    import sys, types
    fake_projects = types.SimpleNamespace(
        get_document=lambda did: {"original_name": "PRC-406_HSE.pdf"},
    )
    monkeypatch.setitem(sys.modules, "app.core.projects", fake_projects)

    text = "Per the procedure [source: PRC-406_HSE.pdf, chunk 65]."
    out = _build_sources_from_audit(audit, text)

    assert len(out) == 1
    assert out[0]["page_or_section"] == "chunk #65"
    assert out[0]["doc_name"] == "PRC-406_HSE.pdf"
    # Score 0.69 → Medium, not High
    assert out[0]["confidence"] == "Medium"


def test_build_sources_falls_back_when_no_citations(monkeypatch):
    """When the agent text has no [source: ...] markers, behave like
    pre-PR-110: top-3 retrieved chunks by score."""
    audit = _make_audit([
        {"doc_id": "d1", "chunk_index": 0,  "score": 0.78},
        {"doc_id": "d1", "chunk_index": 4,  "score": 0.74},
        {"doc_id": "d1", "chunk_index": 18, "score": 0.77},
        {"doc_id": "d1", "chunk_index": 65, "score": 0.69},
    ])
    import sys, types
    monkeypatch.setitem(sys.modules, "app.core.projects",
                        types.SimpleNamespace(get_document=lambda did: {"original_name": "x.pdf"}))

    out = _build_sources_from_audit(audit, "answer with no citations")
    assert len(out) == 3
    # Top 3 by score desc: 0.78, 0.77, 0.74 — chunks 0, 18, 4
    assert [c["page_or_section"] for c in out] == ["chunk #0", "chunk #18", "chunk #4"]


def test_build_sources_falls_back_when_citation_doesnt_match(monkeypatch):
    """Citation that references a non-injected chunk → fall back, don't
    return an empty list."""
    audit = _make_audit([
        {"doc_id": "d1", "chunk_index": 0, "score": 0.78},
    ])
    import sys, types
    monkeypatch.setitem(sys.modules, "app.core.projects",
                        types.SimpleNamespace(get_document=lambda did: {"original_name": "x.pdf"}))

    out = _build_sources_from_audit(audit, "see [source: other.pdf, chunk 99]")
    # Citation didn't match any injected chunk → fall back
    assert len(out) == 1
    assert out[0]["page_or_section"] == "chunk #0"


def test_build_sources_unicode_dash_matches(monkeypatch):
    """The agent rewrote the source name with a non-breaking hyphen.
    The lookup still resolves via _normalise_filename."""
    audit = _make_audit([
        {"doc_id": "d1", "chunk_index": 65, "score": 0.69},
    ])
    import sys, types
    monkeypatch.setitem(sys.modules, "app.core.projects",
                        types.SimpleNamespace(get_document=lambda did: {"original_name": "PRC-406_HSE.pdf"}))

    text = "see [source: PRC‑406_HSE.pdf, chunk 65]"  # NBH
    out = _build_sources_from_audit(audit, text)
    assert len(out) == 1
    assert out[0]["page_or_section"] == "chunk #65"


def test_build_sources_empty_audit_returns_empty():
    out = _build_sources_from_audit({"chunks": []}, "any text")
    assert out == []
