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
    aca_amount_is_toy_example,
    answer_states_daily_amount,
    chunk_has_real_accepted_contract_amount,
    compose_delay_damages_daily_from_excerpts,
    delay_damages_daily,
    delay_damages_rate_is_coc_lookalike,
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


def test_scanned_rate_adjacent_to_scanned_aca_still_parses():
    """Live E1: rate and ACA are neighboring scanned chunks, no prefix."""
    adjacent = SCANNED_RATE + "\n\n" + SCANNED_NET_ACA
    assert parse_accepted_contract_amount(adjacent) == (NET_ACA, "SAR")
    assert parse_delay_damages_rate_percent(adjacent) == 0.1
    with_gross = adjacent + "\n\n" + GROSS_ACA_ROW
    assert parse_accepted_contract_amount(with_gross) == (NET_ACA, "SAR")


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


def test_graft_replaces_a_cannot_calculate_refusal():
    rag = _sys(RATE_ROW, NET_ACA_ROW, GROSS_ACA_ROW)
    msgs = [{"role": "user", "content": LIVE_E1}]
    refusal = (
        "I cannot calculate the delay damages per calendar day from the "
        "reference context provided. Retrieved excerpts include the "
        "Specification table of contents."
    )
    out = _graft_composed_delay_damages_daily(refusal, rag, msgs)
    assert "1,754,504.46" in out
    assert "cannot calculate" not in out.lower()


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


LIVE_E1_ACA_ONLY = (
    "The Accepted Contract Amount including VAT is "
    f"SAR {GROSS_ACA:,.2f}."
)


def test_e1_is_not_an_a2_including_vat_election():
    from app.core.rag.retriever import query_asks_for_aca_including_vat

    assert query_asks_delay_damages_daily_amount(LIVE_E1)
    assert not query_asks_for_aca_including_vat(LIVE_E1)
    assert not query_asks_for_aca_including_vat(E1_ASK)


def test_graft_replaces_including_vat_aca_only_answer():
    """Live leftover E1 after #523: answer was only the A2 particular."""
    rag = _sys(RATE_ROW, NET_ACA_ROW, GROSS_ACA_ROW)
    msgs = [{"role": "user", "content": LIVE_E1}]
    out = _graft_composed_delay_damages_daily(LIVE_E1_ACA_ONLY, rag, msgs)
    assert "1,754,504.46" in out
    assert "per calendar day" in out.lower()
    assert out != LIVE_E1_ACA_ONLY
    first = out.split("\n", 1)[0]
    assert "1,754,504.46" in first
    assert f"{GROSS_ACA:,.2f}" not in first


def test_postprocess_e1_cannot_answer_with_only_including_vat_aca():
    rag = _sys(RATE_ROW, NET_ACA_ROW, GROSS_ACA_ROW)
    msgs = [{"role": "user", "content": LIVE_E1}]
    out = _postprocess_answer(LIVE_E1_ACA_ONLY, rag, msgs)
    assert "1,754,504.46" in out
    assert "per calendar day" in out.lower()
    assert out != LIVE_E1_ACA_ONLY
    assert out != _CG_REFUSAL
    # Must not remain an A2-shaped including-VAT-only particular.
    body = out.strip()
    assert body != LIVE_E1_ACA_ONLY
    assert not (
        "including VAT" in body
        and "1,754,504.46" not in body
        and "per calendar day" not in body.lower()
    )


def test_a2_graft_does_not_steal_an_e1_daily_compose():
    from app.agents.runtime import _graft_asked_contract_particular

    rag = _sys(RATE_ROW, NET_ACA_ROW, GROSS_ACA_ROW)
    msgs = [{"role": "user", "content": LIVE_E1}]
    daily = (
        "Delay damages for the whole of the Works are "
        f"SAR {DAILY:,.2f} per calendar day "
        f"(0.1% of Accepted Contract Amount SAR {NET_ACA:,.2f})."
    )
    assert _graft_asked_contract_particular(daily, rag, msgs) == daily
    assert _graft_asked_contract_particular(LIVE_E1_ACA_ONLY, rag, msgs) == (
        LIVE_E1_ACA_ONLY
    )


# Live leftover E1 on c5c6dfa: compose elected a FIDIC worked-example
# ACA of SAR 10,000,000 from Contract Data 8.8 chunks 9–11
# (0.1% → SAR 10,000/day) instead of the filled excl-VAT row.
TOY_ACA = 10_000_000.00
TOY_DAILY = 10_000.00
TOY_ACA_ROW = (
    "CONTRACT DATA particulars — filled-in amount / duration / percentage.\n"
    "1.1.1 Accepted Contract Amount | SAR 10,000,000.00"
)
# Stained excl-VAT: the 8.8 worked example names the net base so
# first-in-excl-bucket used to elect 10M over the filled 1.1.1 row.
TOY_EXAMPLE_WINDOW = (
    "Volume 1 - Conditions of Contract. Sub-Clause 8.8 Delay Damages. "
    "The Contractor shall pay delay damages for the whole of the Works "
    f"at {RATE}. For example, if the Accepted Contract Amount "
    "excluding VAT is SAR 10,000,000.00, the daily amount is "
    "SAR 10,000.00."
)
LIVE_E1_TOY_ANSWER = (
    "Delay damages for the whole of the Works are "
    "SAR 10,000.00 per calendar day "
    "(0.1% of Accepted Contract Amount SAR 10,000,000.00)."
)


def test_ten_million_is_a_toy_aca_unless_it_is_a_filled_excl_row():
    assert aca_amount_is_toy_example(TOY_ACA, "Accepted Contract Amount")
    assert aca_amount_is_toy_example(
        TOY_ACA,
        "For example, if the Accepted Contract Amount is SAR 10,000,000.00",
    )
    assert not aca_amount_is_toy_example(
        TOY_ACA,
        "1.1.1 Accepted Contract Amount excluding VAT | SAR 10,000,000.00",
    )
    assert not aca_amount_is_toy_example(NET_ACA, NET_ACA_ROW)
    assert not aca_amount_is_toy_example(8_640_000.00, DEMO_ACA_ROW)


def test_aca_parser_rejects_toy_ten_million_when_excl_vat_is_also_present():
    """Leftover E1: 10M example must lose to the filled excl-VAT ACA."""
    both = "\n\n".join((TOY_EXAMPLE_WINDOW, NET_ACA_ROW, GROSS_ACA_ROW))
    assert parse_accepted_contract_amount(both) == (NET_ACA, "SAR")
    unlabeled_then_excl = "\n\n".join((TOY_ACA_ROW, NET_ACA_ROW))
    assert parse_accepted_contract_amount(unlabeled_then_excl) == (NET_ACA, "SAR")
    excl_then_toy = "\n\n".join((NET_ACA_ROW, TOY_EXAMPLE_WINDOW))
    assert parse_accepted_contract_amount(excl_then_toy) == (NET_ACA, "SAR")


def test_compose_leftover_e1_prefers_excl_vat_over_toy_ten_million():
    excerpts = "\n\n".join((
        RATE_ROW, TOY_EXAMPLE_WINDOW, TOY_ACA_ROW, NET_ACA_ROW, GROSS_ACA_ROW,
    ))
    out = compose_delay_damages_daily_from_excerpts(LIVE_E1, excerpts)
    assert out is not None
    assert out["daily_amount"] == DAILY
    assert out["rate_percent"] == 0.1
    assert out["contract_amount"] == NET_ACA
    assert out["currency"] == "SAR"
    assert out["contract_amount"] != TOY_ACA
    assert out["daily_amount"] != TOY_DAILY


def test_graft_replaces_the_live_ten_thousand_per_day_fail():
    rag = _sys(RATE_ROW, TOY_EXAMPLE_WINDOW, NET_ACA_ROW, GROSS_ACA_ROW)
    msgs = [{"role": "user", "content": LIVE_E1}]
    out = _graft_composed_delay_damages_daily(LIVE_E1_TOY_ANSWER, rag, msgs)
    assert "1,754,504.46" in out
    assert "10,000.00" not in out.split("\n", 1)[0]
    first = out.split("\n", 1)[0]
    assert "1,754,504.46" in first
    assert "10,000,000.00" not in first


def test_postprocess_e1_cannot_answer_with_the_toy_ten_million_aca():
    rag = _sys(RATE_ROW, TOY_EXAMPLE_WINDOW, NET_ACA_ROW)
    msgs = [{"role": "user", "content": LIVE_E1}]
    out = _postprocess_answer(LIVE_E1_TOY_ANSWER, rag, msgs)
    assert "1,754,504.46" in out
    assert out != LIVE_E1_TOY_ANSWER
    assert out != _CG_REFUSAL
    assert "10,000,000.00" not in out.split("\n", 1)[0]


def test_toy_aca_kill_switch_restores_electing_ten_million(monkeypatch):
    monkeypatch.setenv("COMPOSE_REJECT_E1_TOY_ACA", "0")
    # Example language is ignored; 10M is first in the excl-VAT bucket.
    both = "\n\n".join((TOY_EXAMPLE_WINDOW, NET_ACA_ROW))
    assert parse_accepted_contract_amount(both) == (TOY_ACA, "SAR")


# Live leftover E1 after #529: Contract Data template "insert" / a
# neighboring "for example" 8.8 window stained the filled excl-VAT
# ACA as a toy. Rescue then dropped it, compose had no money operand,
# and the cost-grounding gate refused.
INSERT_STAINED_NET_ACA = (
    "CONTRACT DATA particulars — filled-in amount / duration / percentage.\n"
    "1.1.1 insert Accepted Contract Amount excluding VAT | "
    f"SAR {NET_ACA:,.2f}"
)
SCANNED_INSERT_NET_ACA = (
    "CONTRACT DATA\n1.1.1\nAccepted\nContract\nAmount (excluding VAT)\n"
    f"[insert amount] SAR {NET_ACA:,.2f}\n"
)


def test_insert_template_cue_does_not_stain_the_filled_excl_vat_aca():
    """Non-10M excl-VAT is never the FIDIC worked example."""
    assert not aca_amount_is_toy_example(
        NET_ACA,
        "1.1.1 insert Accepted Contract Amount excluding VAT | "
        f"SAR {NET_ACA:,.2f}",
    )
    assert not aca_amount_is_toy_example(
        NET_ACA,
        "For example, if the Accepted Contract Amount excluding VAT is "
        f"SAR {NET_ACA:,.2f}",
    )
    assert chunk_has_real_accepted_contract_amount(INSERT_STAINED_NET_ACA)
    assert chunk_has_real_accepted_contract_amount(SCANNED_INSERT_NET_ACA)
    assert parse_accepted_contract_amount(INSERT_STAINED_NET_ACA) == (
        NET_ACA, "SAR",
    )
    assert parse_accepted_contract_amount(SCANNED_INSERT_NET_ACA) == (
        NET_ACA, "SAR",
    )


def test_compose_does_not_elect_toy_ten_million_when_it_is_the_only_aca():
    """#529 leftover: toy-only excerpts must not emit SAR 10,000/day."""
    excerpts = "\n\n".join((RATE_ROW, TOY_EXAMPLE_WINDOW, TOY_ACA_ROW))
    assert parse_accepted_contract_amount(excerpts) is None
    assert compose_delay_damages_daily_from_excerpts(LIVE_E1, excerpts) is None


def test_compose_and_graft_from_insert_stained_excl_vat():
    excerpts = "\n\n".join((RATE_ROW, TOY_EXAMPLE_WINDOW, INSERT_STAINED_NET_ACA))
    out = compose_delay_damages_daily_from_excerpts(LIVE_E1, excerpts)
    assert out is not None
    assert out["daily_amount"] == DAILY
    assert out["contract_amount"] == NET_ACA
    rag = _sys(RATE_ROW, TOY_EXAMPLE_WINDOW, INSERT_STAINED_NET_ACA)
    msgs = [{"role": "user", "content": LIVE_E1}]
    posted = _postprocess_answer(_CG_REFUSAL, rag, msgs)
    assert "1,754,504.46" in posted
    assert posted != _CG_REFUSAL
    assert "upload your priced BOQ" not in posted.lower()


def test_graft_replaces_cost_refusal_when_late_excl_vat_is_present():
    """Live 0a0ee76: model/gate emitted the BOQ refusal over 8.8 chunks 9–11.

    When the filled excl-VAT row is in the excerpts (after the late-doc
    scan), graft must state SAR 1,754,504.46/day and must not keep the
    refusal or the toy 10,000/day product.
    """
    excerpts = "\n\n".join(
        (TOY_EXAMPLE_WINDOW, TOY_EXAMPLE_WINDOW, TOY_EXAMPLE_WINDOW, NET_ACA_ROW),
    )
    out = compose_delay_damages_daily_from_excerpts(LIVE_E1, excerpts)
    assert out is not None
    assert out["daily_amount"] == DAILY
    assert out["contract_amount"] == NET_ACA
    assert out["daily_amount"] != TOY_DAILY
    rag = _sys(TOY_EXAMPLE_WINDOW, TOY_EXAMPLE_WINDOW, TOY_EXAMPLE_WINDOW, NET_ACA_ROW)
    msgs = [{"role": "user", "content": LIVE_E1}]
    posted = _postprocess_answer(_CG_REFUSAL, rag, msgs)
    assert "1,754,504.46" in posted
    assert posted != _CG_REFUSAL
    assert "upload your priced BOQ" not in posted.lower()
    assert "10,000.00" not in posted.split("\n", 1)[0]
    assert "0.015%" not in posted
    assert "263,175.67" not in posted


# Live leftover E1 after #535 (4242b86): compose first-matched CoC 8.7/8.8
# 0.015% of the excl-VAT ACA → SAR 263,175.67/day. Contract Data 8.8 is
# 0.1% of the Contract Price. Sources stayed on chunks 9–11.
COC_015_WINDOW = (
    "Volume 1 - Conditions of Contract. Sub-Clause 8.7 Delay Damages. "
    "Delay damages for the whole of the Works are 0.015% of the "
    f"Accepted Contract Amount SAR {NET_ACA:,.2f} per calendar day."
)
LIVE_E1_015_ANSWER = (
    "Delay damages for the whole of the Works are "
    "SAR 263,175.67 per calendar day "
    f"(0.015% of Accepted Contract Amount SAR {NET_ACA:,.2f})."
)
LOOKALIKE_DAILY = 263_175.67


def test_point_zero_one_five_of_aca_is_a_coc_lookalike():
    assert delay_damages_rate_is_coc_lookalike(0.015, COC_015_WINDOW)
    assert not delay_damages_rate_is_coc_lookalike(0.1, RATE_ROW)
    assert not delay_damages_rate_is_coc_lookalike(0.2, DEMO_RATE_ROW)


def test_rate_parser_prefers_contract_data_point_one_over_coc_015():
    """First-match used to return 0.015% when the CoC window led."""
    both = "\n\n".join((COC_015_WINDOW, RATE_ROW, NET_ACA_ROW))
    assert parse_delay_damages_rate_percent(COC_015_WINDOW) is None
    assert parse_delay_damages_rate_percent(RATE_ROW) == 0.1
    assert parse_delay_damages_rate_percent(both) == 0.1
    led_by_015 = "\n\n".join((COC_015_WINDOW, COC_015_WINDOW, RATE_ROW))
    assert parse_delay_damages_rate_percent(led_by_015) == 0.1


def test_compose_does_not_elect_015_when_excl_vat_aca_is_present():
    """(b) 0.015% × excl-VAT ACA must not become 263,175.67."""
    lookalike_only = "\n\n".join((COC_015_WINDOW, NET_ACA_ROW))
    assert compose_delay_damages_daily_from_excerpts(LIVE_E1, lookalike_only) is None
    both = "\n\n".join((COC_015_WINDOW, RATE_ROW, NET_ACA_ROW))
    out = compose_delay_damages_daily_from_excerpts(LIVE_E1, both)
    assert out is not None
    assert out["daily_amount"] == DAILY
    assert out["rate_percent"] == 0.1
    assert out["contract_amount"] == NET_ACA
    assert out["daily_amount"] != LOOKALIKE_DAILY


def test_graft_replaces_the_live_015_per_day_fail():
    rag = _sys(COC_015_WINDOW, RATE_ROW, NET_ACA_ROW)
    msgs = [{"role": "user", "content": LIVE_E1}]
    out = _graft_composed_delay_damages_daily(LIVE_E1_015_ANSWER, rag, msgs)
    assert "1,754,504.46" in out
    assert "263,175.67" not in out.split("\n", 1)[0]
    assert "0.015%" not in out.split("\n", 1)[0]


def test_postprocess_e1_cannot_answer_with_015_when_cd_rate_is_present():
    """(a) refuse is replaced when excl-VAT ACA is in the loaded rows."""
    rag = _sys(COC_015_WINDOW, RATE_ROW, NET_ACA_ROW)
    msgs = [{"role": "user", "content": LIVE_E1}]
    posted = _postprocess_answer(_CG_REFUSAL, rag, msgs)
    assert "1,754,504.46" in posted
    assert posted != _CG_REFUSAL
    assert "upload your priced BOQ" not in posted.lower()
    assert "263,175.67" not in posted
    assert "0.015%" not in posted.split("\n", 1)[0]


def test_lookalike_rate_kill_switch_restores_electing_015(monkeypatch):
    monkeypatch.setenv("COMPOSE_REJECT_E1_LOOKALIKE_RATE", "0")
    assert parse_delay_damages_rate_percent(COC_015_WINDOW) == 0.015
    out = compose_delay_damages_daily_from_excerpts(
        LIVE_E1, "\n\n".join((COC_015_WINDOW, NET_ACA_ROW)),
    )
    assert out is not None
    assert out["rate_percent"] == 0.015
    assert out["daily_amount"] == LOOKALIKE_DAILY
