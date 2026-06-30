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


def _stub_get_document(monkeypatch, original_name: str) -> None:
    """Stub app.core.projects.get_document without relying on sys.modules."""
    monkeypatch.setattr(
        "app.core.projects.get_document",
        lambda did: {"original_name": original_name},
    )


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
    _stub_get_document(monkeypatch, "PRC-406_HSE.pdf")

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
    _stub_get_document(monkeypatch, "x.pdf")

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
    _stub_get_document(monkeypatch, "x.pdf")

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
    _stub_get_document(monkeypatch, "PRC-406_HSE.pdf")

    text = "see [source: PRC‑406_HSE.pdf, chunk 65]"  # NBH
    out = _build_sources_from_audit(audit, text)
    assert len(out) == 1
    assert out[0]["page_or_section"] == "chunk #65"


def test_build_sources_empty_audit_returns_empty():
    out = _build_sources_from_audit({"chunks": []}, "any text")
    assert out == []


# ── gpt-oss-120b inline-bracketed form: "Source: [filename], chunk N" ──
# The pilot model (gpt-oss:120b-cloud) emits the filename INSIDE brackets
# with the chunk number AFTER the bracket, inline mid-sentence — a form none
# of the prior patterns matched, so the Sources panel silently fell back to
# top-3-by-score instead of the chunks the answer actually cited (2026-06-30).

def test_extract_inline_bracketed_source_single_chunk():
    txt = "Trees must be protected (Source: [DD-2022-175 - DG II Demolition Part 3], chunk 941)."
    out = _extract_cited_chunk_indexes(txt)
    assert any(idx == 941 for _, idx in out), out


def test_extract_inline_bracketed_source_chunk_range():
    txt = "See the schedule (Source: [DD-2022-175 - DG II Demolition Part 2], chunks 1988-1990)."
    out = _extract_cited_chunk_indexes(txt)
    nums = {idx for _, idx in out}
    assert 1988 in nums, out  # at least the range start is captured


def test_build_sources_matches_inline_bracket_cite_by_chunk_index(monkeypatch):
    """The cited chunk index uniquely identifies the injected chunk even when
    gpt-oss truncated the filename with an ellipsis. The Sources panel must
    surface THAT chunk, not the top-3-by-score scaffolding."""
    audit = _make_audit([
        {"doc_id": "d1", "chunk_index": 5,   "score": 0.80},   # high-score scaffolding
        {"doc_id": "d1", "chunk_index": 12,  "score": 0.78},
        {"doc_id": "d2", "chunk_index": 941, "score": 0.64},   # the actually-cited chunk
    ])
    _stub_get_document(
        monkeypatch,
        "DD-2022-175 - DG II Demolition and Site Clearance Works Package 1 Volume 2 Specs Part 3.pdf",
    )
    text = "Trees must be protected (Source: [DD-2022-175 - DG II Demolition … Part 3], chunk 941)."
    out = _build_sources_from_audit(audit, text)
    assert len(out) == 1, out
    assert out[0]["page_or_section"] == "chunk #941"


# ── PR #111 — bracketless "Source:" prefix form (gpt-oss variant) ──────

def test_extract_bracketless_line_form_single():
    """gpt-oss sometimes emits 'Source: foo.pdf, chunk N.' on its own
    line at the end of the answer, no brackets."""
    txt = "Some answer body here.\nSource: PRC-406_HSE.pdf, chunk 65."
    out = _extract_cited_chunk_indexes(txt)
    assert ("PRC-406_HSE.pdf", 65) in out


def test_extract_bracketless_at_start():
    """Source: prefix at the very start of the string (no leading newline)."""
    txt = "Source: vendor.pdf, chunk 4."
    out = _extract_cited_chunk_indexes(txt)
    assert ("vendor.pdf", 4) in out


def test_extract_bracketless_multichunk():
    txt = "See for detail.\nSources: vendor.pdf, chunks 16, 34, 55."
    out = _extract_cited_chunk_indexes(txt)
    assert ("vendor.pdf", 16) in out
    assert ("vendor.pdf", 34) in out
    assert ("vendor.pdf", 55) in out


def test_build_sources_uses_bracketless_citation(monkeypatch):
    """End-to-end: agent emits bracketless 'Source:' form; Sources panel
    still surfaces the cited chunk, not the top retrieval slice."""
    audit = _make_audit([
        {"doc_id": "d1", "chunk_index": 0,  "score": 0.78},
        {"doc_id": "d1", "chunk_index": 65, "score": 0.69},
    ])
    _stub_get_document(monkeypatch, "PRC-406_HSE.pdf")

    text = "Answer body.\nSource: PRC-406_HSE.pdf, chunk 65."
    out = _build_sources_from_audit(audit, text)
    assert len(out) == 1
    assert out[0]["page_or_section"] == "chunk #65"


# ── PR #112 — [doc_id=X chunk=N] form (gpt-oss technical-precision style) ──

def test_extract_doc_id_form_with_separators():
    """gpt-oss sometimes emits [doc_id=3496d239, chunk 65, score 0.697]."""
    txt = "see [doc_id=3496d239, chunk 65, score 0.697] for detail"
    out = _extract_cited_chunk_indexes(txt)
    assert ("3496d239", 65) in out


def test_extract_doc_id_form_kv_separators():
    """The RAG-injection header style: [doc_id=X chunk=N score=Y]."""
    txt = "[doc_id=abc12345 chunk=4 score=0.81]"
    out = _extract_cited_chunk_indexes(txt)
    assert ("abc12345", 4) in out


def test_build_sources_uses_doc_id_citation(monkeypatch):
    """When the agent cites by [doc_id=X chunk=N], the panel surfaces
    THAT chunk by direct doc_id match — bypasses the filename match
    entirely (avoids name-mismatch fall-through)."""
    audit = _make_audit([
        {"doc_id": "3496d239", "chunk_index": 0,  "score": 0.78},
        {"doc_id": "3496d239", "chunk_index": 4,  "score": 0.74},
        {"doc_id": "3496d239", "chunk_index": 65, "score": 0.69},
    ])
    _stub_get_document(monkeypatch, "PRC-406_HSE.pdf")

    text = "Per the procedure [doc_id=3496d239, chunk 65, score 0.697]."
    out = _build_sources_from_audit(audit, text)
    assert len(out) == 1
    assert out[0]["page_or_section"] == "chunk #65"
    assert out[0]["doc_id"] == "3496d239"


def test_build_sources_doc_id_mismatch_falls_back(monkeypatch):
    """Unknown doc_id in citation → no match → fall back to top-3."""
    audit = _make_audit([
        {"doc_id": "a", "chunk_index": 1, "score": 0.5},
        {"doc_id": "a", "chunk_index": 2, "score": 0.6},
    ])
    _stub_get_document(monkeypatch, "x.pdf")

    text = "[doc_id=unknown chunk=99]"
    out = _build_sources_from_audit(audit, text)
    assert len(out) == 2  # both chunks (only 2 injected, top-3 cap returns all)


# ── P0A: smart-quote / inline / prose citation hardening ────────────────

def test_extract_smart_quote_source_inline():
    """Model emits Source: “File Name.xlsx”, which says..."""
    txt = 'Source: "Diff BOQ Qty Vs Modified Qty.xlsx", which lists PVC pipe sizes.'
    out = _extract_cited_chunk_indexes(txt)
    assert any("Diff BOQ Qty Vs Modified Qty.xlsx" in fname for fname, _ in out)


def test_extract_smart_quote_source_with_chunk():
    txt = 'Source: “PRC-302_Risk Management.pdf”, chunk 8.'
    out = _extract_cited_chunk_indexes(txt)
    assert ("PRC-302_Risk Management.pdf", 8) in out


def test_extract_filename_with_spaces_and_punctuation(monkeypatch):
    """A filename containing spaces, commas (when no chunk suffix), and
    typographic quotes still resolves to the injected chunk."""
    audit = _make_audit([
        {"doc_id": "d1", "chunk_index": 0, "score": 0.8},
    ])
    _stub_get_document(monkeypatch, "Diff BOQ Qty Vs Modified Qty.xlsx")

    text = 'Source: “Diff BOQ Qty Vs Modified Qty.xlsx”, which lists PVC sizes.'
    out = _build_sources_from_audit(audit, text)
    assert len(out) == 1
    assert out[0]["doc_name"] == "Diff BOQ Qty Vs Modified Qty.xlsx"


def test_build_sources_emits_fallback_when_citation_unparseable(monkeypatch):
    """If the model mentions a filename but the formal parser cannot map it
    and the text does not match any injected doc_name, the top-3 retrieved
    chunks must still be emitted so the right panel never goes empty."""
    audit = _make_audit([
        {"doc_id": "d1", "chunk_index": 0, "score": 0.9},
        {"doc_id": "d2", "chunk_index": 3, "score": 0.7},
        {"doc_id": "d3", "chunk_index": 5, "score": 0.6},
    ])
    _stub_get_document(monkeypatch, "Real Document.pdf")

    text = "The answer is based on some external document not in the project."
    out = _build_sources_from_audit(audit, text)
    assert len(out) == 3
    assert [c["page_or_section"] for c in out] == ["chunk #0", "chunk #3", "chunk #5"]


def test_build_sources_uses_filename_mention_fallback(monkeypatch):
    """If the model names an injected document in prose without a formal
    citation marker, the right panel should still surface that document's
    highest-scoring chunk."""
    audit = _make_audit([
        {"doc_id": "d1", "chunk_index": 0, "score": 0.9},
        {"doc_id": "d2", "chunk_index": 3, "score": 0.7},
    ])

    def doc_lookup(did):
        return {"original_name": "Diff BOQ Qty Vs Modified Qty.xlsx"} if did == "d1" else {"original_name": "Other.pdf"}

    monkeypatch.setattr("app.core.projects.get_document", doc_lookup)

    text = "The storm-water pipe sizes come from Diff BOQ Qty Vs Modified Qty.xlsx."
    out = _build_sources_from_audit(audit, text)
    assert len(out) == 1
    assert out[0]["doc_name"] == "Diff BOQ Qty Vs Modified Qty.xlsx"
    assert out[0]["page_or_section"] == "chunk #0"


# ── P0B: source contract hardening ───────────────────────────────────────────

from app.agents.runtime import _clean_path_label, _sanitize_citation_labels


def test_clean_path_label_strips_windows_drive():
    assert _clean_path_label(r"G:\My Drive\500-Design Management\PRC-501.pdf") == "PRC-501.pdf"


def test_clean_path_label_strips_unix_path():
    assert _clean_path_label("/home/user/projects/specs/PRC-501.pdf") == "PRC-501.pdf"


def test_clean_path_label_strips_network_share():
    assert _clean_path_label(r"\\server\share\folder\doc.pdf") == "doc.pdf"


def test_clean_path_label_leaves_basename_alone():
    assert _clean_path_label("PRC-501.pdf") == "PRC-501.pdf"


def test_sanitize_citation_labels_cleans_bracketed_windows_path():
    raw = "See [source: G:\\My Drive\\PRC-501.pdf, chunk 3] for details."
    cleaned = _sanitize_citation_labels(raw)
    assert "G:\\My Drive" not in cleaned
    assert "[source: PRC-501.pdf, chunk 3]" in cleaned


def test_sanitize_citation_labels_cleans_chinese_bracket_path():
    raw = "See 【source: G:\\My Drive\\PRC-501.pdf, chunk 3】 for details."
    cleaned = _sanitize_citation_labels(raw)
    assert "G:\\My Drive" not in cleaned
    assert "【source: PRC-501.pdf, chunk 3】" in cleaned


def test_sanitize_citation_labels_cleans_bracketless_source_line():
    raw = "Answer body.\nSource: \\server\\share\\folder\\doc.pdf, chunk 8."
    cleaned = _sanitize_citation_labels(raw)
    assert "\\server" not in cleaned
    assert "Source: doc.pdf, chunk 8" in cleaned


def test_extract_chinese_bracket_source():
    txt = "Per the procedure 【source: PRC-406_HSE.pdf, chunk 65】."
    out = _extract_cited_chunk_indexes(txt)
    assert ("PRC-406_HSE.pdf", 65) in out


def test_build_sources_cleans_raw_doc_name(monkeypatch):
    audit = {
        "project_id": "proj_x",
        "chunks": [
            {"doc_id": "d1", "chunk_index": 4, "chunk_id": "proj_x:d1:4", "score": 0.82},
        ],
    }
    monkeypatch.setattr(
        "app.core.projects.get_document",
        lambda did: {"original_name": r"G:\My Drive\PRC-406_HSE.pdf"},
    )
    out = _build_sources_from_audit(audit, "citation text")
    assert len(out) == 1
    assert out[0]["doc_name"] == "PRC-406_HSE.pdf"
    assert out[0]["project_id"] == "proj_x"
    assert out[0]["chunk_index"] == 4
    assert out[0]["chunk_id"] == "proj_x:d1:4"
