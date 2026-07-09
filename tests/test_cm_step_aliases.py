"""Tests for CM step → ConstructionContainer / block action aliases."""

from __future__ import annotations

import pytest

from app.core.cm_step_aliases import (
    ACTION_ALIASES,
    STEP_TO_TARGET,
    all_template_step_types,
    resolve_action,
    resolve_step,
    unmapped_step_types,
)
from app.core.cross_domain_reasoner import MultiDomainPlanBuilder
from app.core.workflow_templates import WorkflowTemplateLibrary


# Known ConstructionContainer route keys + specialist blocks that accept
# default process (action may be None).
_KNOWN_CONSTRUCTION_ACTIONS = {
    "generate_wbs",
    "parse_primavera_schedule",
    "schedule_risk",
    "forensic_delay_analysis",
    "procurement_list_generator",
    "progress_tracker",
    "qa_qc_inspection",
    "estimate_costs",
    "sympy_reason",
    "change_order_impact",
    "claims_builder",
    "carbon_footprint_calculator",
    "commissioning_checklist",
    "safety_compliance_audit",
    "submittal_log_generator",
    "procurement_optimizer",
    "process_contract",
    "risk_register_auto_populate",
    "bim_clash_detection",
    "payment_certificate",
    "cash_flow_forecast",
    "om_manual_generator",
    "warranty_maintenance_schedule",
    "bim_analysis",
    "bim_extract",
    "rfi_generator",
    "as_built_deviation_report",
    "daily_site_report",
    "esg_sustainability_report",
    "health_check",
}

_KNOWN_BLOCKS = {
    "construction",
    "boq_processor",
    "document_engine",
    "spec_analyzer",
}


class TestStepAliasCoverage:
    def test_every_template_step_type_is_mapped(self):
        missing = unmapped_step_types()
        assert missing == [], f"Unmapped STEP_* constants: {missing}"

    def test_all_targets_use_known_blocks(self):
        for step, (block, action) in STEP_TO_TARGET.items():
            assert block in _KNOWN_BLOCKS, f"{step} → unknown block {block}"
            if block == "construction":
                assert action in _KNOWN_CONSTRUCTION_ACTIONS, (
                    f"{step} → unknown construction action {action}"
                )

    def test_resolve_action_aliases(self):
        assert resolve_action("payment_cert") == "payment_certificate"
        assert resolve_action("procurement_plan") == "procurement_list_generator"
        assert resolve_action("float_analysis") == "parse_primavera_schedule"
        assert resolve_action("extract_boq") == "extract_boq"  # specialist block
        assert resolve_action("payment_certificate") == "payment_certificate"

    def test_resolve_step(self):
        assert resolve_step("build_wbs") == ("construction", "generate_wbs")
        assert resolve_step("extract_boq") == ("boq_processor", None)
        assert resolve_step("nope") is None


class TestTemplatePlansResolve:
    def test_every_template_step_resolves(self):
        lib = WorkflowTemplateLibrary()
        builder = MultiDomainPlanBuilder(lib)
        for tid in lib.all_template_ids():
            plan = builder.build_from_template(tid)
            assert plan is not None
            assert len(plan["steps"]) > 0
            for step in plan["steps"]:
                assert step["block"] in _KNOWN_BLOCKS
                action = step["params"].get("action")
                if step["block"] == "construction":
                    assert action in _KNOWN_CONSTRUCTION_ACTIONS, (
                        f"{tid}: {step.get('step_type')} → {action}"
                    )

    def test_new_project_setup_uses_canonical_actions(self):
        plan = MultiDomainPlanBuilder().build_from_template("new_project_setup")
        actions = [s["params"].get("action") for s in plan["steps"] if s["block"] == "construction"]
        assert "generate_wbs" in actions
        assert "procurement_list_generator" in actions
        assert "risk_register_auto_populate" in actions
        # Must NOT leave template vocabulary as the action
        assert "procurement_plan" not in actions
        assert "build_wbs" not in actions

    def test_step_type_preserved(self):
        plan = MultiDomainPlanBuilder().build_from_template("monthly_progress")
        assert any(s.get("step_type") == "payment_cert" for s in plan["steps"])
        payment = next(s for s in plan["steps"] if s.get("step_type") == "payment_cert")
        assert payment["params"]["action"] == "payment_certificate"
