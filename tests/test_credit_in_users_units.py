"""The calculator credit shows inputs in the units the user gave."""
from __future__ import annotations

import json


def test_the_credit_shows_a_converted_input_in_the_users_unit():
    """CORE: the credit echoed the converted internal value (t/m3) instead of
    the unit the user typed (kg/m3)."""
    from app.agents import citation_provenance as cp
    from app.lib.construction_formulas import run_calculation

    env = run_calculation("excavation_volume", {"length_m": "10 ft", "width_m": 5, "depth_m": 2})
    assert env["conversions"], env
    msgs = [{"role": "user", "content": "pit 10 ft by 5 m by 2 m"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "type": "function", "function": {
                "name": "construction_calc", "arguments": json.dumps(
                    {"calculation": "excavation_volume", "params": {"length_m": 3.048, "width_m": 5, "depth_m": 2}})}}]},
            {"role": "tool", "name": "construction_calc", "tool_call_id": "c1",
             "content": json.dumps({"name": "construction_calc", "ok": True, "result": env})}]
    credits = cp._calculator_credits(cp.build_evidence(None, msgs, tool_passages=True))
    assert credits and credits[0].inputs["length_m"] == "10 ft"
