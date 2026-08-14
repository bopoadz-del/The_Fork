"""
Construction Calculations — deterministic engineering formula library.

Single source of truth for construction calculations (deep foundations,
concrete technology, structural, crane planning, cost build-up, MEP, QC).
Sourced from SMGT-C552 course material. No project/company names.

DETERMINISTIC TOOLS, not a rate oracle: every cost build-up takes its unit
rates as PARAMETERS. The hardcoded defaults are indicative GCC fallbacks only —
callers should pass real rates from RAG (company priced BOQ -> GK) so the cost
answer stays grounded (see the-fork-rates-in-rag). The maths never changes; only
the inputs do.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


# ═══════════════════════════════════════════════════════════════════════════
#  SECTION 1 — DEEP FOUNDATIONS
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class DewateringResult:
    uplift_force_t_m2: float
    counter_weight_t_m2: float
    fos: float
    can_stop: bool
    needs_tension_piles: bool
    min_floors_for_stop: int
    notes: List[str] = field(default_factory=list)


def dewatering_uplift_check(
    water_depth: float,
    raft_thickness: float,
    floor_count: int,
    floor_thickness: float = 0.3,
    concrete_unit_weight: float = 2.5,
    water_unit_weight: float = 1.0,
    required_fos: float = 1.25,
) -> DewateringResult:
    """Check if dewatering can stop — FOS = counter-weight / uplift >= 1.25."""
    uplift = water_depth * water_unit_weight
    raft_weight = raft_thickness * concrete_unit_weight
    floor_weight = floor_count * floor_thickness * concrete_unit_weight
    counter_weight = raft_weight + floor_weight
    fos = counter_weight / uplift if uplift > 0 else 0.0

    notes = [
        f"Uplift = {water_depth} x {water_unit_weight} = {uplift:.3f} T/m2",
        f"Counter = {raft_weight:.3f} + {floor_weight:.3f} = {counter_weight:.3f} T/m2",
        f"FOS = {fos:.3f} (required: {required_fos})",
    ]
    can_stop = fos >= required_fos
    if can_stop:
        notes.append(f"OK - dewatering CAN stop at {floor_count} floors")
    else:
        notes.append(f"NG - dewatering CANNOT stop, FOS {fos:.3f} < {required_fos}")
        notes.append("Tension piles required to anchor against uplift")

    min_counter = uplift * required_fos
    add_floor = floor_thickness * concrete_unit_weight
    min_floors = max(0, math.ceil((min_counter - raft_weight) / add_floor))
    notes.append(f"Minimum floors to stop dewatering: {min_floors}")

    return DewateringResult(
        uplift_force_t_m2=round(uplift, 3),
        counter_weight_t_m2=round(counter_weight, 3),
        fos=round(fos, 3),
        can_stop=can_stop,
        needs_tension_piles=not can_stop,
        min_floors_for_stop=min_floors,
        notes=notes,
    )


def diaphragm_wall_panel_volume(
    panel_length: float, wall_thickness: float,
    excavation_depth: float, panel_count: int = 1,
) -> Dict[str, float]:
    """Concrete volume for diaphragm wall panels with 10% tremie waste."""
    vol = panel_length * wall_thickness * excavation_depth
    total = vol * panel_count
    return {
        "panel_count": panel_count,
        "volume_per_panel_m3": round(vol, 2),
        "total_volume_m3": round(total, 2),
        "volume_with_waste_m3": round(total * 1.10, 2),
        "waste_factor": 1.10,
    }


def dewatering_well_point_spacing(
    soil_permeability_m_s: float,
    required_drawdown_m: float,
    well_point_diameter_m: float = 0.05,
) -> Dict[str, Any]:
    """Well point spacing by soil type. Multi-stage: 5m/stage, max 3 stages (15m)."""
    if soil_permeability_m_s > 1e-3:
        spacing_m, soil_type = 1.5, "coarse sand/gravel"
    elif soil_permeability_m_s > 1e-4:
        spacing_m, soil_type = 1.2, "medium sand"
    elif soil_permeability_m_s > 1e-5:
        spacing_m, soil_type = 0.9, "fine sand"
    else:
        spacing_m, soil_type = 0.6, "silty sand - consider vacuum well points"

    stages = math.ceil(required_drawdown_m / 5.0)
    notes = [f"Soil: {soil_type}", f"Spacing: {spacing_m}m", f"Stages: {stages}"]
    if stages > 3:
        notes.append("Exceeds 3-stage limit (15m) - use deep well system")

    return {
        "soil_type": soil_type,
        "permeability_m_s": soil_permeability_m_s,
        "required_drawdown_m": required_drawdown_m,
        "stages_needed": stages,
        "max_depth_capacity_m": stages * 5.0,
        "well_point_spacing_m": spacing_m,
        "well_point_diameter_m": well_point_diameter_m,
        "notes": notes,
    }


# ═══════════════════════════════════════════════════════════════════════════
#  SECTION 2 — CONCRETE TECHNOLOGY
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class FormworkStrikingResult:
    recommended_hours: float
    bs8110_minimum_hours: float
    ciria_minimum_strength_n_mm2: float
    design_required_strength_n_mm2: float
    actual_early_strength_n_mm2: float
    based_on: str
    notes: List[str] = field(default_factory=list)


def formwork_striking_time(
    concrete_strength_7h: float = 7.0,
    design_required_strength: float = 0.332,
    ciria_surface_strength: float = 3.0,
    bs8110_minimum_hours: float = 12.0,
    proposed_hours: float = 8.0,
) -> FormworkStrikingResult:
    """Safe formwork striking per BS 8110 + CIRIA + cube test data."""
    notes = [
        f"BS 8110: min {bs8110_minimum_hours}h for wall formwork",
        f"CIRIA: need >= {ciria_surface_strength} N/mm2 for surface protection",
        f"Design check: required = {design_required_strength} N/mm2",
        f"Cube test: {concrete_strength_7h} N/mm2 at 7 hours",
    ]
    if concrete_strength_7h >= ciria_surface_strength:
        notes.append(f"OK - {concrete_strength_7h} >= CIRIA min {ciria_surface_strength}")
    else:
        notes.append(f"NG - {concrete_strength_7h} < CIRIA min {ciria_surface_strength}")
    based_on = "test_data" if concrete_strength_7h >= design_required_strength else "insufficient"
    notes.append(f"Recommended: {proposed_hours}h after pour")
    return FormworkStrikingResult(
        recommended_hours=proposed_hours,
        bs8110_minimum_hours=bs8110_minimum_hours,
        ciria_minimum_strength_n_mm2=ciria_surface_strength,
        design_required_strength_n_mm2=design_required_strength,
        actual_early_strength_n_mm2=concrete_strength_7h,
        based_on=based_on,
        notes=notes,
    )


def fineness_modulus(sieve_retained_percentages: List[float]) -> float:
    """FM = (cumulative % retained on all sieves) / 100."""
    if not sieve_retained_percentages:
        return 0.0
    return round(sum(sieve_retained_percentages) / 100.0, 2)


def concrete_mix_design_sg(
    w_c_ratio: float,
    cement_sg: float = 3.15,
    fine_agg_sg: float = 2.67,
    coarse_agg_sg: float = 2.54,
    fine_agg_ratio: float = 2.0,
    coarse_agg_ratio: float = 4.0,
    dune_sand_pct: float = 0.0,
) -> Dict[str, float]:
    """Mix design by Specific Gravity (absolute volume method).
    W/(SGw) + C/(SGc) + F/(SGf) + Cr/(SGcr) = 1.0 m3."""
    cement_tonnes = 1.0 / (w_c_ratio / 1.0 + 1.0 / cement_sg + fine_agg_ratio / fine_agg_sg + coarse_agg_ratio / coarse_agg_sg)
    cement_kg = round(cement_tonnes * 1000, 0)
    water_litres = round(cement_kg * w_c_ratio, 0)
    fine_kg = round(cement_kg * fine_agg_ratio, 0)
    coarse_kg = round(cement_kg * coarse_agg_ratio, 0)
    return {
        "w_c_ratio": w_c_ratio,
        "cement_kg_m3": cement_kg,
        "water_litres_m3": water_litres,
        "fine_aggregate_kg_m3": fine_kg,
        "coarse_aggregate_kg_m3": coarse_kg,
        "total_weight_kg_m3": cement_kg + water_litres + fine_kg + coarse_kg,
        "proportions": f"1:{int(fine_agg_ratio)}:{coarse_agg_ratio}",
    }


def concrete_mix_slip_form() -> Dict[str, Any]:
    """Slip-forming mix: 1:2:2.6, W/C=0.42, slump=150 +/- 30 mm."""
    mix = concrete_mix_design_sg(w_c_ratio=0.42, fine_agg_ratio=2.0, coarse_agg_ratio=2.6)
    mix["slump_mm"] = "150 +/- 30"
    mix["retarder_20c_lit_m3"] = 3.8
    mix["retarder_10c_lit_m3"] = 2.9
    mix["max_concrete_temp_c"] = 32
    mix["cube_strength_n_mm2"] = "50-55 @ 28 days"
    return mix


def modulus_of_elasticity_concrete(fck_n_mm2: float) -> float:
    """Ec = 15000 x sqrt(f'c) in kg/cm2. f'c = fck x 10."""
    return round(15000 * math.sqrt(fck_n_mm2 * 10), 0)


def beam_deflection_ss_udl(w_kn_m: float, span_m: float, ec_mpa: float, i_mm4: float) -> float:
    """Simply supported, UDL: delta = 5wL^4 / (384EI). Returns mm."""
    w_n_mm = w_kn_m  # kN/m = N/mm
    l_mm = span_m * 1e3
    return round((5 * w_n_mm * l_mm**4) / (384 * ec_mpa * i_mm4), 2)


def beam_deflection_cantilever_udl(w_kn_m: float, span_m: float, ec_mpa: float, i_mm4: float) -> float:
    """Cantilever, UDL: delta = wL^4 / (8EI). Returns mm."""
    w_n_mm = w_kn_m
    l_mm = span_m * 1e3
    return round((w_n_mm * l_mm**4) / (8 * ec_mpa * i_mm4), 2)


def modulus_of_rupture(fck_n_mm2: float) -> Dict[str, float]:
    """Tensile strength: fr = 2.4*sqrt(f'c), split cylinder ACI = 1.78*sqrt(f'c)."""
    fck_kg = fck_n_mm2 * 10
    fr = 2.4 * math.sqrt(fck_kg) / 10
    ft_aci = 1.78 * math.sqrt(fck_kg) / 10
    return {
        "fck_n_mm2": fck_n_mm2,
        "modulus_of_rupture_n_mm2": round(fr, 3),
        "split_cylinder_aci_n_mm2": round(ft_aci, 3),
        "tensile_pct_of_compressive": round(ft_aci / fck_n_mm2 * 100, 1),
    }


def thermal_shrinkage_equivalence(shrinkage_strain: float = 0.0002, alpha_c: float = 10e-6) -> Dict[str, float]:
    """Convert shrinkage strain to equivalent temperature drop: delta_t = epsilon_sh / alpha_c."""
    return {
        "shrinkage_strain": shrinkage_strain,
        "equivalent_temp_drop_c": round(shrinkage_strain / alpha_c, 1),
    }


def concrete_thermal_cracking_check(core_temp_c: float, surface_temp_c: float) -> Dict[str, Any]:
    """Mass concrete limits: core <= 70C, delta_T <= 20C."""
    delta_t = core_temp_c - surface_temp_c
    core_ok = core_temp_c <= 70
    delta_ok = delta_t <= 20
    notes = []
    if not core_ok: notes.append(f"Core temp {core_temp_c}C > limit 70C")
    if not delta_ok: notes.append(f"Delta T = {delta_t}C > limit 20C")
    if core_ok and delta_ok: notes.append(f"OK - core={core_temp_c}C, dT={delta_t}C")
    return {
        "core_temp_c": core_temp_c, "surface_temp_c": surface_temp_c,
        "delta_t_c": round(delta_t, 1), "core_ok": core_ok, "delta_ok": delta_ok,
        "thermal_cracking_risk": not (core_ok and delta_ok), "notes": notes,
    }


def unit_weight_concrete(reinforced: bool = True) -> Dict[str, float]:
    """Unit weight: RC = 2500 kg/m3, plain = 2400 kg/m3 (range 2330-2470)."""
    if reinforced:
        return {"unit_weight_kg_m3": 2500, "type": "Reinforced Concrete"}
    return {"unit_weight_kg_m3": 2400, "type": "Plain Concrete", "range_kg_m3": (2330, 2470)}


def shear_stress_check(v_kn: float, b_mm: float, d_mm: float) -> Dict[str, float]:
    """Shear stress: v = V/(b x d) in N/mm2."""
    return {
        "shear_force_kn": v_kn, "width_mm": b_mm, "effective_depth_mm": d_mm,
        "shear_stress_n_mm2": round((v_kn * 1000) / (b_mm * d_mm), 3),
    }


# ═══════════════════════════════════════════════════════════════════════════
#  SECTION 3 — STRUCTURAL SYSTEMS
# ═══════════════════════════════════════════════════════════════════════════

def post_tensioning_force(
    span_m: float, slab_thickness_m: float, live_load_kn_m2: float,
    dead_load_kn_m2: Optional[float] = None,
    tendon_stress_n_mm2: float = 1300.0,
    tendon_diameter_mm: float = 12.7,
    eccentricity_ratio: float = 0.15,
) -> Dict[str, Any]:
    """PT force: P = w x L^2 / (8 x e), balancing 75% of DL."""
    if dead_load_kn_m2 is None:
        dead_load_kn_m2 = slab_thickness_m * 25
    e = eccentricity_ratio * slab_thickness_m
    balanced = dead_load_kn_m2 * 0.75
    force_kn = balanced * span_m**2 / (8 * e) if e > 0 else 0
    area_mm2 = (force_kn * 1000) / tendon_stress_n_mm2
    strand_area = math.pi * (tendon_diameter_mm / 2)**2
    strands = math.ceil(area_mm2 / strand_area) if strand_area > 0 else 0
    return {
        "tendon_force_kn": round(force_kn, 1),
        "tendon_area_mm2": round(area_mm2, 1),
        "num_strands": strands,
        "balanced_load_kn_m": round(balanced, 2),
        "eccentricity_mm": round(e * 1000, 1),
        "cable_profile": f"parabolic, e={e*1000:.0f}mm midspan",
    }


def composite_column_design(
    axial_load_kn: float, column_diameter_mm: float,
    concrete_grade_n_mm2: float = 60.0,
    use_i_beam: bool = True, i_beam_weight_t: float = 0.0,
) -> Dict[str, Any]:
    """Compare C60+I-Beam vs C80+rebar for composite columns."""
    col_area = math.pi * (column_diameter_mm / 2)**2
    cap_a = col_area * concrete_grade_n_mm2 / 1000 * (1.10 if use_i_beam and i_beam_weight_t > 0 else 1.0)
    upgraded = min(concrete_grade_n_mm2 * 1.33, 80.0)
    cap_b = col_area * upgraded / 1000 * 0.95
    return {
        "option_a": {"type": f"C{concrete_grade_n_mm2:.0f}+I-Beam", "capacity_kn": round(cap_a, 0),
                     "is_safe": cap_a >= axial_load_kn},
        "option_b": {"type": f"C{upgraded:.0f}+rebar", "capacity_kn": round(cap_b, 0),
                     "is_safe": cap_b >= axial_load_kn},
        "recommended": "B" if cap_b >= axial_load_kn and not (cap_a >= axial_load_kn) else (
            "A or B" if cap_a >= axial_load_kn and cap_b >= axial_load_kn else "A" if cap_a >= axial_load_kn else "NEITHER"),
    }


def wind_load_on_formwork(
    wind_velocity_m_s: float, formwork_area_m2: float,
    formwork_height_m: float, formwork_width_m: float,
    shape_factor: float = 1.0,
) -> Dict[str, float]:
    """Wind on climbing formwork: q = 0.613 x V^2, F = q x A, M = F x h/2."""
    q = 0.613 * wind_velocity_m_s**2
    force_n = q * formwork_area_m2 * shape_factor
    moment_n_m = force_n * (formwork_height_m / 2)
    z = formwork_width_m * formwork_height_m**2 / 6
    return {
        "wind_pressure_kpa": round(q / 1000, 3),
        "wind_force_kn": round(force_n / 1000, 3),
        "overturning_moment_kn_m": round(moment_n_m / 1000, 3),
        "bending_stress_n_m2": round(moment_n_m / z, 2) if z > 0 else 0,
    }


def foundation_bearing_pressure(
    foundation_width_m: float, foundation_length_m: float,
    column_load_kn: float, soil_bearing_capacity_kn_m2: float,
    foundation_depth_m: float = 0.0,
) -> Dict[str, float]:
    """q = P/A, FOS vs soil capacity."""
    area = foundation_width_m * foundation_length_m
    q = column_load_kn / area
    net_q = q - foundation_depth_m * 18
    fos = soil_bearing_capacity_kn_m2 / net_q if net_q > 0 else float("inf")
    return {
        "bearing_pressure_kn_m2": round(q, 2),
        "net_pressure_kn_m2": round(net_q, 2),
        "fos_against_bearing": round(fos, 2),
        "bearing_ok": fos >= 2.0,
    }


def precast_beam_erection_check(
    beam_weight_t: float, beam_length_m: float,
    crane_capacity_t: float, lift_radius_m: float,
    rigging_weight_t: float = 0.5, dynamic_factor: float = 1.25,
) -> Dict[str, Any]:
    """Crane lift check: effective = (beam + rigging) x 1.25 dynamic."""
    effective = (beam_weight_t + rigging_weight_t) * dynamic_factor
    util = effective / crane_capacity_t * 100
    return {
        "effective_lift_weight_t": round(effective, 2),
        "utilization_pct": round(util, 1),
        "is_safe": util <= 100,
    }


# ═══════════════════════════════════════════════════════════════════════════
#  SECTION 4 — CRANE PLANNING (by Tonnage)
# ═══════════════════════════════════════════════════════════════════════════

def crane_planning(
    total_lift_demand_tons: float,
    crane_capacity_tons: float,
    utilization_pct: float = 65.0,
    shifts_per_day: int = 2,
    hours_per_shift: float = 9.5,
    cycle_time_minutes: float = 20.0,
    working_days: int = 26,
) -> Dict[str, Any]:
    """
    Determine number of cranes needed to service construction lifting demand.

    Formula:
        lifts_per_hour = 60 / cycle_time
        daily_capacity_per_crane = capacity x (util/100) x lifts_per_hour x hours x shifts
        num_cranes = ceil(total_demand / (daily_capacity x working_days))

    Typical crane rental rates (GCC, dry hire without operator):
        25-30 MT:  8,000-12,000 SAR/month
        50 MT:     15,000-20,000 SAR/month
        100 MT:    25,000-35,000 SAR/month
        200 MT:    60,000-80,000 SAR/month
        300+ MT:   100,000+ SAR/month

    Args:
        total_lift_demand_tons: Total tonnage to be lifted over the period
        crane_capacity_tons: Single crane rated capacity (MT)
        utilization_pct: Effective utilization (default 65%)
        shifts_per_day: Number of shifts (default 2)
        hours_per_shift: Hours per shift (default 9.5)
        cycle_time_minutes: Average cycle time per lift (default 20 min)
        working_days: Working days per month (default 26)

    Returns:
        Dict with crane count, monthly cost, and capacity analysis
    """
    lifts_per_hour = 60.0 / cycle_time_minutes
    effective_capacity_per_lift = crane_capacity_tons * (utilization_pct / 100.0)
    daily_lifts_per_crane = lifts_per_hour * hours_per_shift * shifts_per_day
    daily_tonnage_per_crane = effective_capacity_per_lift * daily_lifts_per_crane

    total_working_days = working_days  # Assume 1 month period; scale for longer
    total_capacity_per_crane = daily_tonnage_per_crane * total_working_days

    num_cranes = math.ceil(total_lift_demand_tons / total_capacity_per_crane) if total_capacity_per_crane > 0 else 0

    # Monthly rental rate estimate (SAR/month, dry hire)
    rate_table = {
        25: 8000, 30: 10000, 50: 18000, 100: 30000,
        200: 70000, 300: 120000, 500: 200000,
    }
    # Interpolate or find closest
    closest_cap = min(rate_table.keys(), key=lambda c: abs(c - crane_capacity_tons))
    monthly_rate = rate_table.get(closest_cap, 30000)

    total_monthly_cost = num_cranes * monthly_rate

    return {
        "total_lift_demand_tons": total_lift_demand_tons,
        "crane_capacity_tons": crane_capacity_tons,
        "utilization_pct": utilization_pct,
        "shifts_per_day": shifts_per_day,
        "hours_per_shift": hours_per_shift,
        "cycle_time_minutes": cycle_time_minutes,
        "lifts_per_hour_per_crane": round(lifts_per_hour, 1),
        "daily_tonnage_per_crane": round(daily_tonnage_per_crane, 1),
        "cranes_required": num_cranes,
        "monthly_rate_sar": monthly_rate,
        "total_monthly_cost_sar": total_monthly_cost,
        "notes": [
            f"Each {crane_capacity_tons}T crane handles {daily_tonnage_per_crane:.1f} tons/day",
            f"At {utilization_pct}% utilization, {lifts_per_hour:.1f} lifts/hr, {cycle_time_minutes}min cycle",
            f"Require {num_cranes} crane(s) @ {monthly_rate:,} SAR/month each",
            f"Total crane cost: {total_monthly_cost:,} SAR/month",
        ],
    }


def crane_cost_estimate(
    num_cranes: int,
    crane_capacity_tons: float,
    duration_months: int,
    include_operator: bool = True,
    include_riggers: bool = True,
    remote_area_factor: float = 1.0,
) -> Dict[str, float]:
    """
    Estimate total crane project cost including operator and riggers.

    Args:
        num_cranes: Number of cranes
        crane_capacity_tons: Capacity per crane
        duration_months: Project duration in months
        include_operator: Add operator cost (default True)
        include_riggers: Add rigger cost (default True)
        remote_area_factor: Multiplier for remote sites (default 1.0, oil/gas = 1.3-1.5)

    Returns:
        Dict with breakdown
    """
    rate_table = {25: 8000, 30: 10000, 50: 18000, 100: 30000, 200: 70000, 300: 120000}
    closest = min(rate_table.keys(), key=lambda c: abs(c - crane_capacity_tons))
    dry_hire_monthly = rate_table.get(closest, 30000)

    # Operator: ~3,500 SAR/month per crane
    operator_monthly = 3500 if include_operator else 0
    # Riggers (2 per crane): ~2,800 SAR/month each
    riggers_monthly = 5600 if include_riggers else 0

    per_crane_monthly = (dry_hire_monthly + operator_monthly + riggers_monthly) * remote_area_factor
    total_project_cost = per_crane_monthly * num_cranes * duration_months
    mobilization = num_cranes * dry_hire_monthly * 0.5  # ~50% of monthly rate per crane
    demobilization = mobilization * 0.75

    return {
        "num_cranes": num_cranes,
        "crane_capacity_tons": crane_capacity_tons,
        "duration_months": duration_months,
        "dry_hire_sar_month": dry_hire_monthly,
        "operator_sar_month": operator_monthly,
        "riggers_sar_month": riggers_monthly,
        "remote_area_factor": remote_area_factor,
        "per_crane_monthly_sar": round(per_crane_monthly, 0),
        "total_project_cost_sar": round(total_project_cost, 0),
        "mobilization_sar": round(mobilization, 0),
        "demobilization_sar": round(demobilization, 0),
        "grand_total_sar": round(total_project_cost + mobilization + demobilization, 0),
    }


# ═══════════════════════════════════════════════════════════════════════════
#  SECTION 5 — COST ESTIMATION
# ═══════════════════════════════════════════════════════════════════════════

def cost_buildup_concrete(
    quantity_m3: float,
    cement_kg_m3: float = 400,
    cement_price_sar_t: float = 190,
    aggregate_price_sar_t: float = 29,
    water_price_sar_m3: float = 10,
    microsilica_kg_m3: float = 12,
    microsilica_price_sar_kg: float = 1.5,
    plasticizer_lit_m3: float = 5,
    plasticizer_price_sar_lit: float = 2.4,
    plant_cost_sar_m3: float = 39.2,
    labour_cost_sar_m3: float = 8.0,
    erection_sar_m3: float = 3.0,
    power_sar_m3: float = 2.5,
    indirect_sar_m3: float = 4.0,
    indirect_pct: float = 0.18,
    markup_pct: float = 0.15,
    waste_pct: float = 0.03,
) -> Dict[str, float]:
    """Full concrete cost build-up (SAR/m3)."""
    cement_cost = (cement_kg_m3 / 1000) * cement_price_sar_t
    aggregate_cost = (1839 / 1000) * aggregate_price_sar_t  # ~1839 kg/m3 combined
    water_cost = (160 / 1000) * water_price_sar_m3
    material_cost = cement_cost + aggregate_cost + water_cost + (microsilica_kg_m3 * microsilica_price_sar_kg) + (plasticizer_lit_m3 * plasticizer_price_sar_lit)
    direct = material_cost + plant_cost_sar_m3 + labour_cost_sar_m3 + erection_sar_m3 + power_sar_m3 + indirect_sar_m3
    with_waste = direct * (1 + waste_pct)
    with_indirect = with_waste * (1 + indirect_pct)
    with_markup = with_indirect / (1 - markup_pct)
    return {
        "material_cost_sar_m3": round(material_cost, 2),
        "direct_cost_sar_m3": round(direct, 2),
        "selling_price_sar_m3": round(with_markup, 2),
        "total_project_value_sar": round(with_markup * quantity_m3, 0),
    }


def cost_buildup_rebar(
    quantity_kg: float,
    material_price_sar_t: float = 2600,
    labour_mhr_t: float = 90,
    labour_rate_sar_hr: float = 4.1,
    crane_hr_t: float = 2,
    crane_rate_sar_hr: float = 134.6,
    waste_pct: float = 0.10,
    indirect_pct: float = 0.18,
    markup_pct: float = 0.15,
) -> Dict[str, float]:
    """Rebar cost build-up (SAR/Tonne)."""
    qty_t = quantity_kg / 1000
    material = qty_t * material_price_sar_t * (1 + waste_pct)
    labour = qty_t * labour_mhr_t * labour_rate_sar_hr
    equipment = qty_t * crane_hr_t * crane_rate_sar_hr
    direct = material + labour + equipment
    with_markup = direct * (1 + indirect_pct) / (1 - markup_pct)
    return {
        "material_sar_t": round(material / qty_t, 0) if qty_t > 0 else 0,
        "labour_sar_t": round(labour / qty_t, 0) if qty_t > 0 else 0,
        "equipment_sar_t": round(equipment / qty_t, 0) if qty_t > 0 else 0,
        "selling_price_sar_t": round(with_markup / qty_t, 0) if qty_t > 0 else 0,
    }


def cost_buildup_formwork(
    area_m2: float,
    shuttering_supply_sar_m2: float = 55,
    scaffolding_sar_m2_day: float = 2.15,
    cycle_days: int = 10,
    labour_mhr_m2: float = 9,
    labour_rate_sar_hr: float = 4.1,
    crane_rate_sar_hr: float = 134.6,
    crane_output_m2_hr: float = 30,
    indirect_pct: float = 0.18,
    markup_pct: float = 0.15,
) -> Dict[str, float]:
    """Formwork cost build-up (SAR/m2)."""
    shuttering = shuttering_supply_sar_m2 / 6  # 6 uses
    scaffolding = scaffolding_sar_m2_day * cycle_days
    material = shuttering + scaffolding
    labour = labour_mhr_m2 * labour_rate_sar_hr
    equipment = crane_rate_sar_hr / crane_output_m2_hr
    direct = material + labour + equipment
    with_markup = direct * (1 + indirect_pct) / (1 - markup_pct)
    return {
        "shuttering_sar_m2": round(shuttering, 2),
        "scaffolding_sar_m2": round(scaffolding, 2),
        "material_sar_m2": round(material, 2),
        "labour_sar_m2": round(labour, 2),
        "selling_price_sar_m2": round(with_markup, 2),
        "total_for_area_sar": round(with_markup * area_m2, 0),
    }


# ═══════════════════════════════════════════════════════════════════════════
#  SECTION 6 — MOBILIZATION (for Schedule Production)
# ═══════════════════════════════════════════════════════════════════════════

NOTE_REMOTE_AREA = (
    "Note: Rates include remote area and oil/gas construction project factors. "
    "These are typically 30-50% higher than standard urban construction rates."
)


def mobilization_cost_estimate(
    num_personnel: int,
    duration_months: int,
    camp_type: str = "rented",  # "rented", "self_operated", "hotel"
    include_offices: bool = True,
    include_camp: bool = True,
    include_transport: bool = True,
    include_safety_medical: bool = True,
    remote_area_factor: float = 1.35,
) -> Dict[str, Any]:
    """
    Mobilization cost estimate for construction site setup.

    Covers: offices, camp/accommodation, transport, safety/medical, IT,
    warehouse equipment, demobilization.

    Args:
        num_personnel: Total site personnel count
        duration_months: Project duration in months
        camp_type: "rented" | "self_operated" | "hotel"
        remote_area_factor: 1.0 urban, 1.35 remote, 1.5 very remote
    """
    # Per-person monthly rates (SAR)
    office_per_person = 350 if include_offices else 0

    if camp_type == "rented":
        camp_per_person = 2800
    elif camp_type == "self_operated":
        camp_per_person = 1800
    else:  # hotel
        camp_per_person = 4500

    transport_per_person = 220 if include_transport else 0
    safety_medical_per_person = 150 if include_safety_medical else 0

    total_monthly = (office_per_person + camp_per_person + transport_per_person +
                     safety_medical_per_person) * num_personnel * remote_area_factor

    # Fixed costs
    office_setup = 450000 if include_offices else 0  # Furniture, fencing, parking
    camp_setup = 60000   # Plumbing, electrical, fuel station
    demobilization = 280000
    safety_setup = 40000  # PPE, signs, training, badging system

    # IT costs
    it_costs = num_personnel * 85 * duration_months  # ~85 SAR/person/month

    # Warehouse equipment (forklift, crane for laydown)
    warehouse_equip = 35000 * remote_area_factor

    recurring = total_monthly * duration_months
    fixed = (office_setup + camp_setup + demobilization + safety_setup +
             it_costs + warehouse_equip) * remote_area_factor

    grand_total = recurring + fixed

    return {
        "num_personnel": num_personnel,
        "duration_months": duration_months,
        "camp_type": camp_type,
        "remote_area_factor": remote_area_factor,
        "note": NOTE_REMOTE_AREA,
        "breakdown": {
            "offices_sar": round(office_setup * remote_area_factor, 0),
            "camp_sar": round((camp_setup + recurring) * remote_area_factor, 0),
            "transport_sar": round(transport_per_person * num_personnel * duration_months * remote_area_factor, 0),
            "safety_medical_sar": round((safety_setup + safety_medical_per_person * num_personnel * duration_months) * remote_area_factor, 0),
            "it_sar": round(it_costs * remote_area_factor, 0),
            "warehouse_equipment_sar": round(warehouse_equip, 0),
            "demobilization_sar": round(demobilization * remote_area_factor, 0),
        },
        "recurring_monthly_sar": round(total_monthly, 0),
        "total_recurring_sar": round(recurring, 0),
        "total_fixed_sar": round(fixed, 0),
        "grand_total_sar": round(grand_total, 0),
    }


def supervision_ratio(
    concrete_m3: float = 0,
    structural_steel_t: float = 0,
    piping_dia_inch: float = 0,
    electrical_cable_km: float = 0,
    area_m2: float = 0,
) -> Dict[str, Any]:
    """
    Supervision manpower based on quantities.
    Rules of thumb:
      - 1 civil supervisor per 30,000-40,000 m3 concrete
      - 1 structural supervisor per 2,000-3,000 T steel
      - 1 piping supervisor per 15,000-20,000 dia-inch
      - 1 electrical supervisor per 50-80 km cable
    """
    civil_sup = math.ceil(concrete_m3 / 35000) if concrete_m3 > 0 else 0
    steel_sup = math.ceil(structural_steel_t / 2500) if structural_steel_t > 0 else 0
    piping_sup = math.ceil(piping_dia_inch / 17500) if piping_dia_inch > 0 else 0
    elec_sup = math.ceil(electrical_cable_km / 65) if electrical_cable_km > 0 else 0
    general_sup = math.ceil(area_m2 / 20000) if area_m2 > 0 else 0

    total_sup = civil_sup + steel_sup + piping_sup + elec_sup + general_sup
    hse_officers = max(2, math.ceil(total_sup / 8))  # 1 HSE per 8 supervisors
    document_controllers = max(1, math.ceil(total_sup / 15))

    return {
        "civil_supervisors": civil_sup,
        "structural_supervisors": steel_sup,
        "piping_supervisors": piping_sup,
        "electrical_supervisors": elec_sup,
        "general_supervisors": general_sup,
        "total_supervisors": total_sup,
        "hse_officers": hse_officers,
        "document_controllers": document_controllers,
        "total_supervision_staff": total_sup + hse_officers + document_controllers,
        "notes": [
            f"1 civil supervisor per ~35,000 m3 concrete",
            f"1 structural supervisor per ~2,500 T steel",
            f"1 piping supervisor per ~17,500 dia-inch",
            f"1 electrical supervisor per ~65 km cable",
            f"1 HSE officer per 8 supervisors",
        ],
    }


# ═══════════════════════════════════════════════════════════════════════════
#  SECTION 7 — MEP SEQUENCING (for Schedule Production)
# ═══════════════════════════════════════════════════════════════════════════

def electrical_installation_sequence(
    floor_area_m2: float, num_floors: int = 1,
) -> Dict[str, Any]:
    """Electrical programme: 1st fix -> 2nd fix -> test -> commission -> handover."""
    total = floor_area_m2 * num_floors
    rates = {"1st_fix_conduit_boxes": 50, "2nd_fix_equipment_panels": 75,
             "cabling_testing": 100, "final_test_temp_power": 100,
             "authority_inspection": 50, "final_fix_fittings": 75,
             "commissioning": 30}
    stages = {k: math.ceil(total / v) for k, v in rates.items()}
    return {"total_area_m2": total, "stages": stages, "total_days": sum(stages.values())}


def plumbing_flow_programme(floor_area_m2: float, num_floors: int = 1) -> Dict[str, Any]:
    """Plumbing flow: submittal -> procure -> 1st/2nd fix -> connect -> handover."""
    total = floor_area_m2 * num_floors
    stages = [
        ("material_submittal", 7), ("procurement", 21),
        ("1st_fix_marking_piping", max(1, math.ceil(total / 40))),
        ("2nd_fix_sanitary_fittings", max(1, math.ceil(total / 60))),
        ("final_fix_pumps_tanks", max(1, math.ceil(total / 80))),
        ("water_connection", 7),
        ("testing_commissioning", max(1, math.ceil(total / 50))),
        ("handover", 3),
    ]
    result = {"stages": {}, "total_days": 0}
    cum = 0
    for name, days in stages:
        cum += days
        result["stages"][name] = {"days": days, "cumulative_days": cum}
    result["total_days"] = cum
    return result


# ═══════════════════════════════════════════════════════════════════════════
#  SECTION 8 — GROUTING & MATURITY
# ═══════════════════════════════════════════════════════════════════════════

def grout_pressure_calc(
    tendon_duct_diameter_mm: float,
    required_pressure_n_mm2: float = 0.5,
    strength_28d_n_mm2: float = 35.0,
    strength_7d_n_mm2: float = 20.0,
    mixing_time_minutes: float = 2.0,
) -> Dict[str, float]:
    """Grouting for PT: 0.5 N/mm2 (5 kg/cm2 = 75 PSI). 35 N/mm2 @ 28d, >=20 @ 7d."""
    area_mm2 = math.pi * (tendon_duct_diameter_mm / 2)**2
    return {
        "duct_area_mm2": round(area_mm2, 2),
        "grout_volume_l_m": round(area_mm2 * 1000 / 1e6, 3),
        "pressure_n_mm2": required_pressure_n_mm2,
        "pressure_kg_cm2": round(required_pressure_n_mm2 * 10.197, 1),
        "pressure_psi": round(required_pressure_n_mm2 * 145.038, 0),
        "strength_28d_n_mm2": strength_28d_n_mm2,
        "strength_7d_n_mm2": strength_7d_n_mm2,
        "mixing_time_min": mixing_time_minutes,
    }


def concrete_maturity_strength(
    temperature_history_c: List[float],
    time_intervals_hours: List[float],
    datum_temperature: float = -10.0,
    strength_28d_n_mm2: float = 40.0,
) -> Dict[str, float]:
    """Nurse-Saul maturity: MI = sum((T_avg - T0) x dt)."""
    mi = sum(max(0, t - datum_temperature) * dt for t, dt in zip(temperature_history_c, time_intervals_hours))
    avg_t = sum(t * dt for t, dt in zip(temperature_history_c, time_intervals_hours)) / sum(time_intervals_hours) if time_intervals_hours else 0
    ratio = (1055 + (625 * avg_t)) / ((600 + 625) * avg_t) if avg_t > 0 else 0
    ratio = min(ratio, 1.0)
    return {
        "maturity_index_c_hrs": round(mi, 0),
        "predicted_strength_n_mm2": round(strength_28d_n_mm2 * ratio, 1),
        "percent_of_28d": round(ratio * 100, 1),
    }


# ═══════════════════════════════════════════════════════════════════════════
#  DETERMINISTIC DISPATCH — the agent's `construction_calc` tool routes here.
# ═══════════════════════════════════════════════════════════════════════════

import dataclasses as _dc
import inspect as _inspect
import logging

logger = logging.getLogger(__name__)


# Public functions in this module that are DISPATCH infrastructure, not
# calculators — excluded from the registry regardless of definition order.
_NON_CALCULATORS = {"available_calculations", "run_calculation"}


def _build_calculator_registry() -> "Dict[str, Any]":
    """Every public calculator defined in this module, by name. Drift-free:
    a new calculator is exposed automatically (and its smoke-input guard test
    fails until it is covered)."""
    reg: Dict[str, Any] = {}
    for _name, _obj in _inspect.getmembers(__import__(__name__, fromlist=["*"]),
                                           _inspect.isfunction):
        if _name.startswith("_") or _name in _NON_CALCULATORS:
            continue
        if getattr(_obj, "__module__", None) != __name__:
            continue  # skip imported names (dataclass/field/etc.)
        reg[_name] = _obj
    # Also expose the reporting / commercial / procurement / risk calculators
    # that live in construction_knowledge (EVM, IPC payment, tender scoring,
    # PRC-302 risk). REFERENCED, not copied — construction_knowledge stays the
    # single source of that maths; construction_calc just makes them callable
    # (they were tested but 0-caller). Optional import: if unavailable, the
    # engineering calculators above still work.
    try:
        from app.core import construction_knowledge as _ck
        for _n in ("calculate_evm", "calculate_payment", "evaluate_tender", "score_risk"):
            _f = getattr(_ck, _n, None)
            if callable(_f):
                reg[_n] = _f
    except Exception:  # noqa: BLE001 — never break the formula registry on import
        logger.warning(
            "swallowed %s in _build_calculator_registry() — continuing",
            "Exception", exc_info=True,
        )
    # Post-live-chat additions (guardrail height, interim payment) live in a
    # separate module so they can also feed the discipline-hats binding
    # resolver. Merge them here too so the LIVE construction_calc tool can
    # actually call them — before this they resolved ONLY through the flag-off
    # hats resolver and were unreachable from the running agent (the Q2/Q12
    # "registered in CALCULATORS" gap). REFERENCED, single source stays in the
    # additions module.
    try:
        from app.lib.construction_formulas_additions import ADDITIONAL_CALCULATORS as _add
        for _n, _f in _add.items():
            if callable(_f):
                reg[_n] = _f
    except Exception:  # noqa: BLE001 — never break the formula registry on import
        logger.warning(
            "swallowed %s in _build_calculator_registry() — continuing",
            "Exception", exc_info=True,
        )
    # Additive discipline modules (the drop-catalog gap-fill library). Each
    # module exposes ADDITIONAL_CALCULATORS; listed here so a new discipline is a
    # one-line addition and never touches the existing calculators. Every merged
    # name is gated by the coverage guard + a per-formula oracle test. See
    # docs/additive-formula-library-scope.md.
    for _ext in _EXTENSION_MODULES:
        try:
            _mod = __import__(_ext, fromlist=["ADDITIONAL_CALCULATORS"])
            for _n, _f in getattr(_mod, "ADDITIONAL_CALCULATORS", {}).items():
                if callable(_f):
                    reg[_n] = _f
        except Exception:  # noqa: BLE001 — a broken/absent module never breaks the registry
            logger.warning(
                "swallowed %s in _build_calculator_registry() — continuing",
                "Exception", exc_info=True,
            )
    return reg


# Discipline extension modules merged into CALCULATORS (order-independent; a
# later module wins a name collision, but names are unique by design).
_EXTENSION_MODULES: Tuple[str, ...] = (
    "app.lib.construction_formulas_structural_steel",
    "app.lib.construction_formulas_loads",
    "app.lib.construction_formulas_quantities",
    "app.lib.construction_formulas_earthwork",
    "app.lib.construction_formulas_beam_analysis",
    "app.lib.construction_formulas_structural_rc",
    "app.lib.construction_formulas_qc",
    "app.lib.construction_formulas_commercial",
    "app.lib.construction_formulas_planning",
    "app.lib.construction_formulas_safety",
    "app.lib.construction_formulas_reference_tables",
    "app.lib.construction_formulas_columns",
    "app.lib.construction_formulas_masonry",
)


CALCULATORS: Dict[str, Any] = _build_calculator_registry()


def available_calculations() -> List[str]:
    return sorted(CALCULATORS)


def _result_is_failure(result: Dict[str, Any]) -> bool:
    """Did a calculator report failure by RETURNING rather than raising?

    Keyed on a STRING "error" deliberately: a NUMERIC field named "error" is a
    tolerance or deviation -- data, not a failure signal -- and must not be
    mistaken for one. (Swept 2026-08-15 across all registered calculators: none
    currently returns a numeric "error".)
    """
    return isinstance(result.get("error"), str)


def run_calculation(name: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Run one whitelisted deterministic calculator by name with keyword params.

    Returns a standard envelope: {status, calculation, result, note} on success,
    or {status:error, error, ...} on a bad name / bad params. COST build-ups use
    the unit rates passed in ``params``; the built-in defaults are indicative GCC
    fallbacks only — pass the project's priced-BOQ rates (from RAG) for a firm
    cost so the answer stays grounded.
    """
    fn = CALCULATORS.get(name)
    if fn is None:
        return {
            "status": "error",
            "error": f"Unknown calculation '{name}'.",
            "available": available_calculations(),
        }
    params = params or {}
    if not isinstance(params, dict):
        return {"status": "error", "error": "params must be an object of keyword arguments."}
    try:
        result = fn(**params)
    except TypeError as exc:
        # Wrong / missing kwargs — surface the real signature, don't fabricate.
        sig = str(_inspect.signature(fn))
        return {
            "status": "error",
            "error": f"Bad parameters for {name}: {exc}",
            "signature": f"{name}{sig}",
        }
    except Exception as exc:  # noqa: BLE001 — never raise into the agent loop
        return {"status": "error", "error": f"{name} failed: {exc}"}

    if _dc.is_dataclass(result):
        result = _dc.asdict(result)
    elif not isinstance(result, dict):
        result = {"value": result}

    # Not every calculator signals failure by RAISING. Several (score_risk and
    # friends in app/core/construction_knowledge.py) validate their inputs and
    # RETURN {"error": "..."} instead. Without this, that reply was wrapped as
    # status:"success" -- an error envelope inside a green one, which the agent
    # tool loop reads as a working answer and narrates as a result.
    #
    if _result_is_failure(result):
        return {
            "status": "error",
            "calculation": name,
            "error": result["error"],
        }

    return {
        "status": "success",
        "calculation": name,
        "result": result,
        "note": (
            "Deterministic engineering calculation. Any cost figure uses the unit "
            "rates provided (or indicative GCC defaults if none were given) — for a "
            "firm cost, supply the project's priced-BOQ / rate-schedule rates."
        ),
    }
