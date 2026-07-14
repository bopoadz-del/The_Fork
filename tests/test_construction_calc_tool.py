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
