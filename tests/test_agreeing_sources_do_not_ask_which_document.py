"""Do not ask which source governs when the retrieved figures agree.

The first-line guard in ``app.agents.first_line_hard_rule``
(``apply_first_line_hard_rule`` → ``_select`` → ``_ask_which_figure``)
opens the answer with "Retrieved context states more than one …
figure: …. Which document's figure is meant?" whenever more than one
hit remains. That is right only for a genuine conflict: different
figures for the same item. It is wrong when the sources agree,
including two package copies of one clause, and it is wrong when one
clause states a required degree and an optional higher degree of that
same item.

Live on d708b5d, P1a (backfill compaction for foundations) opened every
run with that question while both hits said 98%. S2 (compaction under
road pavement) stated 100% and 95% and then asked which document
governs. The S2 verdict is recorded on
``test_s2_optional_higher_degree_of_the_same_subgrade_does_not_ask_which_document``.
"""
from __future__ import annotations

import re

from app.agents.first_line_hard_rule import apply_first_line_hard_rule

P1A_ASK = (
    "Per the project specification, to what degree must structural "
    "backfill under foundations be compacted?"
)
S2_ASK = (
    "Per the project specification, what compaction is required under "
    "road pavement?"
)

# Two package copies of one clause. Neither filename is a specification,
# which is the live shape: the 98% sentence sits in Vol 5 Other Documents.
COPY_A = "SYN-2023-118 Package 1_Vol 5 - Other Documents (4 of 5).pdf"
COPY_B = "SYN-2023-118 G2 Infra P1_Vol 5 - Other Documents (4 of 5).pdf"
BACKFILL_98 = (
    "Compaction of structural backfill under foundations to minimum 98% of "
    "maximum dry density of the modified proctor test."
)

# Same item, two specifications, two figures. A disambiguation question
# is still allowed here.
SPEC_98 = "SYN-SPEC-098 Particular Specification Earthworks.pdf"
SPEC_95 = "SYN-SPEC-095 Particular Specification Earthworks Rev B.pdf"
BACKFILL_95 = (
    "Compaction of structural backfill under foundations to minimum 95% of "
    "maximum dry density of the modified proctor test."
)

# S2 stand-in. One geotech excerpt, one sub-grade, two percents.
VOL5 = "SYN-OD-005 Vol 5 Other Documents Geotech.pdf"
SUBGRADE = (
    "Section 9.1 Touch grade. The top twenty centimeters of the subgrade "
    "layer under the road pavement shall be compacted to ninety five percent "
    "(95%) of maximum dry density to get a minimum CBR value of 25. The "
    "material could be compacted to 98% or even 100% of maximum dry density "
    "to get a minimum CBR value of 25 under the approval of the engineer. "
    "Section 9.3 embankment fill uses that same sub-grade requirement."
)

HEDGE = "which document's figure is meant"
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


def _has_percent(line: str, number: str) -> bool:
    return bool(re.search(rf"(?i)\b{re.escape(number)}\s*(?:%|percent\b)", line))


def test_identical_figures_in_package_copies_do_not_ask_which_document():
    """P1a. Both package copies state 98% for the same backfill clause.

    The first line states that 98% and cites a copy. It does not ask
    which document's figure is meant.
    """
    out = apply_first_line_hard_rule(
        NARRATIVE,
        _rag(
            _chunk("a", COPY_A, BACKFILL_98, "0.860"),
            _chunk("b", COPY_B, BACKFILL_98, "0.840"),
        ),
        _msgs(P1A_ASK),
    )
    first = _first(out)
    assert HEDGE not in first.lower(), first
    assert "more than one" not in first.lower(), first
    assert _has_percent(first, "98"), first
    assert COPY_A in first or COPY_B in first, first


def test_genuine_conflict_on_the_same_item_may_ask_which_document():
    """Guard. 98% and 95% both describe structural backfill under foundations.

    Different figures for the same item may still ask which document's
    figure is meant. A fix that stops every question would fail here.
    """
    out = apply_first_line_hard_rule(
        NARRATIVE,
        _rag(
            _chunk("s98", SPEC_98, BACKFILL_98, "0.860"),
            _chunk("s95", SPEC_95, BACKFILL_95, "0.840"),
        ),
        _msgs(P1A_ASK),
    )
    first = _first(out)
    assert HEDGE in first.lower(), first
    assert _has_percent(first, "98") and _has_percent(first, "95"), first
    assert SPEC_98 in first and SPEC_95 in first, first


def test_s2_optional_higher_degree_of_the_same_subgrade_does_not_ask_which_document():
    """S2 verdict: same item, not a conflict, and not different layers.

    The live Vol 5 geotech clause (pavement subgrade, sections 9.1 touch
    grade, 9.2 cut, and 9.3 embankment fill) requires the sub-grade to be
    compacted to 95% of maximum dry density for a minimum CBR of 25. The
    same sentence says that material could be compacted to 98% or even
    100% of maximum dry density, still for that CBR, under the engineer's
    approval. 100% is an optional higher degree of the same sub-grade,
    not a second layer and not another document disagreeing. Listing the
    two percents and asking which document governs is wrong. The first
    line states the specified 95% and cites the geotech excerpt.
    """
    out = apply_first_line_hard_rule(
        NARRATIVE,
        _rag(_chunk("v5", VOL5, SUBGRADE)),
        _msgs(S2_ASK),
    )
    first = _first(out)
    assert HEDGE not in first.lower(), first
    assert "more than one" not in first.lower(), first
    assert _has_percent(first, "95"), first
    assert VOL5 in first, first
