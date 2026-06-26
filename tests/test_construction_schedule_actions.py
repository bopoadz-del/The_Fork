"""Deterministic tests for construction schedule non-file actions.

Covers the public actions:
  - progress_tracker
  - warranty_maintenance_schedule
  - commissioning_checklist

And the pure helpers:
  - _calculate_duration_days
  - _calculate_date_diff
  - _analyze_schedule_risks
  - _generate_recovery_options

No real Primavera files or external services are used; the container is
exercised directly with synthetic inputs.
"""

from __future__ import annotations

import pytest

from app.containers.construction import ConstructionContainer
from tests.conftest import requires_construction_kit


@pytest.fixture
def container():
    return ConstructionContainer()


# ---------------------------------------------------------------------------
# Pure helper tests
# ---------------------------------------------------------------------------

@requires_construction_kit
def test_calculate_duration_days_typical(container):
    assert container._calculate_duration_days("2024-01-01", "2024-01-10") == 9


@requires_construction_kit
def test_calculate_duration_days_same_day(container):
    assert container._calculate_duration_days("2024-06-15", "2024-06-15") == 0


@requires_construction_kit
def test_calculate_duration_days_negative_range_is_zero(container):
    """Finish before start is clamped to zero (duration cannot be negative)."""
    assert container._calculate_duration_days("2024-06-15", "2024-06-01") == 0


@requires_construction_kit
def test_calculate_duration_days_iso_with_z(container):
    assert container._calculate_duration_days("2024-01-01T00:00:00Z", "2024-01-10T00:00:00Z") == 9


@requires_construction_kit
def test_calculate_duration_days_invalid_returns_zero(container):
    assert container._calculate_duration_days("not-a-date", "2024-01-10") == 0
    assert container._calculate_duration_days("2024-01-01", "") == 0


@requires_construction_kit
def test_calculate_date_diff_typical(container):
    assert container._calculate_date_diff("2024-01-01", "2024-01-10") == 9


@requires_construction_kit
def test_calculate_date_diff_negative_is_zero(container):
    """Delay analysis only cares about positive slips; negative diffs are clamped."""
    assert container._calculate_date_diff("2024-06-15", "2024-06-01") == 0


@requires_construction_kit
def test_calculate_date_diff_iso_with_z(container):
    assert container._calculate_date_diff("2024-01-01T00:00:00Z", "2024-01-10T00:00:00Z") == 9


@requires_construction_kit
def test_calculate_date_diff_invalid_returns_zero(container):
    assert container._calculate_date_diff("bad", "2024-01-10") == 0


@requires_construction_kit
def test_analyze_schedule_risks_high_when_low_float(container):
    risks = container._analyze_schedule_risks({"average_float": 1.5})
    assert len(risks) == 1
    risk = risks[0]
    assert risk["category"] == "schedule"
    assert risk["impact"] == "high"
    assert "minimal overall float" in risk["description"]
    assert "mitigation" in risk and risk["mitigation"]


@requires_construction_kit
def test_analyze_schedule_risks_empty_when_healthy_float(container):
    assert container._analyze_schedule_risks({"average_float": 5.0}) == []
    assert container._analyze_schedule_risks({"average_float": 2.0}) == []


@requires_construction_kit
def test_generate_recovery_options_no_delay_analysis(container):
    assert container._generate_recovery_options(None, {}) == []


@requires_construction_kit
def test_generate_recovery_options_zero_delay(container):
    assert container._generate_recovery_options({"total_delay_days": 0}, {}) == []


@requires_construction_kit
def test_generate_recovery_options_returns_three_strategies(container):
    options = container._generate_recovery_options({"total_delay_days": 10}, {})
    assert len(options) == 3
    strategies = [o["strategy"] for o in options]
    assert "Crash Critical Path" in strategies
    assert "Fast Track" in strategies
    assert "Scope Reduction" in strategies

    crash = next(o for o in options if o["strategy"] == "Crash Critical Path")
    assert crash["potential_savings_days"] == 5.0
    fast = next(o for o in options if o["strategy"] == "Fast Track")
    assert fast["potential_savings_days"] == 3.0
    scope = next(o for o in options if o["strategy"] == "Scope Reduction")
    assert scope["potential_savings_days"] == 5.0


# ---------------------------------------------------------------------------
# Public action tests
# ---------------------------------------------------------------------------

@requires_construction_kit
@pytest.mark.asyncio
async def test_progress_tracker_on_track(container):
    result = await container.progress_tracker(
        input_data={},
        params={
            "planned_percent": 50.0,
            "actual_percent": 51.0,
            "contract_value": 1_000_000.0,
            "reporting_period": "June 2025",
        },
    )
    assert result["status"] == "success"
    assert result["action"] == "progress_tracker"
    assert result["reporting_period"] == "June 2025"

    overall = result["overall_progress"]
    assert overall["planned_percent"] == 50.0
    assert overall["actual_percent"] == 51.0
    assert overall["variance_percent"] == 1.0
    assert overall["schedule_performance_index"] == pytest.approx(1.02, rel=1e-3)
    assert overall["status"] == "on_track"
    assert overall["estimated_delay_days"] == 0

    ev = result["earned_value"]
    assert ev is not None
    assert ev["contract_value"] == 1_000_000.0
    assert ev["earned_value"] == 510_000.0
    assert ev["planned_value"] == 500_000.0
    assert ev["cost_variance"] == 10_000.0

    assert result["recommendations"] == ["Maintain current momentum"]


@requires_construction_kit
@pytest.mark.asyncio
async def test_progress_tracker_delayed(container):
    result = await container.progress_tracker(
        input_data={
            "planned_percent": 50.0,
            "actual_percent": 42.0,
            "activities": [
                {"name": "Foundation", "planned_percent": 80.0, "actual_percent": 70.0},
                {"name": "Steel", "planned_percent": 30.0, "actual_percent": 20.0},
            ],
        },
        params={"contract_value": 2_000_000.0},
    )
    overall = result["overall_progress"]
    assert overall["variance_percent"] == -8.0
    assert overall["status"] == "delayed"
    assert overall["schedule_performance_index"] == pytest.approx(0.84, rel=1e-3)
    assert overall["estimated_delay_days"] == 16  # round(8 / 0.5)

    activities = result["activities"]
    assert len(activities) == 2
    assert activities[0]["status"] == "delayed"
    assert activities[0]["variance"] == -10.0

    assert result["key_risks"]  # variance < -5
    assert any("recovery plan" in r for r in result["key_risks"])


@requires_construction_kit
@pytest.mark.asyncio
async def test_progress_tracker_ahead(container):
    result = await container.progress_tracker(
        input_data={},
        params={"planned_percent": 40.0, "actual_percent": 48.0},
    )
    assert result["overall_progress"]["status"] == "ahead"
    assert result["overall_progress"]["variance_percent"] == 8.0
    assert result["earned_value"] is None  # no contract value


@requires_construction_kit
@pytest.mark.asyncio
async def test_progress_tracker_zero_planned_avoids_division_error(container):
    result = await container.progress_tracker(
        input_data={},
        params={"planned_percent": 0.0, "actual_percent": 5.0},
    )
    assert result["overall_progress"]["schedule_performance_index"] == 1.0


@requires_construction_kit
@pytest.mark.asyncio
async def test_warranty_maintenance_schedule_custom_systems(container):
    result = await container.warranty_maintenance_schedule(
        input_data={},
        params={
            "systems": [
                {"name": "HVAC System", "type": "mechanical", "supplier": "Acme Mech"},
                {"name": "Electrical Distribution", "type": "electrical", "supplier": "Volt Co"},
            ],
            "project_name": "Data Center A",
            "handover_date": "2025-01-01",
            "defects_liability_months": 12,
        },
    )
    assert result["status"] == "success"
    assert result["action"] == "warranty_maintenance_schedule"
    assert result["project"] == "Data Center A"
    assert result["handover_date"] == "2025-01-01"
    assert result["defects_liability_period_months"] == 12
    assert result["total_systems"] == 2

    register = result["warranty_register"]
    assert len(register) == 2
    assert register[0]["system"] == "HVAC System"
    assert register[0]["warranty_months"] == 24
    assert register[0]["warranty_expiry"] == "2026-12-22"
    assert register[0]["dlp_expiry"] == "2025-12-27"  # 12 months * 30 days

    assert register[1]["system"] == "Electrical Distribution"
    assert register[1]["warranty_months"] == 12
    assert register[1]["warranty_expiry"] == "2025-12-27"

    maintenance = result["maintenance_schedule"]
    assert len(maintenance) == 5  # 3 mechanical + 2 electrical
    assert all("next_due" in m for m in maintenance)

    early = result["early_expiries"]
    assert len(early) == 1
    assert early[0]["system"] == "Electrical Distribution"

    assert any("2025-12-27" in r for r in result["recommendations"])


@requires_construction_kit
@pytest.mark.asyncio
async def test_warranty_maintenance_schedule_defaults_when_empty(container):
    result = await container.warranty_maintenance_schedule(input_data={}, params={})
    assert result["status"] == "success"
    assert result["total_systems"] == 7  # default system list
    assert result["project"] == "Project"
    assert result["defects_liability_period_months"] == 12
    assert len(result["maintenance_schedule"]) > 0


@requires_construction_kit
@pytest.mark.asyncio
async def test_warranty_maintenance_schedule_respects_input_data(container):
    """Regression: handover_date, defects_liability_months and project_name
    must be read from input_data when not supplied in params."""
    result = await container.warranty_maintenance_schedule(
        input_data={
            "systems": [{"name": "Backup Generator", "type": "electrical", "supplier": "GenSet"}],
            "project_name": "From Input",
            "handover_date": "2025-06-15",
            "defects_liability_months": 18,
        },
        params={},
    )
    assert result["project"] == "From Input"
    assert result["handover_date"] == "2025-06-15"
    assert result["defects_liability_period_months"] == 18
    assert result["warranty_register"][0]["dlp_expiry"] == "2026-12-07"  # 18 months * 30 days


@requires_construction_kit
@pytest.mark.asyncio
async def test_commissioning_checklist_default_systems(container):
    result = await container.commissioning_checklist(
        input_data={},
        params={"substantial_completion_date": "2025-01-01", "equipment_list": ["chiller", "ups"]},
    )
    assert result["status"] == "success"
    assert result["action"] == "commissioning_checklist_generated"
    assert result["project_phase"] == "pre_handover"
    assert result["substantial_completion_target"] == "2025-01-01"

    # 5 default systems * 2 weeks + 0 extra equipment (2 // 10 == 0) = 10 weeks
    assert result["commissioning_period_weeks"] == 10
    assert result["completion_target"] == "2025-03-12T00:00:00"

    summary = result["summary"]
    assert summary["systems_covered"] == 5
    assert summary["total_tests"] > 0
    assert summary["passed"] == 0
    assert summary["failed"] == 0
    assert summary["pending"] == summary["total_tests"]
    assert summary["percent_complete"] == 0.0

    assert set(result["checklists_by_system"].keys()) == {
        "electrical", "hvac", "fire_protection", "elevators", "building_envelope"
    }
    assert len(result["master_test_schedule"]) == summary["total_tests"]
    assert len(result["witness_required"]) > 0
    assert len(result["third_party_testing"]) > 0  # fire smoke detector sensitivity
    assert len(result["documentation_required"]) == 5
    assert len(result["training_requirements"]) == 5

    sign_off = result["final_sign_off"]
    assert sign_off["mechanical_contractor"] == "pending"
    assert sign_off["commissioning_authority"] == "pending"


@requires_construction_kit
@pytest.mark.asyncio
async def test_commissioning_checklist_custom_systems(container):
    result = await container.commissioning_checklist(
        input_data={},
        params={
            "systems": ["electrical", "mechanical", "fire"],
            "equipment_list": list(range(15)),
            "substantial_completion_date": "2025-06-01",
        },
    )
    summary = result["summary"]
    assert summary["systems_covered"] == 3
    assert summary["total_tests"] == 20  # 7 electrical + 7 hvac + 6 fire_protection

    # 3 systems * 2 weeks + 15 // 10 = 6 + 1 = 7 weeks
    assert result["commissioning_period_weeks"] == 7
    assert result["completion_target"] == "2025-07-20T00:00:00"

    assert len(result["witness_required"]) == 12
    assert len(result["third_party_testing"]) == 1


@requires_construction_kit
@pytest.mark.asyncio
async def test_commissioning_checklist_respects_input_data(container):
    """Regression: systems and substantial_completion_date must be read from
    input_data when not supplied in params."""
    result = await container.commissioning_checklist(
        input_data={
            "systems": ["electrical"],
            "substantial_completion_date": "2025-09-01",
        },
        params={},
    )
    assert result["summary"]["systems_covered"] == 1
    assert result["substantial_completion_target"] == "2025-09-01"
    assert result["commissioning_period_weeks"] == 2
    assert result["completion_target"] == "2025-09-15T00:00:00"
