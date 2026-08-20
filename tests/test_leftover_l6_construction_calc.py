"""Leftover L6: trench 14.5 × 3.2 × 1.75 m bank volume → 81.2 m³.

Live 4e0b485 (pinned smart-orchestrator on /v1/chat/stream):
  The hat called ``construction`` with ``action=construction_calc``.
  ``route()`` returned Unknown action; the model then narrated a
  ``formula_execute`` JSON stub and never emitted 81.2.

These tests pin the machinery (no live LLM).
"""
from __future__ import annotations

import json

import pytest

from tests.conftest import requires_construction_kit

L6_PROMPT = (
    "Stay on smart-orchestrator. Bank volume of a rectangular trench "
    "14.5 m long by 3.2 m wide by 1.75 m deep. Start with the "
    "smart_orchestrator tool then a calculator. Report Intent, Routed to, "
    "and the bank volume in m3."
)


@requires_construction_kit
@pytest.mark.asyncio
async def test_container_construction_calc_alias_returns_81_2():
    from app.containers.construction import ConstructionContainer

    r = await ConstructionContainer().route(
        "construction_calc",
        {},
        {
            "action": "construction_calc",
            "name": "excavation_volume",
            "length_m": 14.5,
            "width_m": 3.2,
            "depth_m": 1.75,
        },
    )
    assert r.get("status") == "success", r
    inner = r.get("result") or r
    bank = inner.get("bank_volume_m3") or (inner.get("result") or {}).get("bank_volume_m3")
    assert bank == pytest.approx(81.2)


@requires_construction_kit
@pytest.mark.asyncio
async def test_construction_calc_infers_excavation_from_formula_string():
    from app.containers.construction import ConstructionContainer

    r = await ConstructionContainer().route(
        "construction_calc",
        {},
        {"action": "construction_calc", "formula": "14.5 * 3.2 * 1.75", "unit": "m3"},
    )
    assert r.get("status") == "success", r
    inner = r.get("result") or r
    bank = inner.get("bank_volume_m3") or (inner.get("result") or {}).get("bank_volume_m3")
    assert bank == pytest.approx(81.2)


@requires_construction_kit
@pytest.mark.asyncio
async def test_construction_execute_accepts_hat_shape():
    from app.containers.construction import ConstructionContainer

    env = await ConstructionContainer().execute(
        {},
        {
            "action": "construction_calc",
            "name": "excavation_volume",
            "length_m": 14.5,
            "width_m": 3.2,
            "depth_m": 1.75,
        },
    )
    assert env.get("status") == "success", env
    blob = json.dumps(env, default=str)
    assert "81.2" in blob


@requires_construction_kit
@pytest.mark.asyncio
async def test_orchestrator_routes_l6_prompt_to_construction_calc():
    from app.blocks.smart_orchestrator import SmartOrchestratorBlock

    r = await SmartOrchestratorBlock().process({"user_message": L6_PROMPT})
    assert r["status"] == "success"
    assert "construction_calc" in (r.get("action_queue") or []), r
    matched = [m.get("action") for m in (r.get("matched_actions") or [])]
    assert "construction_calc" in matched, r
