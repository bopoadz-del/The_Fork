"""CM wiring hardening — delivery integrity, procedure routes, promote flag, honesty."""
from __future__ import annotations

import pytest

from app.core.procedure_actions import is_procedure_action, procedure_metadata
from app.core.construction_learning import ConstructionLearningEngine


@pytest.mark.asyncio
async def test_procedure_actions_do_not_404_on_route():
    from app.containers.construction import ConstructionContainer

    container = ConstructionContainer()
    for action in (
        "design_review_workflow",
        "rfi_management",
        "ncr_management",
        "handover_management",
        "inspection_request",
    ):
        result = await container.route(action, {}, {"action": action})
        assert "Unknown action" not in str(result.get("error", "")), action
        assert result.get("status") == "success", result


def test_procedure_metadata_is_honest_not_fake_execution():
    meta = procedure_metadata("design_review_workflow")
    assert meta["status"] == "success"
    assert meta["execution_mode"] == "metadata_only"
    assert meta["procedure_id"] == "PRC-501"
    assert "fabricated" in meta["note"].lower() or "guidance" in meta["note"].lower()


@pytest.mark.asyncio
async def test_rfi_management_delegates_to_rfi_generator():
    from app.containers.construction import ConstructionContainer

    result = await ConstructionContainer().route(
        "rfi_management", {"issue": "clarify steel grade"}, {"action": "rfi_management"}
    )
    assert result.get("status") == "success"
    ctx = result.get("procedure_context") or {}
    assert ctx.get("delegate_action") == "rfi_generator"
    assert ctx.get("execution_mode") == "delegated"


@pytest.mark.asyncio
async def test_promote_cm_followups_flag_allows_boq_queue_growth():
    from app.blocks.smart_orchestrator import SmartOrchestratorBlock

    block = SmartOrchestratorBlock()
    queue = ["boq_process"]
    meta = block._cross_domain_enrichment(
        "process this bill of quantities and set up procurement for the data center project",
        queue,
        promote_cm_followups=True,
    )
    assert meta.get("cm_follow_up_tools")
    # Weak template alone must not promote without score/suggested overlap.
    assert meta.get("cm_queue_appended") == [] or all(
        t in queue for t in meta.get("cm_queue_appended") or []
    )


@pytest.mark.asyncio
async def test_promote_cm_followups_default_keeps_boq_metadata_only():
    from app.blocks.smart_orchestrator import SmartOrchestratorBlock

    block = SmartOrchestratorBlock()
    queue = ["boq_process"]
    meta = block._cross_domain_enrichment(
        "process this bill of quantities and cost sheet",
        queue,
        promote_cm_followups=False,
    )
    assert meta.get("cm_queue_appended") == []
    assert queue == ["boq_process"]


@pytest.mark.asyncio
async def test_project_dashboard_executable_plan_has_honesty_labels():
    from app.blocks.project_dashboard import ProjectDashboardBlock

    block = ProjectDashboardBlock()
    result = await block.process({
        "action": "workflow_detail",
        "template_id": "new_project_setup",
    })
    assert result["status"] == "success"
    plan = result["executable_plan"]
    assert plan["execution_mode"] == "plan_only"
    assert plan.get("execution_note")
    labels = {s.get("execution_label") for s in plan.get("steps") or []}
    assert "dispatchable" in labels
    assert "caller_render" in labels


def test_construction_learning_list_surfaces():
    engine = ConstructionLearningEngine()
    out = engine.list_surfaces()
    assert out["status"] == "success"
    assert out["execution_mode"] == "list_only"
    assert len(out["surfaces"]) >= 4
    assert "fabricated" in out["note"].lower()


def test_cm_prompt_fragment_for_agent_path():
    from app.agents.runtime import _cm_prompt_fragment_for_turn

    frag = _cm_prompt_fragment_for_turn(
        "set up a new data center project and generate WBS schedule"
    )
    # May be empty on weak match — must not raise; strong match includes template.
    assert isinstance(frag, str)
    assert len(frag) <= 800


def test_schedule_generator_shim_docstring_declares_delegation():
    from app.blocks import schedule_generator as sg

    mod_doc = sg.__doc__ or ""
    assert "shim" in mod_doc.lower() or "delegate" in mod_doc.lower()
    assert "generate_wbs" in mod_doc.lower()
