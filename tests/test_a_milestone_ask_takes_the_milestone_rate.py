"""Which delay-damages rate answers the question is decided by the question.

The contract carries two daily rates: 0.1% of the Contract Price for the whole
of the Works, and 0.015% for each Milestone. The rate parser was written for
the whole-of-Works ask (leftover E1) and treats 0.015% as a "CoC lookalike" to
be avoided -- correct there, and wrong for a Milestone ask, which is exactly
what SET4 M3 asks:

    "What are the delay damages in SAR per calendar day for a single
     Milestone?"   -> 0.015% x ACA

Live 70948f0, asked 10 times: 4/10 produced the figure, the rest refused,
because the deterministic composer could not select the Milestone row -- it
never saw the question. Synthetic Contract Data text throughout.
"""
import pytest

from app.lib import construction_formulas_commercial as cc

# Synthetic: both rows, the cap, and a synthetic amount. No client figures.
CONTRACT_DATA = (
    "Contract Data 8.8.1 | Delay Damages for the whole of the Works: 0.1% of "
    "the Contract Price per calendar day. | Delay Damages per Milestone: 0.015% "
    "of the Contract Price per calendar day, for each of Milestone 1 through "
    "Milestone 10. | Maximum Amount of Delay Damages: 10% of the Contract Price. "
    "| Accepted Contract Amount (excluding VAT): SAR 2,000,000,000.00"
)
WHOLE_ASK = "What are the delay damages in SAR per calendar day for the whole of the Works?"
MILESTONE_ASK = "What are the delay damages in SAR per calendar day for a single Milestone?"
SECTION_ASK = "What are the delay damages in SAR per calendar day for a Section?"


@pytest.mark.parametrize("ask,expected", [
    (WHOLE_ASK, 0.1),
    (MILESTONE_ASK, 0.015),
    (SECTION_ASK, 0.015),
    ("", 0.1),  # no ask: the whole-of-Works default stands
])
def test_the_ask_selects_the_rate(ask, expected):
    assert cc.parse_delay_damages_rate_percent(CONTRACT_DATA, ask) == pytest.approx(expected)


def test_the_cap_is_never_a_rate():
    for ask in (WHOLE_ASK, MILESTONE_ASK):
        assert cc.parse_delay_damages_rate_percent(CONTRACT_DATA, ask) != pytest.approx(10.0)


def test_a_milestone_ask_is_recognised_and_a_general_one_is_not():
    assert cc.ask_is_about_a_milestone_or_section(MILESTONE_ASK)
    assert cc.ask_is_about_a_milestone_or_section(SECTION_ASK)
    assert cc.ask_is_about_a_milestone_or_section("delay damages for Milestone 3")
    assert not cc.ask_is_about_a_milestone_or_section(WHOLE_ASK)
    assert not cc.ask_is_about_a_milestone_or_section("what are the delay damages per day?")


def test_the_composed_daily_amount_uses_the_selected_rate():
    whole = cc.compose_delay_damages_daily_from_excerpts(WHOLE_ASK, CONTRACT_DATA)
    milestone = cc.compose_delay_damages_daily_from_excerpts(MILESTONE_ASK, CONTRACT_DATA)
    assert whole and milestone
    # 0.1% and 0.015% of SAR 2,000,000,000.
    assert whole["daily_amount"] == pytest.approx(2_000_000.0, rel=1e-6)
    assert milestone["daily_amount"] == pytest.approx(300_000.0, rel=1e-6)


def test_nothing_is_composed_without_both_operands():
    no_aca = CONTRACT_DATA.split("| Accepted Contract Amount")[0]
    assert cc.compose_delay_damages_daily_from_excerpts(MILESTONE_ASK, no_aca) is None
    no_rate = "Accepted Contract Amount (excluding VAT): SAR 2,000,000,000.00"
    assert cc.compose_delay_damages_daily_from_excerpts(MILESTONE_ASK, no_rate) is None


def test_a_milestone_ask_does_not_take_the_whole_works_rate_when_both_are_present():
    # The regression this guards: scoring a Milestone ask by whole-of-Works
    # preferences, which puts 0.1% on top and answers the wrong question.
    score_whole = cc.delay_damages_rate_preference_score(0.1, "whole of the Works Contract Data", MILESTONE_ASK)
    score_ms = cc.delay_damages_rate_preference_score(0.015, "per Milestone Contract Data", MILESTONE_ASK)
    assert score_ms > score_whole
