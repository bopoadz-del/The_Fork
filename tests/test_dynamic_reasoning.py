"""Dynamic UNDERSTAND (LLM intent read). Deterministic tests mock the LLM;
the mapping/parse/fallthrough logic is what's under test. Live behavior on a
real model is validated separately (it beats the keyword router)."""
from __future__ import annotations

import asyncio

from app.core import dynamic_reasoning as dr
from app.core.llm_client import _extract_json_object


def _mock(monkeypatch, obj):
    async def fake(system, user, **kw):
        return obj
    monkeypatch.setattr(dr, "complete_json", fake)


def test_produce_schedule_maps_to_action_and_params(monkeypatch):
    _mock(monkeypatch, {"workflow": "schedule", "mode": "produce", "params": {"target_count": 300}})
    r = asyncio.run(dr.understand_intent("produce a schedule"))
    assert r["action"] == "generate_wbs" and r["deliverable"] is True
    assert r["params"]["target_count"] == 300


def test_question_is_not_deliverable(monkeypatch):
    _mock(monkeypatch, {"workflow": "schedule", "mode": "question", "params": {}})
    r = asyncio.run(dr.understand_intent("how long is procurement?"))
    assert r["action"] == "generate_wbs"
    assert r["deliverable"] is False and r["mode"] == "question"


def test_none_workflow_falls_through(monkeypatch):
    _mock(monkeypatch, {"workflow": "none", "mode": "question", "params": {}})
    r = asyncio.run(dr.understand_intent("what is the tallest building"))
    assert r["action"] is None and r["workflow"] == "none"


def test_llm_failure_falls_through_not_raises(monkeypatch):
    async def boom(system, user, **kw):
        raise RuntimeError("llm down")
    monkeypatch.setattr(dr, "complete_json", boom)
    r = asyncio.run(dr.understand_intent("produce a schedule"))
    assert r["action"] is None


def test_empty_message_short_circuits():
    r = asyncio.run(dr.understand_intent(""))
    assert r["action"] is None


def test_extract_json_tolerant():
    assert _extract_json_object('```json\n{"a": 1}\n```') == {"a": 1}
    assert _extract_json_object('sure: {"workflow": "schedule"} ok') == {"workflow": "schedule"}
    assert _extract_json_object("no json here") == {}
