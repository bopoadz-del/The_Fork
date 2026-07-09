"""smart_orchestrator post-route CM enrichment (non-competing)."""
from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_smart_orchestrator_attaches_cm_enrichment():
    from app.blocks.smart_orchestrator import SmartOrchestratorBlock

    block = SmartOrchestratorBlock()
    result = await block.process({
        "user_message": "set up a new data center project and generate WBS",
    })
    assert result["status"] == "success"
    assert "action_queue" in result
    assert "cm_follow_up_tools" in result or "cm_matched_template" in result
    if result.get("cm_matched_template"):
        assert result["cm_matched_template"] == "new_project_setup"
        assert result.get("cm_workflow_plan", {}).get("step_count", 0) > 0
