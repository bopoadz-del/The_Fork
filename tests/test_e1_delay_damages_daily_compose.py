"""OLD-pack E1: compose delay-damages rate × ACA into a daily SAR figure.

Live Master Corpus (tip a65cebb):

* A5 PASS — excerpts state ``0.1% of the Contract Price per calendar day``.
* A2 PASS — excerpts state the Accepted Contract Amount.
* E1 FAIL — the answer quoted contract metadata / sources and never
  multiplied. Expected SAR 1,754,504.46/day = 0.1% of the net ACA
  SAR 1,754,504,456.25 (the incl-VAT twin is SAR 2,017,680,124.69).

This path is compose-only. It must not invent a rate or an amount, must
not steal A5 (rate-string lookup), and must not duplicate the A5 rate
rescue (#503). Kill-switch ``COMPOSE_DELAY_DAMAGES_DAILY=0`` restores
the FAIL (sources quoted, no SAR/day).
"""
from __future__ import annotations

import json
from pathlib import Path

from app.agents.runtime import (
    _CG_REFUSAL,
    _cost_grounding_gate,
    _graft_composed_delay_damages_daily,
    _postprocess_answer,
)
from app.lib.construction_formulas_commercial import (
    answer_states_daily_amount,
    compose_delay_damages_daily_from_excerpts,
    delay_damages_daily,
    parse_accepted_contract_amount,
    parse_delay_damages_rate_percent,
    query_asks_delay_damages_daily_amount,
)


CATALOG = json.loads(
    (Path(__file__).parent / "fixtures" / "ui_phys" / "questions.json")
    .read_text(encoding="utf-8")
)
A5_ASK = CATALOG["cases"]["A5"]["ask"]
E1_ASK = CATALOG["cases"]["E1"]["ask"]
LIVE_PREFIX = "Answer only from the client project documents. "
LIVE_E1 = LIVE_PREFIX + (
    "Calculate the delay damages per calendar day in SAR for the "
    "whole of the Works."
)
LIVE_A5 = LIVE_PREFIX + A5_ASK

# Fixture figures already used elsewhere in this repo. Not a live leak.
NET_ACA = 1_754_504_456.25
GROSS_ACA = 2_017_680_124.69
DAILY = 1_754_504.46
RATE = "0.1% of the Contract Price per calendar day"

RATE_ROW = (
    "CONTRACT DATA particulars — filled-in amount / duration / percentage.\n"
    f"8.8 Delay Damages for the whole of the Works: {RATE}"
)
CAP_ROW = (
    "CONTRACT DATA particulars — filled-in amount / duration / percentage.\n"
    "8.8 Maximum amount of delay damages: 10% of the Accepted Contract Amount"
)
GC_POINTER = (
    "Sub-Clause 8.8 Delay Damages. The Contractor shall pay delay damages "
    "for the whole of the Works at the rate stated in the Contract Data "
    "for every calendar day."
)
NET_ACA_ROW = (
    "CONTRACT DATA particulars — filled-in amount / duration / percentage.\n"
    "1.1.1 Accepted Contract Amount excluding VAT | "
    f"SAR {NET_ACA:,.2f}"
)
GROSS_ACA_ROW = (
    "CONTRACT DATA particulars — filled-in amount / duration / percentage.\n"
    "1.1.1 Accepted Contract Amount including VAT | "
    f"SAR {GROSS_ACA:,.2f}"
)
SCANNED_RATE = (
    "CONTRACT DATA\n8.8\nDelay \nDamages for the whole of the Works\n"
    f"{RATE}\n"
)
SCANNED_NET_ACA = (
    "CONTRACT DATA\nAccepted\nContract\nAmount (excluding VAT)\n"
    f"SAR {NET_ACA:,.2f}\n"
)
SOURCES_ONLY = (
    "Delay Damages are stated in the Contract Data for the whole of the "
    "Works. Sources: DD-2023-118 Conditions of Contract; Contract Data."
)
DEMO_RATE_ROW = (
    "CONTRACT DATA particulars.\n"
    "8.8 Delay Damages for the whole of the Works: "
    "0.2% of the Contract Price per calendar day"
)
DEMO_ACA_ROW = (
    "CONTRACT DATA particulars.\n"
    "1.1.1 Accepted Contract Amount excluding VAT | SAR 8,640,000.00"
)


def _sys(*chunk_texts: str) -> dict:
    body = "\n\n".join(
        f"[doc_id=doc{i} chunk={i} score=0.80] {t}"
        for i, t in enumerate(chunk_texts)
    )
    return {"role": "system", "content": "Reference context:\n" + body}


def test_catalog_e1_ask_is_frozen():
    assert E1_ASK == (
        "Calculate the delay damages per calendar day in SAR for the "
        "whole of the Works."
    )
    assert A5_ASK == "What are the Delay Damages for the whole of the Works?"


def test_e1_ask_is_compose_class_and_a5_is_not():
    assert query_asks_delay_damages_daily_amount(E1_ASK)
    assert query_asks_delay_damages_daily_amount(LIVE_E1)
    assert not query_asks_delay_damages_daily_amount(A5_ASK)
    assert not query_asks_delay_damages_daily_amount(LIVE_A5)
    assert not query_asks_delay_damages_daily_amount(
        "What is the maximum amount of delay damages?"
    )
    assert not query_asks_delay_damages_daily_amount(
        "If Milestone 1 is 30 days late, what are the milestone delay damages?"
    )


def test_rate_parser_takes_the_daily_rate_not_the_cap_or_pointer():
    assert parse_delay_damages_rate_percent(RATE_ROW) == 0.1
    assert parse_delay_damages_rate_percent(SCANNED_RATE) == 0.1
    assert parse_delay_damages_rate_percent(CAP_ROW) is None
    assert parse_delay_damages_rate_percent(GC_POINTER) is None
    assert parse_delay_damages_rate_percent(DEMO_RATE_ROW) == 0.2


def test_aca_parser_prefers_excluding_vat_when_both_are_present():
    both = NET_ACA_ROW + "\n" + GROSS_ACA_ROW
    assert parse_accepted_contract_amount(both) == (NET_ACA, "SAR")
    assert parse_accepted_contract_amount(SCANNED_NET_ACA) == (NET_ACA, "SAR")
    assert parse_accepted_contract_amount(GROSS_ACA_ROW) == (GROSS_ACA, "SAR")
    assert parse_accepted_contract_amount(CAP_ROW) is None
    assert parse_accepted_contract_amount(RATE_ROW) is None


def test_compose_live_e1_is_point_one_percent_of_net_aca():
    excerpts = "\n\n".join((RATE_ROW, NET_ACA_ROW, GROSS_ACA_ROW, CAP_ROW))
    out = compose_delay_damages_daily_from_excerpts(LIVE_E1, excerpts)
    assert out is not None
    assert out["daily_amount"] == DAILY
    assert out["rate_percent"] == 0.1
    assert out["contract_amount"] == NET_ACA
    assert out["currency"] == "SAR"


def test_compose_demo_pack_e1_is_point_two_percent_of_8640000():
    # UI-PHYS E1: 0.2% × 8,640,000.00 = 17,280.00
    excerpts = "\n\n".join((DEMO_RATE_ROW, DEMO_ACA_ROW))
    out = compose_delay_damages_daily_from_excerpts(E1_ASK, excerpts)
    assert out is not None
    assert out["daily_amount"] == 17_280.00
    assert out["rate_percent"] == 0.2


def test_compose_does_not_invent_when_an_operand_is_missing():
    assert compose_delay_damages_daily_from_excerpts(LIVE_E1, RATE_ROW) is None
    assert compose_delay_damages_daily_from_excerpts(LIVE_E1, NET_ACA_ROW) is None
    assert compose_delay_damages_daily_from_excerpts(LIVE_A5, RATE_ROW + NET_ACA_ROW) is None


def test_kill_switch_restores_the_no_compose_fail(monkeypatch):
    monkeypatch.setenv("COMPOSE_DELAY_DAMAGES_DAILY", "0")
    excerpts = "\n\n".join((RATE_ROW, NET_ACA_ROW))
    assert compose_delay_damages_daily_from_excerpts(LIVE_E1, excerpts) is None


def test_graft_states_the_daily_figure_when_the_model_quoted_sources_only():
    rag = _sys(RATE_ROW, NET_ACA_ROW, GROSS_ACA_ROW)
    msgs = [{"role": "user", "content": LIVE_E1}]
    out = _graft_composed_delay_damages_daily(SOURCES_ONLY, rag, msgs)
    assert "1,754,504.46" in out
    assert "per calendar day" in out.lower()
    assert any(
        m.get("role") == "tool" and "delay_damages_daily" in str(m.get("content"))
        for m in msgs
    )


def test_graft_replaces_a_fabricated_daily_figure():
    rag = _sys(RATE_ROW, NET_ACA_ROW)
    msgs = [{"role": "user", "content": LIVE_E1}]
    out = _graft_composed_delay_damages_daily(
        "The daily delay damages are SAR 9,936.00.", rag, msgs,
    )
    assert "1,754,504.46" in out
    assert "9,936" not in out


def test_graft_is_a_no_op_when_the_composed_figure_is_already_stated():
    rag = _sys(RATE_ROW, NET_ACA_ROW)
    msgs = [{"role": "user", "content": LIVE_E1}]
    already = (
        "Delay damages are SAR 1,754,504.46/day "
        "(0.1% of Accepted Contract Amount SAR 1,754,504,456.25)."
    )
    assert _graft_composed_delay_damages_daily(already, rag, msgs) == already


def test_postprocess_e1_sources_only_states_the_daily_sar_figure():
    rag = _sys(RATE_ROW, NET_ACA_ROW, GROSS_ACA_ROW)
    msgs = [{"role": "user", "content": LIVE_E1}]
    out = _postprocess_answer(SOURCES_ONLY, rag, msgs)
    assert "1,754,504.46" in out
    assert out != _CG_REFUSAL
    assert "upload your priced BOQ" not in out.lower()


def test_cost_gate_still_refuses_a_fabricated_daily_when_aca_is_absent():
    """Rate-only excerpt cannot ground an invented SAR/day (existing E1 gate)."""
    ask = LIVE_E1
    msgs = [{"role": "user", "content": ask}]
    rag = _sys(RATE_ROW)
    grafted = _graft_composed_delay_damages_daily(
        "The daily delay damages are SAR 9,936.00.", rag, msgs,
    )
    assert grafted == "The daily delay damages are SAR 9,936.00."
    out = _cost_grounding_gate(grafted, rag, msgs)
    assert out == _CG_REFUSAL


def test_cost_gate_allows_the_composed_product_when_both_operands_are_present():
    rag = _sys(RATE_ROW, NET_ACA_ROW)
    msgs = [{"role": "user", "content": LIVE_E1}]
    composed = delay_damages_daily(
        rate_percent=0.1, contract_amount=NET_ACA, currency="SAR",
    )
    answer = (
        f"The daily delay damages are SAR {composed['daily_amount']:,.2f}."
    )
    grafted = _graft_composed_delay_damages_daily(answer, rag, msgs)
    out = _cost_grounding_gate(grafted, rag, msgs)
    assert out != _CG_REFUSAL
    assert "1,754,504.46" in out


def test_answer_states_daily_amount_accepts_formatted_and_compact():
    assert answer_states_daily_amount("SAR 1,754,504.46/day", DAILY)
    assert answer_states_daily_amount("SAR 1754504.46 per calendar day", DAILY)
    assert not answer_states_daily_amount(SOURCES_ONLY, DAILY)
