"""Oracle tests for QC + commercial + planning + safety additive formulas.

Values hand-derived from the cited basis and shown in comments.
"""
import pytest

from app.lib.construction_formulas import CALCULATORS


# ── QC ─────────────────────────────────────────────────────────────────────

def test_concrete_cylinders_150mm():
    # A = pi/4*150^2 = 17671 mm2; f = 530000/17671 = 29.99 MPa.
    r = CALCULATORS["concrete_cylinders"](failure_load_kn=530, cylinder_diameter_mm=150)
    assert r["compressive_strength_mpa"] == pytest.approx(30.0, abs=0.05)


def test_concrete_shrinkage_1yr():
    # esh = 365/(35+365)*780 = 0.9125*780 = 711.75 microstrain.
    r = CALCULATORS["concrete_shrinkage"](time_days=365)
    assert r["shrinkage_microstrain"] == pytest.approx(711.75, abs=0.5)


def test_concrete_curing_time_70pct():
    # t = 4*0.7/(1 - 0.85*0.7) = 2.8/0.405 = 6.91 days.
    r = CALCULATORS["concrete_curing_time"](target_strength_fraction=0.70)
    assert r["days_to_target"] == pytest.approx(6.91, abs=0.05)


# ── commercial ─────────────────────────────────────────────────────────────

def test_roi_calculator():
    # (1.2M - 1.0M)/1.0M*100 = 20%.
    r = CALCULATORS["roi_calculator"](gain=1_200_000, cost=1_000_000)
    assert r["roi_percent"] == pytest.approx(20.0, abs=0.01)
    assert r["net_profit"] == pytest.approx(200_000, abs=1)


def test_unit_cost_total():
    r = CALCULATORS["unit_cost_total"](quantity=100, unit_rate=250)
    assert r["total_cost"] == pytest.approx(25_000, abs=0.01)


def test_cost_per_area():
    # 1,000,000 / 5000 = 200 per unit.
    r = CALCULATORS["cost_per_area"](total_cost=1_000_000, area=5000)
    assert r["cost_per_area"] == pytest.approx(200.0, abs=0.01)


def test_productivity_rate():
    # 500/40 = 12.5/hr; /4 crew = 3.125/worker-hr.
    r = CALCULATORS["productivity_rate"](output_quantity=500, labor_hours=40, crew_size=4)
    assert r["rate_per_hour"] == pytest.approx(12.5, abs=0.001)
    assert r["rate_per_worker_hour"] == pytest.approx(3.125, abs=0.001)


# ── planning ───────────────────────────────────────────────────────────────

def test_critical_path_float():
    # TF = LS - ES = 8 - 5 = 3 (= LF - EF = 13 - 10).
    r = CALCULATORS["critical_path_float"](early_start=5, early_finish=10,
                                           late_start=8, late_finish=13)
    assert r["total_float"] == pytest.approx(3.0, abs=0.001)
    assert r["is_critical"] is False


def test_critical_path_float_zero_is_critical():
    r = CALCULATORS["critical_path_float"](early_start=5, early_finish=10,
                                           late_start=5, late_finish=10)
    assert r["total_float"] == pytest.approx(0.0, abs=0.001)
    assert r["is_critical"] is True


# ── safety ─────────────────────────────────────────────────────────────────

def test_scaffold_load_capacity_medium():
    # intended = 2.4 kPa * 10 m2 = 24 kN; required = 4x = 96 kN.
    r = CALCULATORS["scaffold_load_capacity"](platform_area_m2=10, duty="medium")
    assert r["intended_load_kn"] == pytest.approx(24.0, abs=0.01)
    assert r["required_capacity_kn"] == pytest.approx(96.0, abs=0.01)


def test_fall_arrest_force_within_limit():
    # F = 981*(1 + 1.8/1.0) = 981*2.8 = 2746.8 N = 2.75 kN (< 8 kN).
    r = CALCULATORS["fall_arrest_force"](worker_mass_kg=100, free_fall_m=1.8,
                                         deceleration_distance_m=1.0)
    assert r["max_arrest_force_kn"] == pytest.approx(2.75, abs=0.02)
    assert r["within_osha_limit"] is True


def test_crane_lift_capacity():
    # net = 50 - 3 = 47 t; util = 38/47 = 80.85% -> pass (<=85%).
    r = CALCULATORS["crane_lift_capacity"](chart_capacity_t=50, deductions_t=3, load_t=38)
    assert r["net_capacity_t"] == pytest.approx(47.0, abs=0.01)
    assert r["utilization_percent"] == pytest.approx(80.85, abs=0.1)
    assert r["passed"] is True
