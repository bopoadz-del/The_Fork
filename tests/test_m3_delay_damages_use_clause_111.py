"""M3: per-Milestone delay damages use clause 1.1.1, and name that source.

Live on 0b1d13a, every run:

    Delay damages per Milestone are SAR 5,864.76 per calendar day
    (0.015% of the Contract Price SAR 39,098,392.98).

SAR 39,098,392.98 is the partial Accepted Contract Amount on the rate
document's leading chunk. It is not clause 1.1.1. The rate document's
own Contract Price is SAR 1,754,504,456.25 (0.015% → SAR 263,175.67).
The line also names no source: the clause window contains the
neighbouring whole-of-Works wording, so the quote is dropped.

A whole-of-Works ask on the same shape takes the first excluding-VAT
clause it sees (a purchase-order 1.1.1 of SAR 55,000,000.00) instead of
the rate document's clause 1.1.1. A rewording whose partial row is not
marked excluding VAT refuses, even though clause 1.1.1 was retrieved
under another doc id.

Synthetic amounts only. The partial and the filled clause are the
figures already used by the E1 fixtures.
"""
from __future__ import annotations

import pytest

from app.agents.runtime import _graft_composed_delay_damages_daily
from app.lib import construction_formulas_commercial as cc

PARTIAL = 39_098_392.98
CONTRACT_PRICE = 1_754_504_456.25
PO_AMOUNT = 55_000_000.00
DAILY = 263_175.67
WHOLE_DAILY = 1_754_504.46

MILESTONE_ASKS = (
    "What are the delay damages in SAR per calendar day for a Milestone?",
    "What are the delay damages in SAR per calendar day for a single Milestone?",
    "What is the per-Milestone delay damages figure in SAR per calendar day?",
)
WHOLE_ASK = (
    "What are the delay damages in SAR per calendar day for the whole of the Works?"
)
REFUSAL = (
    "I can give you the rate, but not a SAR figure. The Contract Price "
    "is not in the retrieved Contract Data excerpts."
)
LIVE_PARTIAL_LINE = (
    "Delay damages per Milestone are SAR 5,864.76 per calendar day "
    "(0.015% of the Contract Price SAR 39,098,392.98)."
)

# The rate chunk states 0.015% and a partial Accepted Contract Amount.
# Clause 1.1.1 is retrieved, but under a different doc id, so the
# per-document price search never sees it.
MILESTONE_BUNDLE = (
    "[doc_id=rate chunk=0 score=0.940 class=project_corpus src=Contract-Data.pdf] "
    "CONTRACT DATA\n"
    "8.8.1: Delay Damages: 0.015% of the Contract Price per calendar day "
    "per Milestone\n"
    "8.8 Delay Damages for the whole of the Works: 0.1% of the Contract "
    "Price per calendar day\n"
    f"Accepted Contract Amount excluding VAT SAR {PARTIAL:,.2f}\n\n"
    "[doc_id=clause chunk=40 score=0.400 class=project_corpus src=Contract-Data.pdf] "
    "CONTRACT DATA\n"
    "1.1.1 Accepted Contract Amount excluding VAT "
    f"SAR {CONTRACT_PRICE:,.2f}\n"
)
# Same rate, but the partial row is not marked excluding VAT, so the
# price search scores it 0 and compose returns nothing. Clause 1.1.1
# is still in the retrieved excerpts.
REFUSAL_BUNDLE = (
    "[doc_id=rate chunk=0 score=0.940 class=project_corpus src=Contract-Data.pdf] "
    "CONTRACT DATA\n"
    "8.8.1: Delay Damages: 0.015% of the Contract Price per calendar day "
    "per Milestone\n"
    f"Accepted Contract Amount SAR {PARTIAL:,.2f}\n\n"
    "[doc_id=clause chunk=40 score=0.400 class=project_corpus src=Contract-Data.pdf] "
    "CONTRACT DATA\n"
    "1.1.1 Accepted Contract Amount excluding VAT "
    f"SAR {CONTRACT_PRICE:,.2f}\n"
)
# Whole-of-Works: a purchase order's own clause 1.1.1 leads. The rate
# document's clause 1.1.1 is later. First-match excl-VAT picks the PO.
SIBLING_BUNDLE = (
    "[doc_id=po chunk=0 score=0.990 class=project_corpus src=Purchase-Order.pdf] "
    "Purchase order\n"
    "1.1.1 Accepted Contract Amount excluding VAT "
    f"SAR {PO_AMOUNT:,.2f}\n\n"
    "[doc_id=rate chunk=0 score=0.800 class=project_corpus src=Contract-Data.pdf] "
    "CONTRACT DATA\n"
    "8.8 Delay Damages for the whole of the Works: 0.1% of the Contract "
    "Price per calendar day\n"
    "8.8.1: Delay Damages: 0.015% of the Contract Price per calendar day "
    "per Milestone\n"
    "1.1.1 Accepted Contract Amount excluding VAT "
    f"SAR {CONTRACT_PRICE:,.2f}\n"
)
# Top-k stopped on the partial. The loaded Contract Data volume still
# has the rate and clause 1.1.1.
VOLUME_ONLY = (
    "CONTRACT DATA\n"
    "8.8.1: Delay Damages: 0.015% of the Contract Price per calendar day "
    "per Milestone\n"
    "1.1.1 Accepted Contract Amount excluding VAT "
    f"SAR {CONTRACT_PRICE:,.2f}\n"
)
PARTIAL_ONLY = (
    "[doc_id=rate chunk=0 score=0.940 class=project_corpus src=Contract-Data.pdf] "
    "CONTRACT DATA\n"
    "8.8.1: Delay Damages: 0.015% of the Contract Price per calendar day "
    "per Milestone\n"
    "8.8 Delay Damages for the whole of the Works: 0.1% of the Contract "
    "Price per calendar day\n"
    f"Accepted Contract Amount excluding VAT SAR {PARTIAL:,.2f}\n"
)


def _line(ask: str, bundle: str) -> str:
    out = cc.compose_delay_damages_daily_from_excerpts(ask, bundle)
    assert out is not None, ask
    return cc.format_delay_damages_daily_line(out)


@pytest.mark.parametrize("ask", MILESTONE_ASKS)
def test_milestone_phrasings_use_clause_111_and_name_contract_data(ask):
    """Three phrasings of the live per-Milestone question."""
    out = cc.compose_delay_damages_daily_from_excerpts(ask, MILESTONE_BUNDLE)
    assert out is not None
    assert out["rate_percent"] == pytest.approx(0.015)
    assert out["contract_amount"] == pytest.approx(CONTRACT_PRICE)
    assert out["daily_amount"] == pytest.approx(DAILY)
    assert out["basis"] == "milestone"
    line = cc.format_delay_damages_daily_line(out)
    assert f"{DAILY:,.2f}" in line
    assert f"{CONTRACT_PRICE:,.2f}" in line
    assert "contract data" in line.lower()
    assert "8.8.1" in line
    assert "per Milestone" in line
    assert f"{PARTIAL:,.2f}" not in line
    assert "5,864.76" not in line
    assert "whole of the Works" not in line


def test_a_rewording_does_not_refuse_when_clause_111_was_retrieved():
    """The partial is not excluding-VAT, so compose used to return None."""
    line = _line(MILESTONE_ASKS[0], REFUSAL_BUNDLE)
    assert f"{DAILY:,.2f}" in line
    assert "contract data" in line.lower()
    assert "8.8.1" in line
    assert "not in the retrieved" not in line


def test_whole_of_works_sibling_uses_the_rate_documents_clause_111():
    """Same shape: another document's clause 1.1.1 must not be the base."""
    out = cc.compose_delay_damages_daily_from_excerpts(WHOLE_ASK, SIBLING_BUNDLE)
    assert out is not None
    assert out["rate_percent"] == pytest.approx(0.1)
    assert out["contract_amount"] == pytest.approx(CONTRACT_PRICE)
    assert out["daily_amount"] == pytest.approx(WHOLE_DAILY)
    assert out["basis"] == "whole"
    line = cc.format_delay_damages_daily_line(out)
    assert f"{WHOLE_DAILY:,.2f}" in line
    assert "contract data" in line.lower()
    assert f"{PO_AMOUNT:,.2f}" not in line
    assert "55,000.00" not in line
    assert "per Milestone" not in line


def test_graft_replaces_the_partial_when_clause_111_is_in_the_loaded_volume(
    monkeypatch,
):
    """Top-k compose already succeeded on the partial, so the volume scan
    used to be skipped. The loaded volume has clause 1.1.1.
    """
    monkeypatch.setattr(
        "app.core.rag.retriever.e1_compose_excerpts_from_loaded_cd_volume",
        lambda *a, **k: VOLUME_ONLY,
    )
    rag = {"role": "system", "content": "Reference context:\n" + PARTIAL_ONLY}
    msgs = [{"role": "user", "content": MILESTONE_ASKS[0]}]
    out = _graft_composed_delay_damages_daily(
        LIVE_PARTIAL_LINE, rag, msgs, project_id="p-fixture",
    )
    assert f"{DAILY:,.2f}" in out
    assert "contract data" in out.lower()
    assert "8.8.1" in out
    assert "5,864.76" not in out
    assert f"{PARTIAL:,.2f}" not in out


def test_graft_replaces_the_refusal_when_clause_111_is_in_the_excerpts():
    rag = {"role": "system", "content": "Reference context:\n" + REFUSAL_BUNDLE}
    msgs = [{"role": "user", "content": MILESTONE_ASKS[1]}]
    out = _graft_composed_delay_damages_daily(REFUSAL, rag, msgs)
    first = out.split("\n", 1)[0]
    assert f"{DAILY:,.2f}" in first
    assert "contract data" in first.lower()
    assert "not in the retrieved" not in first


# ── The live 0/6: the rescue joins chunks WITHOUT [doc_id=] markers, so
# compose reads one document. When the real clause-1.1.1 base and a partial
# ACA are BOTH in that one segment, the price search already prefers 1.1.1
# (score 7) over the partial (score 3). The live defect was upstream: the
# rescue capped aca_parts at [:3] and a run of partial rows pushed the 1.1.1
# base out of the excerpt entirely, so compose only ever saw the partial.
# The fix orders the clause-1.1.1 row first so the cap cannot drop it.

UNMARKED_NO_111 = (
    "CONTRACT DATA "
    "8.8.1: Delay Damages: 0.015% of the Contract Price per calendar day "
    "per Milestone "
    "8.8 Delay Damages for the whole of the Works: 0.1% of the Contract "
    "Price per calendar day "
    f"Accepted Contract Amount excluding VAT SAR {PARTIAL:,.2f} "
)
UNMARKED_WITH_111 = (
    UNMARKED_NO_111
    + f"1.1.1 Accepted Contract Amount excluding VAT SAR {CONTRACT_PRICE:,.2f} "
)


def test_milestone_uses_clause_111_over_a_partial_in_one_unmarked_bundle():
    """Same unmarked shape, but the clause 1.1.1 base is present: use it."""
    out = cc.compose_delay_damages_daily_from_excerpts(
        MILESTONE_ASKS[1], UNMARKED_WITH_111,
    )
    assert out is not None
    assert out["rate_percent"] == pytest.approx(0.015)
    assert out["contract_amount"] == pytest.approx(CONTRACT_PRICE)
    assert out["daily_amount"] == pytest.approx(DAILY)
    line = cc.format_delay_damages_daily_line(out)
    assert f"{PARTIAL:,.2f}" not in line
    assert "5,864.76" not in line


def test_text_states_clause_111_aca_tells_the_base_from_the_partial():
    assert not cc.text_states_clause_111_aca(
        f"Accepted Contract Amount excluding VAT SAR {PARTIAL:,.2f}",
    )
    assert cc.text_states_clause_111_aca(
        f"1.1.1 Accepted Contract Amount excluding VAT SAR {CONTRACT_PRICE:,.2f}",
    )


def test_the_rescue_orders_clause_111_ahead_of_the_partials():
    """The rescue caps aca_parts at [:3]; three partial excl-VAT rows used to
    push the real clause-1.1.1 base out of that window, so compose fell to a
    partial. Clause 1.1.1 must sort first and survive the cap."""
    from app.core.rag.retriever import _aca_parts_clause_111_first

    junk = [
        f"Accepted Contract Amount excluding VAT SAR {amt:,.2f}"
        for amt in (PARTIAL, 12_345_678.90, PO_AMOUNT)
    ]
    real = (
        "1.1.1 Accepted Contract Amount excluding VAT "
        f"SAR {CONTRACT_PRICE:,.2f}"
    )
    ordered = _aca_parts_clause_111_first(junk + [real])
    assert ordered[0] == real
    assert real in ordered[:3]
