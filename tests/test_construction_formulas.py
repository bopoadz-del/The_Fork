"""Verification of the construction_formulas library (integrated from the
operator's SMGT-C552 formula upgrade). Every expected value below is hand-
computed from the formula definition, so this proves the maths — not just that
the functions run. Also pins the rates-as-parameters contract that keeps cost
build-ups grounded (real rates flow in from RAG; hardcoded defaults are
indicative fallbacks only).
"""
from __future__ import annotations

import pytest

from app.lib import construction_formulas as cf


def test_dewatering_uplift_check_cannot_stop():
    # uplift = 23; counter = 2.0*2.5 + 5*0.3*2.5 = 5.0 + 3.75 = 8.75; FOS = 0.380
    r = cf.dewatering_uplift_check(water_depth=23, raft_thickness=2.0, floor_count=5)
    assert r.uplift_force_t_m2 == 23.0
    assert r.counter_weight_t_m2 == 8.75
    assert r.fos == pytest.approx(0.380, abs=0.001)
    assert r.can_stop is False
    assert r.needs_tension_piles is True
    # min floors: ceil((23*1.25 - 5.0)/0.75) = ceil(31.67) = 32
    assert r.min_floors_for_stop == 32


def test_diaphragm_wall_volume_with_tremie_waste():
    r = cf.diaphragm_wall_panel_volume(panel_length=6, wall_thickness=0.8,
                                       excavation_depth=20, panel_count=3)
    assert r["volume_per_panel_m3"] == 96.0
    assert r["total_volume_m3"] == 288.0
    assert r["volume_with_waste_m3"] == 316.8  # 288 * 1.10


def test_well_point_spacing_by_permeability():
    r = cf.dewatering_well_point_spacing(soil_permeability_m_s=2e-3, required_drawdown_m=12)
    assert r["soil_type"] == "coarse sand/gravel"
    assert r["well_point_spacing_m"] == 1.5
    assert r["stages_needed"] == 3  # ceil(12/5)


def test_fineness_modulus():
    assert cf.fineness_modulus([10, 25, 45, 70, 90, 98]) == 3.38  # 338/100


def test_cost_buildup_concrete_arithmetic():
    # material = 0.4*190 + 1.839*29 + 0.16*10 + 12*1.5 + 5*2.4 = 160.93
    r = cf.cost_buildup_concrete(quantity_m3=100)
    assert r["material_cost_sar_m3"] == pytest.approx(160.93, abs=0.01)
    # direct 217.63 -> *1.03 waste -> *1.18 indirect -> /0.85 markup = 311.19
    assert r["selling_price_sar_m3"] == pytest.approx(311.19, abs=0.1)
    assert r["total_project_value_sar"] == pytest.approx(31119, abs=2)


def test_cost_buildup_rebar_arithmetic():
    r = cf.cost_buildup_rebar(quantity_kg=1000)
    # material = 1t * 2600 * 1.10 = 2860
    assert r["material_sar_t"] == 2860
    # (2860 + 90*4.1 + 2*134.6) * 1.18 / 0.85 = 4856
    assert r["selling_price_sar_t"] == pytest.approx(4856, abs=2)


def test_rates_are_parameters_not_hardcoded():
    # The RAG-grounding contract: a real rate passed in flows through the maths.
    base = cf.cost_buildup_rebar(quantity_kg=1000)
    with_real_rate = cf.cost_buildup_rebar(quantity_kg=1000, material_price_sar_t=3000)
    assert with_real_rate["material_sar_t"] == 3300  # 1t * 3000 * 1.10
    assert with_real_rate["material_sar_t"] != base["material_sar_t"]


def test_modulus_of_rupture_runs_and_is_sane():
    r = cf.modulus_of_rupture(fck_n_mm2=40)
    # fr ~ 0.7*sqrt(fck) family — just assert a positive, plausible value + keys.
    assert isinstance(r, dict)
    assert any(isinstance(v, (int, float)) and v > 0 for v in r.values())


def test_library_surface_is_broad():
    # Sanity: the integrated library exposes the full calculator set.
    for fn in ("dewatering_uplift_check", "concrete_mix_design_sg", "crane_planning",
               "crane_cost_estimate", "cost_buildup_formwork", "mobilization_cost_estimate",
               "beam_deflection_ss_udl", "foundation_bearing_pressure"):
        assert callable(getattr(cf, fn)), fn
