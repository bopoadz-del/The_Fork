"""Two real bugs surfaced by the 2026-07-25 fresh sweep triage.

Both had the answer's numbers sitting in the prompt, same family as the
payment_certificate / cash_flow figures-from-message fixes:

1. carbon_footprint_calculator computed ZERO for "12,000 m3 of C40
   concrete and 900t of rebar" — predefined dispatch sent empty params,
   so the quantities in the message were never parsed.
2. sympy_reason variance question ("planned SAR 4.2M vs actual SAR 5.1M")
   got the cost-grounding refusal — the gate didn't count the USER's own
   supplied figures as grounded, so it refused to echo them back.
"""

from __future__ import annotations

import pytest


class TestCarbonQuantitiesFromMessage:
    def test_extracts_concrete_and_rebar(self):
        from app.containers.construction.boq import _carbon_quantities_from_message
        q = _carbon_quantities_from_message(
            "calculate the embodied carbon for 12,000 m3 of C40 concrete and 900t of rebar"
        )
        assert q["concrete_m3"] == 12000.0
        assert q["rebar_kg"] == 900_000.0  # 900 tonnes -> kg

    @pytest.mark.asyncio
    async def test_carbon_action_nonzero_from_message(self):
        from app.containers.construction import ConstructionContainer
        r = await ConstructionContainer().carbon_footprint_calculator(
            {"message": "embodied carbon for 12,000 m3 of C40 concrete and 900t of rebar"},
            {},
        )
        assert r["status"] == "success"
        assert r["total_embodied_carbon_kg"] > 0
        # concrete 12000*250 + rebar 900000*1.9 = 3,000,000 + 1,710,000
        assert r["total_embodied_carbon_kg"] == pytest.approx(4_710_000.0, rel=1e-6)

    @pytest.mark.asyncio
    async def test_explicit_quantities_still_win(self):
        from app.containers.construction import ConstructionContainer
        r = await ConstructionContainer().carbon_footprint_calculator(
            {"quantities": {"concrete_m3": 100}, "message": "999999 m3 concrete"}, {},
        )
        assert r["total_embodied_carbon_kg"] == pytest.approx(25_000.0)  # 100*250


class TestCostGateGroundsUserFigures:
    def test_user_supplied_numbers_are_grounded(self):
        from app.agents.runtime import _cg_grounded_numbers
        messages = [{"role": "user",
                     "content": "variance analysis: planned SAR 4.2M vs actual SAR 5.1M"}]
        grounded = _cg_grounded_numbers("", messages)
        # 4.2M and 5.1M the user typed must be in the grounded set.
        assert any(abs(g - 4_200_000) <= 21000 for g in grounded)
        assert any(abs(g - 5_100_000) <= 25500 for g in grounded)

    def test_variance_answer_passes_the_gate(self):
        from app.agents.runtime import _cost_grounding_gate
        messages = [{"role": "user",
                     "content": "planned SAR 4.2M vs actual SAR 5.1M, what drove the overrun?"}]
        answer = ("The overrun is SAR 0.9M: actual SAR 5.1M against planned "
                  "SAR 4.2M, a 21% variance driven by rate escalation.")
        out = _cost_grounding_gate(answer, None, messages)
        assert out == answer  # echoing the user's own figures is not refused

    def test_fabricated_rate_still_refused(self, monkeypatch):
        from app.agents import runtime as rt
        # A figure the user NEVER supplied, no RAG, no tool -> still refused.
        messages = [{"role": "user", "content": "what's the rate for C40 concrete?"}]
        answer = "The rate for C40 concrete is SAR 465 per cubic metre."
        out = rt._cost_grounding_gate(answer, None, messages)
        assert out == rt._CG_REFUSAL
