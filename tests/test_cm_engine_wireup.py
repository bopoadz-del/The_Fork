"""E2E: workflow template → aliased actions → ConstructionContainer.route."""
from __future__ import annotations

import pytest

from app.core.cm_step_aliases import resolve_action
from app.core.cross_domain_reasoner import MultiDomainPlanBuilder


@pytest.mark.asyncio
async def test_template_steps_dispatch_via_construction_route():
    from app.containers.construction import ConstructionContainer

    plan = MultiDomainPlanBuilder().build_from_template("change_order_impact")
    assert plan is not None
    container = ConstructionContainer()

    construction_steps = [s for s in plan["steps"] if s["block"] == "construction"]
    assert len(construction_steps) >= 3

    for step in construction_steps[:4]:
        action = step["params"]["action"]
        raw = step.get("step_type")
        if raw:
            assert resolve_action(raw) == action or action == resolve_action(raw)
        result = await container.route(action, {}, {"action": action})
        assert result.get("error", "").startswith("Unknown action") is False, (
            f"step {step.get('step_type')} → {action}: {result}"
        )
        assert "known_actions" not in result or result.get("status") != "error" or (
            "Unknown action" not in str(result.get("error", ""))
        )


@pytest.mark.asyncio
async def test_route_accepts_template_vocabulary_directly():
    from app.containers.construction import ConstructionContainer

    container = ConstructionContainer()
    result = await container.route(
        "payment_cert", {}, {"action": "payment_cert"}
    )
    assert "Unknown action" not in str(result.get("error", ""))


@pytest.mark.asyncio
async def test_project_dashboard_run_workflow():
    from app.blocks.project_dashboard import ProjectDashboardBlock

    block = ProjectDashboardBlock()
    result = await block.process({
        "action": "run_workflow",
        "template_id": "new_project_setup",
        "project_type": "data_center",
    })
    assert result["status"] == "success"
    assert result["template_id"] == "new_project_setup"
    assert len(result["steps"]) > 0
    construction_actions = [
        s["params"]["action"] for s in result["steps"] if s["block"] == "construction"
    ]
    assert "generate_wbs" in construction_actions
    assert "build_wbs" not in construction_actions
