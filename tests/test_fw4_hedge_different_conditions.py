"""Different conditions are not a conflict about which document governs.

PR #714 stops the first-line guard from asking "which document's figure
is meant?" when the retrieved figures agree. Figures that answer
different materials or conditions were still treated as a conflict.

FIX WAVE 4 #1: ask which document only when two figures answer the same
material and condition, or when no condition separates them. Otherwise
state each figure bound to its own condition and to the document,
clause, or drawing that actually contains it.

Synthetic chunks only. Names start with FIXTURE-d-20260927-.
"""
from __future__ import annotations

import re

from app.agents.first_line_hard_rule import apply_first_line_hard_rule

P1A_ASK = (
    "Per the project specification, to what degree must structural "
    "backfill under foundations be compacted?"
)
S1_ASK = (
    "Per the project specification, what is the minimum concrete cover "
    "to reinforcement for foundations?"
)
S2_ASK = (
    "Per the project specification, what compaction is required under "
    "road pavement?"
)

DOC_98 = "FIXTURE-d-20260927-spec-backfill-98.pdf"
DOC_95 = "FIXTURE-d-20260927-spec-structural-fill-95.pdf"
DOC_90 = "FIXTURE-d-20260927-spec-general-fill-90.pdf"
BACKFILL_98 = (
    "Clause 8.4. Compaction of structural backfill under foundations "
    "to minimum 98% of maximum dry density of the modified proctor test."
)
STRUCTURAL_FILL_95 = (
    "Clause 8.1. Structural fill shall be compacted to 95% of maximum "
    "dry density of the modified proctor test."
)
GENERAL_FILL_90 = (
    "Clause 8.2. General fill shall be compacted to 90% of maximum "
    "dry density of the modified proctor test."
)

SPEC_COVER = "FIXTURE-d-20260927-spec-cover-deferral.pdf"
DWG_SOIL = "FIXTURE-d-20260927-dwg-cover-soil.pdf"
DWG_FOOT = "FIXTURE-d-20260927-dwg-cover-footing.pdf"
SPEC_DEFERS = (
    "Clause 3.1.25.8. Concrete cover to reinforcement shall be as shown "
    "on the drawings. This clause states no cover figure."
)
COVER_75 = (
    "Nominal concrete cover to reinforcement is 75 mm for concrete cast "
    "against or in contact with soil."
)
COVER_100 = (
    "Nominal concrete cover to reinforcement is 100 mm at the bottom "
    "of footings."
)

DOC_S2 = "FIXTURE-d-20260927-geotech-subgrade.pdf"
SUBGRADE_95 = (
    "The sub-grade layer under the road pavement shall be compacted to "
    "95% of maximum dry density."
)

# Same material and the same condition. The clause numbers differ on
# purpose: a clause id is not a condition, and must not suppress the ask.
DOC_SAME_A = "FIXTURE-d-20260927-spec-backfill-copy-a.pdf"
DOC_SAME_B = "FIXTURE-d-20260927-spec-backfill-copy-b.pdf"
SAME_98 = (
    "Clause 8.4. Compaction of structural backfill under foundations "
    "to minimum 98% of maximum dry density of the modified proctor test."
)
SAME_95 = (
    "Clause 9.1. Compaction of structural backfill under foundations "
    "to minimum 95% of maximum dry density of the modified proctor test."
)

HEDGE = "which document"
NARRATIVE = "I will answer from the retrieved context."


def _chunk(doc_id: str, name: str, text: str, score: str = "0.800") -> str:
    return (
        f"[doc_id={doc_id} chunk=0 score={score} class=project_corpus "
        f"src={name}] {text}"
    )


def _rag(*chunks: str) -> dict:
    return {"role": "system", "content": "\n\n".join(chunks)}


def _msgs(ask: str) -> list:
    return [{"role": "user", "content": ask}]


def _first(text: str) -> str:
    for line in (text or "").splitlines():
        if line.strip():
            return line.strip()
    return ""


def _sentences(text: str) -> list[str]:
    return [part.strip() for part in re.split(r"(?<=\.)\s+", text) if part.strip()]


def _percent_sentence(text: str, number: str) -> str:
    rx = re.compile(rf"(?i)\b{re.escape(number)}\s*(?:%|percent\b)")
    for sentence in _sentences(text):
        if rx.search(sentence):
            return sentence
    return ""


def _mm_sentence(text: str, number: str) -> str:
    rx = re.compile(rf"(?i)\b{re.escape(number)}\s*mm\b")
    for sentence in _sentences(text):
        if rx.search(sentence):
            return sentence
    return ""


def _has_percent(line: str, number: str) -> bool:
    return bool(re.search(rf"(?i)\b{re.escape(number)}\s*(?:%|percent\b)", line))


def test_p1a_different_materials_do_not_ask_which_document():
    """Structural backfill 98% (§8.4) is not structural fill 95% (§8.1).

    General fill at 90% is a third material. The first line leads with
    98% and names §8.4. It does not ask which document. 95% stays bound
    to structural fill, §8.1, and the document that states it.
    """
    out = apply_first_line_hard_rule(
        NARRATIVE,
        _rag(
            _chunk("f95", DOC_95, STRUCTURAL_FILL_95, "0.860"),
            _chunk("f90", DOC_90, GENERAL_FILL_90, "0.840"),
            _chunk("f98", DOC_98, BACKFILL_98, "0.820"),
        ),
        _msgs(P1A_ASK),
    )
    first = _first(out)
    assert HEDGE not in out.lower(), first
    assert "more than one" not in first.lower(), first
    assert first.lower().startswith("98%"), first
    assert "8.4" in first, first
    lead = _percent_sentence(first, "98")
    assert "structural backfill" in lead.lower(), lead
    assert "8.4" in lead, lead
    assert DOC_98 in lead, lead
    assert DOC_95 not in lead and DOC_90 not in lead, lead
    other = _percent_sentence(first, "95")
    assert "structural fill" in other.lower(), other
    assert "8.1" in other, other
    assert DOC_95 in other, other
    assert DOC_98 not in other, other
    third = _percent_sentence(first, "90")
    assert "general fill" in third.lower(), third
    assert DOC_90 in third, third
    assert DOC_98 not in third and DOC_95 not in third, third


def test_s1_different_cover_conditions_name_the_drawing_not_the_spec():
    """75 mm against soil and 100 mm at the bottom of footings.

    The specification clause has no cover figure. Each figure is bound
    to its condition and to the drawing that states it. Neither figure
    is credited to the specification.
    """
    out = apply_first_line_hard_rule(
        NARRATIVE,
        _rag(
            _chunk("spec", SPEC_COVER, SPEC_DEFERS, "0.900"),
            _chunk("soil", DWG_SOIL, COVER_75, "0.860"),
            _chunk("foot", DWG_FOOT, COVER_100, "0.840"),
        ),
        _msgs(S1_ASK),
    )
    first = _first(out)
    assert HEDGE not in out.lower(), first
    assert "more than one" not in first.lower(), first
    soil = _mm_sentence(first, "75")
    assert soil, first
    assert "contact with soil" in soil.lower() or "cast against" in soil.lower(), soil
    assert "soil" in soil.lower(), soil
    assert DWG_SOIL in soil, soil
    assert SPEC_COVER not in soil, soil
    foot = _mm_sentence(first, "100")
    assert foot, first
    assert "bottom of footing" in foot.lower(), foot
    assert DWG_FOOT in foot, foot
    assert SPEC_COVER not in foot, foot
    assert SPEC_COVER not in out, out
    assert "3.1.25.8" not in out, out


def test_s2_single_corpus_figure_is_credited_without_a_hedge():
    """One figure, 95%, named on the first line with its own document."""
    out = apply_first_line_hard_rule(
        NARRATIVE,
        _rag(_chunk("g95", DOC_S2, SUBGRADE_95)),
        _msgs(S2_ASK),
    )
    first = _first(out)
    assert HEDGE not in first.lower(), first
    assert "more than one" not in first.lower(), first
    assert first.lower().startswith("95%"), first
    assert _has_percent(first, "95"), first
    assert DOC_S2 in first, first
    assert not re.search(r"(?i)\b(?:90|98|100)\s*(?:%|percent\b)", first), first


def test_same_condition_from_two_documents_still_asks_which():
    """Negative control. Both figures are structural backfill under foundations.

    Clause 8.4 and clause 9.1 do not make them different conditions.
    Two documents, two figures, one condition: still ask which.
    """
    out = apply_first_line_hard_rule(
        NARRATIVE,
        _rag(
            _chunk("a", DOC_SAME_A, SAME_98, "0.860"),
            _chunk("b", DOC_SAME_B, SAME_95, "0.840"),
        ),
        _msgs(P1A_ASK),
    )
    first = _first(out)
    assert HEDGE in first.lower(), first
    assert _has_percent(first, "98") and _has_percent(first, "95"), first
    assert DOC_SAME_A in first and DOC_SAME_B in first, first
