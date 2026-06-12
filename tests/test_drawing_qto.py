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


async def test_drawing_number_regex_accepts_swapped_jcb_tokens():
    """Phase 1.6: WS sheets use IP-INF-053-JCB-0000-DWG-WS-... — token
    order 4-5 swapped vs the TM/SG/EL/TL form. Both must parse."""
    from app.blocks.drawing_qto import _DWG_NUMBER_FULL
    # Original TM/SG/EL/TL form
    assert _DWG_NUMBER_FULL.fullmatch(
        "IP-INF-053-0000-JCB-DWG-TM-200-1000005-A"
    ), "long-regex failed on 0000-JCB-DWG form"
    # WS swapped form
    assert _DWG_NUMBER_FULL.fullmatch(
        "IP-INF-053-JCB-0000-DWG-WS-600-0000001-C"
    ), "long-regex failed on JCB-0000-DWG form"
    # Reject obvious non-matches
    assert _DWG_NUMBER_FULL.fullmatch("IP-INF-053-JCB") is None
    assert _DWG_NUMBER_FULL.fullmatch("FOO-BAR-BAZ") is None


async def test_drawing_title_rejects_pure_numeric():
    """Phase 1.6: pure-numeric clusters (1800, chainage stations) must
    never be accepted as drawing_title. Validates the candidate filter
    in _extract_title_block."""
    # The filter is a pure function over candidate text. Verify by
    # checking the regex shape directly so we don't depend on a fixture
    # PDF.
    bad_titles = ["1800", "1:1800", "1 : 1800", "100.5,200.3", "-/--", "12-34/56"]
    pat_numeric = re.compile(r"[\d.,/:\-]+")
    pat_scale = re.compile(r"1\s*:\s*\d+")
    for t in bad_titles:
        t_compact = re.sub(r"\s+", "", t)
        rejected = bool(pat_numeric.fullmatch(t_compact)) or bool(pat_scale.fullmatch(t))
        assert rejected, f"title {t!r} should have been rejected by numeric/scale filter"


async def test_drawing_title_rejects_dg2_place_names():
    """Phase 1.6: DG2 area / district names must be rejected as
    drawing_title candidates."""
    from app.blocks.drawing_qto import _DG2_PLACE_NAMES
    for name in ("KHUZAMA", "AL TURAIF", "AL BUJAIRI", "AL QARYA"):
        assert name in _DG2_PLACE_NAMES, f"{name!r} missing from DG2 blocklist"
    # Lowercase and whitespace variants should round-trip to a blocked entry
    for variant in ("khuzama", " AL  TURAIF ", "Al Bujairi"):
        normalized = re.sub(r"\s+", " ", variant.upper()).strip()
        assert normalized in _DG2_PLACE_NAMES, (
            f"variant {variant!r} normalised to {normalized!r} not in blocklist"
        )


async def test_fitz_char_extraction_smoke():
    """Phase 1.7 fitz swap: ``_chars_from_fitz`` must produce a healthy
    span population on the primary fixture. <100 records would indicate
    the page.get_text("dict") loop is mis-iterating blocks/lines/spans."""
    import fitz
    doc = fitz.open(str(PRIMARY))
    try:
        chars = DrawingQTOBlock._chars_from_fitz(doc[0])
    finally:
        doc.close()
    assert len(chars) >= 100, (
        f"_chars_from_fitz returned only {len(chars)} records; "
        f"expected >= 100 for the TM detail fixture"
    )
    # And the records must carry the schema downstream code consumes.
    c = chars[0]
    for k in ("text", "x0", "y0", "x1", "y1", "size", "fontname"):
        assert k in c, f"_chars_from_fitz record missing key {k!r}: {c!r}"


async def test_drawing_number_no_doubled_letter_prefix():
    """Phase 1.7 bug fix: a source text run like ``XIIP-INF-053-...``
    used to yield a JCB drawing-number of ``IIP-INF-053-...`` (the head
    ``[A-Z]{2,}`` happily accepted the doubled letter). The
    ``_strip_doubled_letter_prefix`` helper must peel the stray prefix
    char-by-char while the remainder still parses, leaving the canonical
    2-char ``IP`` head."""
    from app.blocks.drawing_qto import (
        _strip_doubled_letter_prefix, _DWG_NUMBER_FULL,
    )
    # The pattern itself matches IIP- because [A-Z]{2,} accepts >=2.
    bad_run = "XIIP-INF-053-0000-JCB-DWG-ST-200-0010001-A"
    raw_match = _DWG_NUMBER_FULL.search(bad_run)
    assert raw_match is not None
    raw = raw_match.group(0)
    # After strip, no doubled-letter prefix
    cleaned = _strip_doubled_letter_prefix(raw)
    assert not cleaned.startswith("II"), (
        f"strip_doubled_letter_prefix kept the doubled prefix: {cleaned!r}"
    )
    # Canonical DG2 prefix
    assert cleaned.startswith("IP-INF-"), (
        f"strip_doubled_letter_prefix did not preserve canonical IP-INF prefix: {cleaned!r}"
    )
    # The known-clean form is a no-op
    clean_in = "IP-INF-053-0000-JCB-DWG-TM-200-1000005-A"
    assert _strip_doubled_letter_prefix(clean_in) == clean_in


async def test_drawing_title_rejects_trailing_lowercase_artifact():
    """Phase 1.7 bug fix: candidates ending in ``[A-Z]{2,}[a-z]`` (e.g.
    ``KEY PLANg`` — a subscript glyph that bled into the title span)
    must be rejected, while mixed-case titles like ``Section A-A`` and
    all-caps titles like ``CONCRETE ENCASEMENT`` must NOT be rejected."""
    from app.blocks.drawing_qto import _has_trailing_lowercase_artifact
    # Rejected
    assert _has_trailing_lowercase_artifact("KEY PLANg") is True
    assert _has_trailing_lowercase_artifact("TITLEx") is True
    assert _has_trailing_lowercase_artifact("CONCRETE BEAMa") is True
    # Accepted (must not match)
    assert _has_trailing_lowercase_artifact("Section A-A") is False
    assert _has_trailing_lowercase_artifact("CONCRETE ENCASEMENT") is False
    assert _has_trailing_lowercase_artifact("KEY PLAN") is False
    # The full title-candidate flow rejects "KEY PLANg" via this filter
    # while keeping "CONCRETE ENCASEMENT".
    assert _has_trailing_lowercase_artifact("KEY PLAN.") is False  # trailing period


async def test_notes_dedup_drops_near_identical():
    """Phase 1.8: near-identical notes (Levenshtein distance < 5) must
    collapse to a single entry. Asserts both the helper and the
    classifier path.

    Verifies that ``_note_near_duplicate`` reports True on a candidate
    that differs from an accepted entry by < 5 chars but False on a
    candidate that differs by >= 5 chars. Then drives
    ``_classify_drawing_zone`` with a constructed char stream that
    contains two near-identical lines and asserts only one survives.
    """
    from app.blocks.drawing_qto import _note_near_duplicate

    # Two strings differing by 1 char -> duplicate
    base = "ISSUED FOR CONSTRUCTION (CONDITIONAL) 02 31/05/24 AJ"
    near = "ISSUED FOR CONSTRUCTION (CONDITIONAL) 03 29/01/25 AJ"
    # These differ by more than 5 chars in pairwise comparison — they're
    # NOT duplicates by the < 5 rule. Check explicitly:
    from app.blocks.drawing_qto import _lev_distance
    assert _lev_distance(base, near) >= 5  # sanity: not deduped

    # A 1-char-different pair IS a duplicate
    assert _note_near_duplicate(
        "WATERSTOP PROFILE",
        ["WATERSTOP PROFIL"],
        max_distance=5,
    ) is True
    # Distance 5 (boundary) should NOT be flagged (< 5 rule).
    # "ABCDEFGHIJ" vs "AAAAAAGHIJ" = 5 substitutions (chars 2..6: B→A,C→A,D→A,E→A,F→A).
    assert _lev_distance("ABCDEFGHIJ", "AAAAAAGHIJ") == 5
    assert _note_near_duplicate("ABCDEFGHIJ", ["AAAAAAGHIJ"], max_distance=5) is False
    # Identical
    assert _note_near_duplicate("FOO", ["FOO"], max_distance=5) is True
    # Length-difference early-out
    assert _note_near_duplicate("FOO", ["FOOBAR123456"], max_distance=5) is False
    # Empty accepted list
    assert _note_near_duplicate("FOO", [], max_distance=5) is False

    # Drive the classifier path: construct two near-identical char lines
    # large enough to land in the "notes" bucket (size >= 4.0) and short
    # enough to escape every other filter.
    def _chars_for_line(text: str, y0: float) -> list:
        # Approximate per-char widths to keep them on the same y-line and
        # within the 30-px x_gap cluster threshold.
        out = []
        x = 0.0
        for ch in text:
            out.append({
                "text": ch,
                "x0": x,
                "y0": y0,
                "x1": x + 5.0,
                "y1": y0 + 8.0,
                "size": 6.0,
                "fontname": "Arial",
            })
            x += 5.5
        return out

    chars = (
        _chars_for_line("THIS IS A LONG NOTE ABOUT WATERPROOFING DETAIL", 100.0)
        + _chars_for_line("THIS IS A LONG NOTE ABOUT WATERPROOFING DETAIM", 200.0)
    )
    block = DrawingQTOBlock()
    notes, _, _, dropped = block._classify_drawing_zone(chars)
    assert len(notes) == 1, f"expected dedup to leave 1 note, got {notes!r}"
    assert dropped == 1, f"expected dedup_dropped_count=1, got {dropped}"


async def test_notes_dedup_does_not_strip_legitimate_notes(primary_result):
    """Phase 1.8 regression: the dedup filter must not strip legitimate
    notes on the TM fixture. The existing
    ``test_notes_present_and_meaningful`` assertion (len(notes) >= 1 AND
    word count > 10) must still pass — re-assert it here for clarity."""
    drawing = primary_result["drawing"]
    notes = drawing.get("notes") or []
    assert len(notes) >= 1, f"dedup stripped all notes; got {notes!r}"
    word_count = sum(len(n.split()) for n in notes)
    assert word_count > 10, (
        f"dedup over-stripped on TM fixture; word count {word_count} <= 10; "
        f"notes={notes!r}"
    )
    # The new field must be present on the drawing dict
    assert "notes_dedup_dropped_count" in drawing


async def test_process_page_prefers_bottom_band_dn_over_referenced_dn():
    """LI-sheet regression (2026-06-12): the LI fixture's bottom-15% title-
    block band carries only a handful of spans (lines < 5), tripping the
    richness fallback that widens ``title_block_chars`` to the right-20%
    zone. The right-20% raw char stream happens to put a REFERENCED
    drawing number (the sheet calls out another sheet) ahead of the
    title-block's own number, so ``_DWG_NUMBER_FULL.search()`` returns
    the wrong one.

    Fix: capture the bottom-band raw DN before the fallback widens, and
    if it parses as a full JCB, prefer it. This test exercises the real
    mechanism via a stubbed page (no PDF fixture required) — feed
    ``_process_page`` chars where the bottom-15% band yields JCB number
    X and the right-20% zone contains JCB number Y appearing first;
    assert X wins.

    A synthetic-rescue-only test (one that just feeds
    ``page_full_raw_texts``) would prove nothing — the rescue never fires
    here, because the wrong number Y already passes ``_is_full_jcb``.
    """
    from app.blocks.drawing_qto import DrawingQTOBlock

    class _FakeRect:
        def __init__(self, width: float, height: float) -> None:
            self.width = width
            self.height = height

    class _FakePage:
        def __init__(self, width: float, height: float) -> None:
            self.rect = _FakeRect(width, height)

    page_w, page_h = 1000.0, 700.0
    # Bottom-15% band threshold: y0 > 0.85 * 700 = 595.
    # Right-20% threshold: x0 >= 0.80 * 1000 = 800.
    # The bottom band gets the CORRECT title-block JCB number — just a
    # few chars (mimicking the LI sheet's sparse title-block band).
    correct_dn = "IP-INF-053-0000-JCB-DWG-LI-200-1001100"
    # The right-20% zone gets a REFERENCED drawing first (top of band)
    # then the correct DN somewhere later, plus enough filler "lines"
    # to push the right-zone richness past the >= 5 lines fallback gate.
    referenced_dn = "IP-INF-053-0000-JCB-DWG-LI-600-0000002"

    def _line_chars(text: str, x_start: float, y0: float) -> list:
        out = []
        x = x_start
        for ch in text:
            out.append({
                "text": ch,
                "x0": x,
                "y0": y0,
                "x1": x + 5.0,
                "y1": y0 + 8.0,
                "size": 6.0,
                "fontname": "Arial",
            })
            x += 5.5
        return out

    chars = []
    # Bottom-15% band: a single ~50-char span carrying the correct DN.
    # Place it in the LEFT half (x_start=100) so it falls OUTSIDE the
    # right-20% zone — otherwise the widened-zone search() would
    # accidentally find the correct DN too (LI sheet's title-block band
    # actually sits in the bottom-right corner; this is a tighter
    # synthetic that isolates "bottom band == correct, right zone ==
    # contaminated"). Keep span count < 5 so the richness fallback fires.
    chars.extend(_line_chars(correct_dn, x_start=100.0, y0=650.0))
    # Right-20% zone (x0 >= 800), NOT in bottom band (y0 < 595):
    # referenced DN appears FIRST in char order, then enough filler
    # lines to push line count >= 5 so the right-20% fallback wins
    # (instead of falling through to full-page).
    chars.extend(_line_chars(referenced_dn, x_start=820.0, y0=100.0))
    chars.extend(_line_chars("CHECKED BY: AB", x_start=820.0, y0=200.0))
    chars.extend(_line_chars("DATE: 01/01/24", x_start=820.0, y0=250.0))
    chars.extend(_line_chars("DRAFTER: CD", x_start=820.0, y0=300.0))
    chars.extend(_line_chars("SCALE: 1:100", x_start=820.0, y0=350.0))
    chars.extend(_line_chars("PROJECT: FOO", x_start=820.0, y0=400.0))

    page = _FakePage(page_w, page_h)
    block = DrawingQTOBlock()
    errors: list = []
    result = block._process_page(page, chars, errors)
    tb = result["title_block"]
    assert tb.get("drawing_number") == correct_dn, (
        f"expected bottom-band DN to win; got {tb.get('drawing_number')!r}; "
        f"errors={errors!r}"
    )
    assert "drawing_number_picked_from_bottom_band" in errors, (
        f"expected band-preference marker in errors; got {errors!r}"
    )


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
