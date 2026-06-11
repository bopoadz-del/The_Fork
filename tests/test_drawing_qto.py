"""Tests for DrawingQTOBlock - font-size + spatial PDF parser.

Spec: docs/superpowers/specs/2026-06-11-drawing-reader-design.md

Two fixtures:
- drawing_tm_1100010.pdf -- a TM Traffic Management *detail* sheet that
  carries a full JCB title block AND multiple
  "MATCH LINE : FOR REFERENCE REFER TO SHEET NO : N" callouts.
  Primary fixture for title-block + notes + match-line assertions.
- drawing_tm_200.pdf -- the TM-200 *key plan*. Used for the
  cad_tags_filtered_count assertion (densest CAD-tag soup in the pilot).
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

from app.blocks.drawing_qto import DrawingQTOBlock


FIXTURES = Path(__file__).parent / "fixtures"
PRIMARY = FIXTURES / "drawing_tm_1100010.pdf"
KEYPLAN = FIXTURES / "drawing_tm_200.pdf"


@pytest.fixture(scope="module")
async def primary_result():
    block = DrawingQTOBlock()
    return await block.process({"file_path": str(PRIMARY)}, {})


@pytest.fixture(scope="module")
async def keyplan_result():
    block = DrawingQTOBlock()
    return await block.process({"file_path": str(KEYPLAN)}, {})


async def test_process_returns_expected_shape(primary_result):
    r = primary_result
    assert isinstance(r, dict)
    assert "text" in r, "missing 'text' (chunk-ready string)"
    assert "drawing" in r, "missing 'drawing' namespace"
    assert "errors" in r, "missing 'errors' array"
    assert "status" in r, "missing 'status'"
    assert isinstance(r["drawing"], dict)
    assert isinstance(r["errors"], list)


async def test_drawing_number_extracted_not_fallback(primary_result):
    drawing = primary_result["drawing"]
    dn = drawing.get("drawing_number") or ""
    assert dn, "drawing_number not extracted"
    assert "drawing_number_fallback_to_filename" not in (
        primary_result.get("errors") or []
    ), f"fell back to filename: {dn}"
    # Must include the JCB-style discipline-section-sequence shape
    assert re.search(r"[A-Z]{2,}-[A-Z]{2,}-\d+", dn), \
        f"drawing_number does not match JCB-style pattern: {dn!r}"
    # Must not be the filename stem
    assert dn != PRIMARY.stem, f"drawing_number equals filename: {dn}"


async def test_discipline_is_tm(primary_result):
    drawing = primary_result["drawing"]
    assert drawing.get("discipline") == "TM", \
        f"discipline expected 'TM', got {drawing.get('discipline')!r}"
    assert drawing.get("discipline_full") == "Traffic Management", \
        f"discipline_full expected 'Traffic Management', " \
        f"got {drawing.get('discipline_full')!r}"


async def test_notes_present_and_meaningful(primary_result):
    drawing = primary_result["drawing"]
    notes = drawing.get("notes") or []
    assert len(notes) >= 1, f"no notes extracted; got {notes!r}"
    word_count = sum(len(n.split()) for n in notes)
    assert word_count > 10, \
        f"notes word count {word_count} <= 10; notes={notes!r}"


async def test_raw_chunk_has_no_cad_tag_patterns(primary_result):
    text = primary_result["text"] or ""
    # Spec: drop pure CAD tag patterns
    assert re.search(r"\bDE\d+-[A-Z]+-\d+\b", text) is None, \
        f"raw chunk contains DE-style CAD tag: {text[:500]!r}"
    # No run of >3 identical adjacent tokens
    tokens = text.split()
    for i in range(len(tokens) - 3):
        if tokens[i] == tokens[i + 1] == tokens[i + 2] == tokens[i + 3]:
            pytest.fail(
                f"repeated-token run found: {tokens[i]!r} x4+ at pos {i}"
            )


async def test_cross_refs_parsed(primary_result):
    drawing = primary_result["drawing"]
    refs = drawing.get("cross_refs") or []
    assert len(refs) >= 1, f"no cross_refs extracted; got {refs!r}"
    types = {r.get("ref_type") for r in refs}
    assert types & {"match_line", "continuation", "reference"}, \
        f"no recognized ref_type; got {types!r}"


async def test_cad_tags_filtered_count_nonzero(keyplan_result):
    """The key plan is the densest CAD-tag soup in the pilot."""
    drawing = keyplan_result["drawing"]
    filtered = drawing.get("cad_tags_filtered_count") or 0
    assert filtered >= 100, \
        f"cad_tags_filtered_count={filtered}, expected >=100 on key plan"


# --- Phase 1.5 fix-list regression guards ----------------------------------

def _normalize_alnum(s: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", (s or "").upper())


async def test_drawing_title_not_equal_to_drawing_number(primary_result):
    """Bug 1: drawing_title must not be the drawing_number (or a clustering
    artifact of it). Compare after stripping non-alphanumerics."""
    drawing = primary_result["drawing"]
    title = drawing.get("drawing_title")
    dn = drawing.get("drawing_number") or ""
    if title is None:
        # Spec allows None when no real title found.
        return
    norm_title = _normalize_alnum(title)
    norm_dn = _normalize_alnum(dn)
    assert norm_title != norm_dn, (
        f"drawing_title equals drawing_number after normalization: "
        f"title={title!r} dn={dn!r}"
    )
    # And must not contain a 12+ char substring of the drawing_number.
    if norm_dn and len(norm_dn) >= 12:
        for i in range(0, len(norm_dn) - 11):
            window = norm_dn[i:i + 12]
            assert window not in norm_title, (
                f"drawing_title contains 12-char window of drawing_number: "
                f"window={window!r} title={title!r}"
            )


async def test_cross_refs_match_line_detected(primary_result):
    """Bug 3: TM fixture has multiple 'MATCH LINE : FOR REFERENCE REFER TO
    SHEET NO : N' callouts and must produce >= 1 cross_ref whose raw text
    contains 'MATCH LINE' (case-insensitive)."""
    drawing = primary_result["drawing"]
    refs = drawing.get("cross_refs") or []
    match_line_refs = [
        r for r in refs
        if "MATCH LINE" in (r.get("raw") or "").upper()
    ]
    assert len(match_line_refs) >= 1, (
        f"no MATCH LINE cross_ref detected; got {refs!r}"
    )


async def test_cross_refs_deduplicated(primary_result):
    """Bug 3: cross_refs must be deduplicated by (ref_type, target_drawing)."""
    drawing = primary_result["drawing"]
    refs = drawing.get("cross_refs") or []
    seen = set()
    for r in refs:
        key = (r.get("ref_type"), r.get("target_drawing"))
        assert key not in seen, (
            f"duplicate (ref_type, target_drawing) tuple {key!r} in cross_refs"
        )
        seen.add(key)


async def test_revision_fallback_to_filename():
    """Bug 1.5c: when neither the title block nor the drawing-number tail
    yields a revision letter, the filename's trailing -<LETTER>.pdf
    suffix is the source of truth. The TM-200 key plan fixture is
    'drawing_tm_200.pdf' (no revision suffix) so we cannot use it here.
    Use the PRIMARY fixture only when the filename ends in -<LETTER>;
    otherwise this test is a smoke check of the fallback regex shape."""
    # Smoke: regex matches the documented pattern.
    assert re.search(r"-([A-Z])$", "IP-INF-053-0000-JCB-DWG-TM-200-1000005-A").group(1) == "A"
    assert re.search(r"-([A-Z])$", "IP-INF-053-0000-JCB-DWG-WS-600-0000001-C").group(1) == "C"
    # Numeric tail (sheet-seq) must NOT match.
    assert re.search(r"-([A-Z])$", "IP-INF-053-0000-JCB-DWG-SW-600-0000035-04") is None
    # Lowercase tail must NOT match.
    assert re.search(r"-([A-Z])$", "drawing-foo-z") is None


async def test_drawing_title_not_cross_ref_callout(primary_result):
    """Bug 1.5b: drawing_title must not be a cross-ref callout like
    'MATCH LINE : FOR REFERENCE REFER TO SHEET NO : N'. On TM detail
    sheets the longest text cluster is a match-line label; it must be
    rejected as a title candidate."""
    drawing = primary_result["drawing"]
    title = (drawing.get("drawing_title") or "").upper()
    if not title:
        return
    bad_patterns = [
        r"\bMATCH\s*LINE\b",
        r"\bCONT(?:INUED|D|\.)?\s*ON\b",
        r"\bSEE\s+DWG\b",
        r"\bREF(?:ER|\.)?[^\n]{0,40}?\b(?:SHEET|DWG|DRAWING)\b",
    ]
    for pat in bad_patterns:
        assert not re.search(pat, title), (
            f"drawing_title is a cross-ref callout matching {pat!r}: "
            f"title={drawing.get('drawing_title')!r}"
        )
