"""When the user names commissioning systems, the lane tool returns only those."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.agents.core.tool_registry import ToolCall
from app.agents.hats.qaqc.tools import handle_commissioning_checklist


class _Container:
    def __init__(self):
        self.seen = None

    async def commissioning_checklist(self, input_data, params):
        self.seen = list(params.get("systems") or [])
        systems = self.seen
        return {
            "status": "success",
            "checklists_by_system": {key: [{"test": key}] for key in systems},
            "summary": {
                "systems_covered": len(systems),
                "total_tests": len(systems),
                "pending": len(systems),
            },
        }


@pytest.mark.asyncio
async def test_named_systems_are_the_only_ones_returned(monkeypatch):
    container = _Container()
    monkeypatch.setattr(
        "app.dependencies.get_block_instance", lambda _name: container,
    )
    call = ToolCall(
        agent=SimpleNamespace(allowed_blocks=["construction"]),
        name="commissioning_checklist",
        args={"systems": ["electrical", "hvac", "fire", "elevator", "facade"]},
        user_message="commissioning checklist for the chillers and the fire alarm",
    )
    out = await handle_commissioning_checklist(call)
    assert out["ok"] is True
    systems = set(out["result"]["checklists_by_system"])
    assert systems == {"hvac", "fire"}
    assert container.seen == ["hvac", "fire"] or set(container.seen) == systems


@pytest.mark.asyncio
async def test_defaults_are_used_only_when_none_are_named(monkeypatch):
    container = _Container()
    monkeypatch.setattr(
        "app.dependencies.get_block_instance", lambda _name: container,
    )
    call = ToolCall(
        agent=SimpleNamespace(allowed_blocks=["construction"]),
        name="commissioning_checklist",
        args={},
        user_message="generate a commissioning checklist",
    )
    out = await handle_commissioning_checklist(call)
    assert out["ok"] is True
    systems = list(out["result"]["checklists_by_system"])
    assert len(systems) >= 2
    assert "electrical" in systems or "hvac" in systems
