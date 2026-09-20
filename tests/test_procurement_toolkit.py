"""Live Phase 2: procurement_list_generator must be in the chat toolkit.

Second ask for a procurement list failed on project-assistant: the model
said ``procurement_list_generator`` is not in its toolkit and the turn
was forced onto ``construction_calc``. First ask sometimes worked via
the generic ``construction`` / ``boq_processor`` path.

The action is already registered on ConstructionContainer. This file
locks the missing half: expose it as a top-level tool (same pattern as
``generate_wbs`` / ``cash_flow_forecast``), force it for tool-shaped
procurement asks, and never treat ``construction_calc`` as the primary
path when both are available.

Works in both boot modes — advertise + force do not import the
construction kit; dispatch is mocked.
"""
from __future__ import annotations

import asyncio
import json

from app.agents.runtime import Agent, _forced_specific_tool, get_agent, load_agents


# Feature-matrix / live Phase 2 asks. Prompt 2 is the one that stayed on
# project-assistant (below_routing_gate) and then missed the tool.
_PROCUREMENT_ASKS = (
    "generate a procurement list for the MEP package",
    "what materials do we need to buy for the substructure works?",
    "use procurement_list_generator for the MEP buy list",
    "call procurement_list_generator",
    "produce a material list for the steel package",
)


def _agent(blocks=None):
    return Agent(
        name="t",
        description="t",
        system_prompt="t",
        allowed_blocks=blocks or [],
    )


def _tool_names(agent, project_id=None):
    return {
        (t.get("function") or {}).get("name") or t.get("name")
        for t in agent.tool_definitions(project_id=project_id)
    }


def _run(coro):
    return asyncio.run(coro)


def _call(agent, name, args):
    return _run(agent._run_tool_call({
        "id": "p1",
        "function": {"name": name, "arguments": json.dumps(args)},
    }))


# ── Exposure ────────────────────────────────────────────────────────────────


def test_procurement_list_generator_offered_when_construction_allowed():
    assert "procurement_list_generator" in _tool_names(_agent(["construction"]))
    assert "procurement_list_generator" not in _tool_names(_agent([]))
    assert "procurement_list_generator" not in _tool_names(
        _agent(["boq_processor"])
    )


def test_project_assistant_advertises_procurement_list_generator():
    """The live UI agent is project-assistant. If the name is missing
    here the model will say it is not in the toolkit."""
    load_agents()
    pa = get_agent("project-assistant")
    names = _tool_names(pa, project_id="fence-check")
    assert "procurement_list_generator" in names
    assert "construction_calc" in names  # still present; not the primary path


def test_construction_capable_agents_advertise_the_action():
    load_agents()
    for agent_id in ("project-assistant", "heavy-reasoning", "quantity-surveyor"):
        names = _tool_names(get_agent(agent_id), project_id="fence-check")
        assert "procurement_list_generator" in names, agent_id


# ── Forced-tool routing ─────────────────────────────────────────────────────


def test_procurement_asks_force_procurement_list_generator_not_calc():
    avail = {"procurement_list_generator", "construction_calc", "generate_wbs"}
    for q in _PROCUREMENT_ASKS:
        msgs = [{"role": "user", "content": q}]
        forced = _forced_specific_tool(msgs, avail)
        assert forced == "procurement_list_generator", (q, forced)


def test_second_ask_still_forces_procurement_not_calc():
    """Conversation continuity: first ask may have gone construction /
    boq_processor; the second user turn must still force the action."""
    avail = {"procurement_list_generator", "construction_calc", "construction"}
    msgs = [
        {"role": "user", "content": "generate a procurement list for the MEP package"},
        {"role": "assistant", "content": "Here is the MEP procurement list."},
        {
            "role": "user",
            "content": "what materials do we need to buy for the substructure works?",
        },
    ]
    assert _forced_specific_tool(msgs, avail) == "procurement_list_generator"


def test_procurement_ask_is_not_forced_onto_construction_calc_when_missing():
    """If the toolkit omitted the action, do not steal the turn onto the
    calculator — that is the live miss (model then invents a calc)."""
    avail = {"construction_calc", "generate_wbs"}
    forced = _forced_specific_tool(
        [{"role": "user", "content": "generate a procurement list for the MEP package"}],
        avail,
    )
    assert forced != "construction_calc"
    assert forced is None


def test_schedule_procurement_question_is_not_forced():
    """'how long is procurement on the schedule?' is RAG / WBS Q&A."""
    avail = {"procurement_list_generator", "construction_calc", "generate_wbs"}
    assert _forced_specific_tool(
        [{"role": "user", "content": "how long is procurement on the schedule?"}],
        avail,
    ) is None


def test_named_calculator_still_forces_construction_calc():
    avail = {"procurement_list_generator", "construction_calc"}
    assert _forced_specific_tool(
        [{"role": "user", "content": "run a dewatering uplift check for 23 m water depth"}],
        avail,
    ) == "construction_calc"
    assert _forced_specific_tool(
        [{"role": "user", "content": "calculate material consumption for 100 m3 of concrete"}],
        avail,
    ) == "construction_calc"


# ── Dispatch ────────────────────────────────────────────────────────────────


def test_synthetic_tool_hits_the_container(monkeypatch):
    calls = {}

    class FakeBlock:
        async def procurement_list_generator(self, input_data, params):
            calls["input"] = input_data
            calls["params"] = params
            return {
                "status": "success",
                "action": "procurement_list",
                "total_items": 1,
                "procurement_list": [{"item": "Rebar", "quantity": 3.2}],
            }

    import app.dependencies as deps
    monkeypatch.setattr(deps, "get_block_instance", lambda name: FakeBlock())
    r = _call(_agent(["construction"]), "procurement_list_generator", {
        "quantities": {"Rebar": {"quantity": 3.2, "unit": "t"}},
        "budget": "200000",
    })
    assert r["ok"], r
    assert r["name"] == "procurement_list_generator"
    assert calls["params"]["quantities"]["Rebar"]["quantity"] == 3.2
    assert float(calls["params"]["budget"]) == 200000


def test_synthetic_tool_refuses_without_construction_block():
    r = _call(_agent([]), "procurement_list_generator", {})
    assert r["ok"] is False
    assert "allowed_blocks" in (r["result"].get("error") or "").lower()
