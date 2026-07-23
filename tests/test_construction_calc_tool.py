"""The `construction_calc` agent tool — deterministic formula dispatch.

Wires app.lib.construction_formulas into the agent as a single dispatcher tool
(schema in tool_definitions, dispatch in _run_tool_call). Real unit rates flow
in via `params`; the library defaults are indicative fallbacks only.
"""
import asyncio
import json

from app.agents.runtime import Agent


def _agent(blocks=None):
    return Agent(name="t", description="t", system_prompt="t",
                 allowed_blocks=blocks or [])


def _run(coro):
    return asyncio.run(coro)


def _call(agent, calculation, params=None):
    tc = {"id": "c1", "function": {
        "name": "construction_calc",
        "arguments": json.dumps({"calculation": calculation, "params": params or {}}),
    }}
    return _run(agent._run_tool_call(tc))


def _tool_names(agent):
    return {t["function"]["name"] for t in agent.tool_definitions()}


def test_tool_offered_only_when_construction_allowed():
    assert "construction_calc" in _tool_names(_agent(["construction"]))
    assert "construction_calc" not in _tool_names(_agent([]))


def test_enum_is_populated_from_registry():
    schema = next(t for t in _agent(["construction"]).tool_definitions()
                  if t["function"]["name"] == "construction_calc")
    enum = schema["function"]["parameters"]["properties"]["calculation"]["enum"]
    assert "cost_buildup_rebar" in enum
    assert "dewatering_uplift_check" in enum
    assert len(enum) >= 25


def test_dispatch_cost_buildup_passes_real_rate_through():
    agent = _agent(["construction"])
    r = _call(agent, "cost_buildup_rebar", {"quantity_kg": 1000, "material_price_sar_t": 3000})
    assert r["ok"] is True
    assert r["result"]["status"] == "success"
    assert r["result"]["result"]["material_sar_t"] == 3300  # 1t * 3000 * 1.10
    assert "priced-BOQ" in r["result"]["note"]  # grounding reminder present


def test_dispatch_engineering_calc():
    r = _call(_agent(["construction"]), "dewatering_uplift_check",
              {"water_depth": 23, "raft_thickness": 2, "floor_count": 5})
    assert r["ok"] is True
    assert r["result"]["result"]["fos"] == 0.38
    assert r["result"]["result"]["can_stop"] is False


def test_unknown_calculation_errors_honestly():
    r = _call(_agent(["construction"]), "no_such_calc")
    assert r["ok"] is False
    assert r["result"]["status"] == "error"
    assert "available" in r["result"]  # tells the model what it CAN call


def test_bad_params_return_the_real_signature():
    r = _call(_agent(["construction"]), "cost_buildup_rebar", {"wrong_arg": 1})
    assert r["ok"] is False
    assert "signature" in r["result"]
    assert "quantity_kg" in r["result"]["signature"]  # the real required input


def test_calc_queries_force_construction_calc():
    # Steering: unambiguous engineering-calc phrases force the tool so the model
    # runs the exact maths instead of doing (wrong) prose-math. Observed live:
    # gpt-4o-mini computed a dewatering check in prose and got it wrong.
    from app.agents.runtime import _forced_specific_tool
    avail = {"construction_calc", "generate_wbs"}
    for q in ("Run a dewatering uplift check for 23 m water depth",
              "crane capacity for a 20 t lift", "concrete mix design for W/C 0.48",
              "bearing pressure for a 3x3 m footing"):
        assert _forced_specific_tool([{"role": "user", "content": q}], avail) == "construction_calc", q
    # ...but a general question is NOT forced onto the calculator.
    assert _forced_specific_tool(
        [{"role": "user", "content": "what is this project about?"}], avail) is None


def test_business_calculators_dispatch_via_construction_calc():
    # EVM / payment / tender / risk are REFERENCED from construction_knowledge
    # (single source) and callable through the same tool — no duplicated maths.
    agent = _agent(["construction"])
    evm = _call(agent, "calculate_evm",
                {"bac": 1_000_000, "bcwp": 400_000, "bcws": 500_000, "acwp": 450_000})
    assert evm["ok"] is True
    assert evm["result"]["result"]["CPI"] == 0.889 and evm["result"]["result"]["SPI"] == 0.8
    risk = _call(agent, "score_risk", {"probability": 4, "impact": 5})
    assert risk["result"]["result"]["band"] == "RED"


def test_business_calc_queries_force_the_tool():
    from app.agents.runtime import _forced_specific_tool
    avail = {"construction_calc"}
    for q in ("compute the earned value for this month",
              "compute EVM: BAC 1M BCWP 400k",   # the bare "EVM" phrasing must also force
              "what is the interim payment due after retention",
              "run a tender evaluation on the three bids",
              "give me the risk score for probability 4 and impact 5"):
        assert _forced_specific_tool([{"role": "user", "content": q}], avail) == "construction_calc", q
    # "evm" must not false-trigger inside ordinary words (movement/pavement have
    # 'vem', not 'evm') — sanity that a non-calc sentence is not forced.
    assert _forced_specific_tool(
        [{"role": "user", "content": "summarise the site movement and pavement works"}], avail) is None
