"""A Section delay-damages rate uses the Contract Price, and says so.

Live SET4.1 M3, "What are the delay damages in SAR per calendar day for
a section?":

  * 4/6 runs multiplied 0.015% by SAR 144,042,486.50 (SAR 21,606.37).
    That figure arrived in a higher-ranked excerpt — the executed cover
    and a pre-kickoff note — ahead of clause 1.1.1.
  * 2/6 runs multiplied by the Contract Price SAR 1,754,504,456.25
    (SAR 263,175.67) and then called it the whole-of-the-Works rate.

The rate is per Section. The base is the Contract Price, excluding VAT.
"""
from __future__ import annotations

import pytest

from app.lib import construction_formulas_commercial as cc

SECTION_ASK = "What are the delay damages in SAR per calendar day for a section?"
WHOLE_ASK = (
    "What are the delay damages in SAR per calendar day for the whole of the Works?"
)
CONTRACT_PRICE = 1_754_504_456.25
SECTION_AMOUNT = 144_042_486.50
DAILY = 263_175.67

# The row the contract uses for a Section. Quoted back, not paraphrased
# into the whole-of-the-Works sentence.
SECTION_CLAUSE = (
    "8.8.1 Delay Damages (for a Section): 0.015% of the Contract Price "
    "per calendar day"
)
WHOLE_CLAUSE = (
    "8.8.1 Delay Damages (for the whole of the Works): 0.1% of the "
    "Contract Price per calendar day"
)
PRICE_ROW = (
    "1.1.1 Accepted Contract Amount: SAR 1,754,504,456.25 excluding VAT"
)

# Live shape: the wrong amount is in other documents and is concatenated
# first. The Section rate and the Contract Price share the conditions.
LIVE_BUNDLE = (
    "[doc_id=REDACTED chunk=0] Form of Agreement | Section contract value "
    f"SAR {SECTION_AMOUNT:,.2f} | "
    "[doc_id=REDACTED chunk=11] GCH Pre-Kick off meeting | "
    f"Accepted Contract Amount excluding VAT SAR {SECTION_AMOUNT:,.2f} | "
    "[doc_id=coc chunk=20] CONTRACT DATA | "
    f"{SECTION_CLAUSE} | {WHOLE_CLAUSE} | {PRICE_ROW}"
)

# The wrong amount leads, inside one window, with only "Contract Price"
# nearby — the bleed that used to elect it as the Accepted Contract Amount.
UNMARKED_BLEED = (
    f"Delay Damages (for a Section): 0.015% of the Contract Price per "
    f"calendar day. Section contract value SAR {SECTION_AMOUNT:,.2f}. "
    f"{PRICE_ROW}. {WHOLE_CLAUSE}."
)


def test_a_section_ask_uses_the_contract_price_not_the_other_amount():
    out = cc.compose_delay_damages_daily_from_excerpts(SECTION_ASK, LIVE_BUNDLE)
    assert out is not None
    assert out["rate_percent"] == pytest.approx(0.015)
    assert out["contract_amount"] == pytest.approx(CONTRACT_PRICE)
    assert out["daily_amount"] == pytest.approx(DAILY)
    assert out["basis"] == "section"


def test_the_line_says_per_section_and_quotes_the_clause():
    out = cc.compose_delay_damages_daily_from_excerpts(SECTION_ASK, LIVE_BUNDLE)
    line = cc.format_delay_damages_daily_line(out)
    assert line.startswith("Delay damages per Section are SAR 263,175.67 ")
    assert "whole of the Works" not in line
    assert "1,754,504,456.25" in line
    assert "144,042,486.50" not in line
    assert "The contract states:" in line
    assert "for a Section" in line
    assert "0.015% of the Contract Price per calendar day" in line


def test_a_leading_section_sum_does_not_beat_clause_1_1_1():
    out = cc.compose_delay_damages_daily_from_excerpts(SECTION_ASK, UNMARKED_BLEED)
    assert out is not None
    assert out["contract_amount"] == pytest.approx(CONTRACT_PRICE)
    assert out["daily_amount"] == pytest.approx(DAILY)


def test_another_documents_amount_is_not_the_section_base():
    """The rate's document has no Contract Price. Do not borrow 144M."""
    bundle = (
        "[doc_id=coc chunk=20] CONTRACT DATA | "
        f"{SECTION_CLAUSE} | "
        "[doc_id=REDACTED chunk=11] GCH Pre-Kick off meeting | "
        f"Accepted Contract Amount excluding VAT SAR {SECTION_AMOUNT:,.2f}"
    )
    assert cc.compose_delay_damages_daily_from_excerpts(SECTION_ASK, bundle) is None


def test_the_whole_of_the_works_line_is_unchanged():
    bundle = (
        "[doc_id=coc chunk=20] CONTRACT DATA | "
        f"{WHOLE_CLAUSE} | {SECTION_CLAUSE} | {PRICE_ROW}"
    )
    out = cc.compose_delay_damages_daily_from_excerpts(WHOLE_ASK, bundle)
    assert out is not None
    assert out["rate_percent"] == pytest.approx(0.1)
    assert out["contract_amount"] == pytest.approx(CONTRACT_PRICE)
    assert out.get("basis") == "whole"
    line = cc.format_delay_damages_daily_line(out)
    assert line.startswith("Delay damages for the whole of the Works are ")
    assert "per Section" not in line
    assert "The contract states:" not in line
