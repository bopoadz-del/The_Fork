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
    if water_depth < 0:
        raise ValueError("water_depth must be >= 0")
    if raft_thickness < 0 or floor_count < 0 or floor_thickness < 0:
        raise ValueError("raft_thickness, floor_count and floor_thickness must be >= 0")
    if concrete_unit_weight <= 0 or water_unit_weight <= 0 or required_fos <= 0:
        raise ValueError("unit weights and required_fos must be > 0")
    uplift = water_depth * water_unit_weight
    raft_weight = raft_thickness * concrete_unit_weight
    floor_weight = floor_count * floor_thickness * concrete_unit_weight
    counter_weight = raft_weight + floor_weight
    fos = counter_weight / uplift if uplift > 0 else float("inf")

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
    if panel_length <= 0 or wall_thickness <= 0 or excavation_depth <= 0:
        raise ValueError("panel_length, wall_thickness and excavation_depth must be > 0")
    if panel_count <= 0:
        raise ValueError("panel_count must be > 0")
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
    if soil_permeability_m_s <= 0:
        raise ValueError("soil_permeability_m_s must be > 0")
    if required_drawdown_m < 0:
        raise ValueError("required_drawdown_m must be >= 0")
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
    strength_ok = (
        concrete_strength_7h >= ciria_surface_strength
        and concrete_strength_7h >= design_required_strength
    )
    based_on = "test_data" if strength_ok else "insufficient"
    recommended_hours = (
        proposed_hours if strength_ok else max(proposed_hours, bs8110_minimum_hours)
    )
    notes.append(f"Recommended: {recommended_hours}h after pour")
    return FormworkStrikingResult(
        recommended_hours=recommended_hours,
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
    if w_c_ratio <= 0:
        raise ValueError("w_c_ratio must be > 0")
    if min(cement_sg, fine_agg_sg, coarse_agg_sg) <= 0:
        raise ValueError("specific gravities must be > 0")
    if fine_agg_ratio < 0 or coarse_agg_ratio < 0:
        raise ValueError("aggregate ratios must be >= 0")
    if float(dune_sand_pct) != 0.0:
        raise ValueError(
            "dune_sand_pct is not applied in the absolute-volume mix "
            "(no dune-sand SG is defined). Omit it or pass 0."
        )
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


def concrete_mix_slip_form(**_kwargs: Any) -> Dict[str, Any]:
    """Slip-forming mix: 1:2:2.6, W/C=0.42, slump=150 +/- 30 mm.

    Extra kwargs are ignored. Documented constants stay locked — live
    probes send slump/w_c next to the name; forwarding them would
    TypeError a zero-arg signature (tool_error / cause).
    """
    mix = concrete_mix_design_sg(w_c_ratio=0.42, fine_agg_ratio=2.0, coarse_agg_ratio=2.6)
    mix["slump_mm"] = "150 +/- 30"
    mix["retarder_20c_lit_m3"] = 3.8
    mix["retarder_10c_lit_m3"] = 2.9
    mix["max_concrete_temp_c"] = 32
    mix["cube_strength_n_mm2"] = "50-55 @ 28 days"
    return mix


def modulus_of_elasticity_concrete(
    fck_n_mm2: float, code: str = "metric_technical",
) -> dict:
    """Concrete modulus of elasticity, with its unit stated.

    Two forms, because they are different numbers and a reader cannot tell
    them apart from a bare float:

    * ``metric_technical`` (default, unchanged): Ec = 15000 sqrt(f'c) with
      f'c in kg/cm2 -- for C35 that is 280,624 kg/cm2.
    * ``aci``: ACI 318-19 Eq. 19.2.2.1b SI form, Ec = 4700 sqrt(f'c) in MPa
      -- for C35, 27,806 MPa.

    Live T12: this returned the bare float 280624.0. The answer stated the ACI
    formula and its substitution correctly, then printed "28,062 MPa" -- the
    kg/cm2 figure divided by 10 instead of converted (x0.0980665 = 27,520),
    and neither number is the 27,806 the ACI form gives. The value now carries
    its unit, and the MPa conversion is done here rather than guessed.
    """
    # Not _norm_code: that helper defaults every unknown string to ACI,
    # which would silently change this function's default form.
    fck = float(fck_n_mm2)
    if (code or "").strip().lower().replace("-", "_") in (
        "aci", "aci318", "aci_318", "aci_318_19", "aci_si",
    ):
        ec_mpa = round(4700 * math.sqrt(fck), 0)
        return {
            "value": ec_mpa,
            "unit": "MPa",
            "standard": "ACI 318-19 Eq. 19.2.2.1b (SI): 4700*sqrt(f'c)",
        }
    ec_kg_cm2 = round(15000 * math.sqrt(fck * 10), 0)
    return {
        "value": ec_kg_cm2,
        "unit": "kg/cm2",
        "value_mpa": round(ec_kg_cm2 * 0.0980665, 0),
        "standard": "15000*sqrt(f'c) with f'c in kg/cm2 (metric-technical form)",
    }


# A beam's second moment of area is quoted in m4 in a question and consumed in
# mm4 by every formula below -- 1e12 apart. Taking the stated number at face
# value returns a deflection in kilometres and prints it as millimetres, so an
# implausibly small value is refused rather than used. The smallest real
# section is orders above 1 mm4 (a 10 mm square bar is 833).
_I_MM4_FLOOR = 1.0


def _second_moment_mm4(i_mm4: float) -> float:
    """Check the second moment of area is in mm4, refusing a unit mix-up."""
    if i_mm4 <= 0:
        raise ValueError("i_mm4 must be > 0")
    if i_mm4 < _I_MM4_FLOOR:
        raise ValueError(
            f"i_mm4={i_mm4:g} is too small to be mm4 -- a value this size is "
            "in m4; multiply it by 1e12 (1 m4 = 1e12 mm4) and call again")
    return float(i_mm4)


def beam_deflection_ss_udl(w_kn_m: float, span_m: float, ec_mpa: float, i_mm4: float) -> float:
    """Simply supported, UDL: delta = 5wL^4 / (384EI). Returns mm."""
    if span_m < 0 or w_kn_m < 0:
        raise ValueError("span_m and w_kn_m must be >= 0")
    if ec_mpa <= 0:
        raise ValueError("ec_mpa must be > 0")
    i_mm4 = _second_moment_mm4(i_mm4)
    w_n_mm = w_kn_m  # kN/m = N/mm
    l_mm = span_m * 1e3
    return round((5 * w_n_mm * l_mm**4) / (384 * ec_mpa * i_mm4), 2)


def beam_deflection_cantilever_udl(w_kn_m: float, span_m: float, ec_mpa: float, i_mm4: float) -> float:
    """Cantilever, UDL: delta = wL^4 / (8EI). Returns mm."""
    if span_m < 0 or w_kn_m < 0:
        raise ValueError("span_m and w_kn_m must be >= 0")
    if ec_mpa <= 0:
        raise ValueError("ec_mpa must be > 0")
    i_mm4 = _second_moment_mm4(i_mm4)
    w_n_mm = w_kn_m
    l_mm = span_m * 1e3
    return round((w_n_mm * l_mm**4) / (8 * ec_mpa * i_mm4), 2)


def beam_deflection_cantilever_point_load(p_kn: float, span_m: float, ec_mpa: float, i_mm4: float) -> float:
    """Cantilever, point load at the tip: delta = PL^3 / (3EI). Returns mm.

    Distinct from ``beam_deflection_cantilever_udl``: substituting a point
    load into wL^4/8EI overstates a 4 m / 30 kN tip deflection by half.
    """
    if span_m < 0 or p_kn < 0:
        raise ValueError("span_m and p_kn must be >= 0")
    if ec_mpa <= 0:
        raise ValueError("ec_mpa must be > 0")
    i_mm4 = _second_moment_mm4(i_mm4)
    p_n = p_kn * 1e3
    l_mm = span_m * 1e3
    return round((p_n * l_mm**3) / (3 * ec_mpa * i_mm4), 2)


def beam_deflection_ss_point_load_midspan(p_kn: float, span_m: float, ec_mpa: float, i_mm4: float) -> float:
    """Simply supported, point load at midspan: delta = PL^3 / (48EI). Returns mm."""
    if span_m < 0 or p_kn < 0:
        raise ValueError("span_m and p_kn must be >= 0")
    if ec_mpa <= 0:
        raise ValueError("ec_mpa must be > 0")
    i_mm4 = _second_moment_mm4(i_mm4)
    p_n = p_kn * 1e3
    l_mm = span_m * 1e3
    return round((p_n * l_mm**3) / (48 * ec_mpa * i_mm4), 2)


def modulus_of_rupture(fck_n_mm2: float, code: str = "metric_technical") -> Dict[str, float]:
    """Tensile strength. Two forms, and the caller's code decides which.

    * ``metric_technical`` (default, unchanged): fr = 2.4 sqrt(f'c) with f'c
      in kg/cm2, plus the split-cylinder figure -- for C30 that is 4.157 MPa.
    * ``aci``: ACI 318-19 Eq. 19.2.3.1, fr = 0.62 sqrt(f'c) in MPa (normal
      weight, lambda = 1.0) -- for C30, 3.40 MPa.

    Live E12, asked as a follow-up naming ACI 318-19 for f'c = 30: this
    returned 4.157 and the operator was shown 4.16 MPa. The sibling
    ``modulus_of_elasticity_concrete`` had already been given this parameter;
    this one was left behind, so an ACI question got the metric-technical
    answer with no sign that the code had been ignored.
    """
    # Not _norm_code: it defaults every unknown string to ACI, which would
    # silently change this function's default form.
    fck = float(fck_n_mm2)
    if (code or "").strip().lower().replace("-", "_") in (
        "aci", "aci318", "aci_318", "aci_318_19", "aci_si",
    ):
        fr_mpa = round(0.62 * math.sqrt(fck), 3)
        return {
            "fck_n_mm2": fck_n_mm2,
            "value": fr_mpa,
            "unit": "MPa",
            "modulus_of_rupture_n_mm2": fr_mpa,
            "standard": "ACI 318-19 Eq. 19.2.3.1: 0.62*sqrt(f'c) MPa, normal weight",
        }
    fck_kg = fck * 10
    fr = 2.4 * math.sqrt(fck_kg) / 10
    ft_aci = 1.78 * math.sqrt(fck_kg) / 10
    return {
        "fck_n_mm2": fck_n_mm2,
        "value": round(fr, 3),
        "unit": "MPa",
        "modulus_of_rupture_n_mm2": round(fr, 3),
        "split_cylinder_aci_n_mm2": round(ft_aci, 3),
        "tensile_pct_of_compressive": round(ft_aci / fck_n_mm2 * 100, 1),
        "standard": "2.4*sqrt(f'c) with f'c in kg/cm2 (metric-technical form)",
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
    # 7-wire strand fill ≈ 0.78 (ASTM A416 / EN 10138: 12.7 mm → 98.7 mm²).
    strand_area = 0.78 * math.pi * (tendon_diameter_mm / 2)**2
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
    if column_diameter_mm <= 0:
        raise ValueError("column_diameter_mm must be > 0")
    if axial_load_kn < 0 or concrete_grade_n_mm2 <= 0:
        raise ValueError("axial_load_kn must be >= 0 and concrete_grade_n_mm2 must be > 0")
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
    if foundation_width_m <= 0 or foundation_length_m <= 0:
        return {"error": "foundation_width_m and foundation_length_m must be > 0"}
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
    if total_lift_demand_tons < 0:
        raise ValueError("total_lift_demand_tons must be >= 0")
    if crane_capacity_tons <= 0 or cycle_time_minutes <= 0 or working_days <= 0:
        raise ValueError("crane_capacity_tons, cycle_time_minutes and working_days must be > 0")
    if hours_per_shift <= 0 or shifts_per_day <= 0:
        raise ValueError("hours_per_shift and shifts_per_day must be > 0")
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
    if num_cranes < 0 or duration_months < 0:
        raise ValueError("num_cranes and duration_months must be >= 0")
    if crane_capacity_tons <= 0:
        raise ValueError("crane_capacity_tons must be > 0")
    if remote_area_factor < 0:
        raise ValueError("remote_area_factor must be >= 0")
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
    if quantity_m3 < 0:
        raise ValueError("quantity_m3 must be >= 0")
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
    if quantity_kg <= 0:
        raise ValueError("quantity_kg must be > 0")
    qty_t = quantity_kg / 1000
    material = qty_t * material_price_sar_t * (1 + waste_pct)
    labour = qty_t * labour_mhr_t * labour_rate_sar_hr
    equipment = qty_t * crane_hr_t * crane_rate_sar_hr
    direct = material + labour + equipment
    with_markup = direct * (1 + indirect_pct) / (1 - markup_pct)
    sell_t = round(with_markup / qty_t, 0) if qty_t > 0 else 0
    return {
        "material_sar_t": round(material / qty_t, 0) if qty_t > 0 else 0,
        "labour_sar_t": round(labour / qty_t, 0) if qty_t > 0 else 0,
        "equipment_sar_t": round(equipment / qty_t, 0) if qty_t > 0 else 0,
        "selling_price_sar_t": sell_t,
        "total_project_value_sar": round(sell_t * qty_t, 0) if qty_t > 0 else 0,
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
    if area_m2 < 0:
        raise ValueError("area_m2 must be >= 0")
    if crane_output_m2_hr <= 0:
        raise ValueError("crane_output_m2_hr must be > 0")
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

    # Warehouse equipment (forklift, crane for laydown). Factor applied once in fixed.
    warehouse_equip = 35000

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
            "camp_sar": round((camp_setup + camp_per_person * num_personnel * duration_months) * remote_area_factor, 0),
            "transport_sar": round(transport_per_person * num_personnel * duration_months * remote_area_factor, 0),
            "safety_medical_sar": round((safety_setup + safety_medical_per_person * num_personnel * duration_months) * remote_area_factor, 0),
            "it_sar": round(it_costs * remote_area_factor, 0),
            "warehouse_equipment_sar": round(warehouse_equip * remote_area_factor, 0),
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
    if floor_area_m2 <= 0 or num_floors <= 0:
        raise ValueError("floor_area_m2 and num_floors must be > 0")
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
    if tendon_duct_diameter_mm <= 0:
        return {"error": "tendon_duct_diameter_mm must be > 0"}
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
    reference_temperature_c: float = 20.0,
    gain_a: float = 4.0,
    gain_b: float = 0.85,
) -> Dict[str, Any]:
    """Nurse-Saul maturity + ACI 209 Type I moist strength gain.

    MI = sum(max(0, T - T0) * dt)  (T0 = -10 °C by default).
    Equivalent age te (hours) = MI / (Tref - T0); te_days = te / 24.
    f(t)/f28 = te_days / (a + b*te_days)  with a=4, b=0.85 (ACI 209R).
    """
    if not temperature_history_c or not time_intervals_hours:
        return {
            "error": "temperature_history_c and time_intervals_hours are required and must be non-empty.",
        }
    if len(temperature_history_c) != len(time_intervals_hours):
        return {
            "error": "temperature_history_c and time_intervals_hours must have the same length.",
        }
    if any(float(dt) <= 0 for dt in time_intervals_hours):
        return {"error": "time_intervals_hours must all be > 0."}
    if strength_28d_n_mm2 <= 0:
        return {"error": "strength_28d_n_mm2 must be > 0."}
    te_denom = float(reference_temperature_c) - float(datum_temperature)
    if te_denom <= 0:
        return {"error": "reference_temperature_c must be greater than datum_temperature."}

    mi = sum(
        max(0.0, float(t) - float(datum_temperature)) * float(dt)
        for t, dt in zip(temperature_history_c, time_intervals_hours)
    )
    te_days = (mi / te_denom) / 24.0
    denom = gain_a + gain_b * te_days
    ratio = (te_days / denom) if denom > 0 else 0.0
    ratio = min(max(ratio, 0.0), 1.0)
    return {
        "maturity_index_c_hrs": round(mi, 0),
        "equivalent_age_days": round(te_days, 3),
        "predicted_strength_n_mm2": round(strength_28d_n_mm2 * ratio, 1),
        "percent_of_28d": round(ratio * 100, 1),
    }


# ═══════════════════════════════════════════════════════════════════════════
#  DETERMINISTIC DISPATCH — the agent's `construction_calc` tool routes here.
# ═══════════════════════════════════════════════════════════════════════════

import dataclasses as _dc
import inspect as _inspect
import json
import logging
import re

logger = logging.getLogger(__name__)


# Public functions in this module that are DISPATCH infrastructure, not
# calculators — excluded from the registry regardless of definition order.
# bind_calculation_params / describe_calculation_params /
# extract_calculation_params_from_text are the shared dispatcher helpers
# (#639 + Agent C / #636 / #652). Do not register them.
_NON_CALCULATORS = {
    "available_calculations",
    "run_calculation",
    "bind_calculation_params",
    "describe_calculation_params",
    "coerce_calc_params",
    "extract_calculation_params_from_text",
    "calculator_name_from_text",
}


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


# Stem collisions: first two tokens are a document/schedule lookup, not a calc.
_FORMULA_NAME_STEM_COLLISIONS = frozenset({
    "critical path",
})
# Optional dests that error unless the incoming key is the real name.
# "sand" / "dune sand" must not suffix-bind onto dune_sand_pct (live mix
# table SGs were scored tool_error).
_EXPLICIT_BIND_ONLY = frozenset({
    "dune_sand_pct",
})


def calculator_name_from_text(text: str) -> Optional[str]:
    """Unique registry name implied by ``text``, or None if absent/ambiguous.

    Full underscore / spaced names beat 2-token stems so
    ``concrete mix design sg`` is not tied with ``concrete_mix_slip_form``
    and ``cost buildup concrete`` is not tied with ``cost_buildup_rebar``.
    A unique 3-token tail (``well point spacing``) also counts — live
    dewatering asks omit the leading ``dewatering_``.
    """
    raw = text or ""
    if not raw.strip():
        return None
    try:
        from app.lib.construction_formulas_structural_rc import (
            looks_like_slab_thickness_min_ask,
        )
        if looks_like_slab_thickness_min_ask(raw):
            return "slab_thickness_min"
    except Exception:  # noqa: BLE001 — name lookup must not break dispatch
        logger.exception("slab_thickness_min name check failed")
    underscored = raw.lower().replace("-", "_")
    spaced = raw.lower().replace("-", " ").replace("_", " ")
    full: List[str] = []
    triples: List[str] = []
    pairs: List[str] = []
    stems: List[str] = []
    for name in CALCULATORS:
        if len(name) < 6:
            continue
        tokens = [tok for tok in name.lower().split("_") if tok]
        spaced_name = name.replace("_", " ")
        if name.lower() in underscored or (
            len(tokens) >= 3 and spaced_name in spaced
        ):
            full.append(name)
            continue
        if len(tokens) >= 3:
            for i in range(len(tokens) - 2):
                chunk = " ".join(tokens[i:i + 3])
                if chunk in spaced:
                    triples.append(name)
                    break
            for i in range(len(tokens) - 1):
                chunk = " ".join(tokens[i:i + 2])
                if len(chunk) >= 10 and chunk in spaced:
                    pairs.append(name)
            stem = " ".join(tokens[:2])
            if (
                len(stem) >= 8
                and stem in spaced
                and stem not in _FORMULA_NAME_STEM_COLLISIONS
            ):
                stems.append(name)
    for group in (full, triples, pairs, stems):
        uniq = list(dict.fromkeys(group))
        if len(uniq) == 1:
            return uniq[0]
        if len(uniq) > 1:
            return None
    return None


# PMI (BCWS/BCWP/ACWP/BAC) and long PE-sheet names → calculate_evm kwargs.
# ``run_calculation`` keeps only exact signature names, so uppercase / long
# aliases were dropped and the tool reported missing PV/EV/AC.
_EVM_CALC_ALIASES: Dict[str, str] = {
    "pv": "pv",
    "planned_value": "pv",
    "bcws": "bcws",
    "ev": "ev",
    "earned_value": "ev",
    "bcwp": "bcwp",
    "ac": "ac",
    "actual_cost": "ac",
    "acwp": "acwp",
    "bac": "bac",
    "budget_at_completion": "bac",
}


def _alias_calculate_evm_params(params: Dict[str, Any]) -> Dict[str, Any]:
    """Bind case-insensitive PMI / PE names onto ``calculate_evm`` kwargs.

    Canonical keys already present win; aliases only fill holes so mixed
    ``pv`` + ``BCWP`` + ``acwp`` still resolve.
    """
    out = dict(params)
    for raw_key, val in params.items():
        if val is None or val == "":
            continue
        dest = _EVM_CALC_ALIASES.get(str(raw_key).strip().lower())
        if dest is None:
            continue
        if dest not in out or out[dest] in (None, ""):
            out[dest] = val
    return out


# Units for empty / incomplete construction_calc kwargs. The model often
# calls with {} or a half-set; the error must name every required input
# with its unit so the retry can bind (same class as empty-args
# payment_certificate / B-calc FAILs).
_PARAM_UNITS: Dict[str, str] = {
    "bar_diameter_mm": "mm",
    "total_length_m": "m",
    "total_weight_kg": "kg",
    "total_mass_kg": "kg",
    "total_mass_t": "t",
    "quantity": "qty",
    "quantity_executed": "qty",
    "quantity_kg": "kg",
    "daily_production": "qty/day",
    "productivity": "qty/h",
    "productivity_rate": "qty/day",
    "crew_cost_per_day": "currency/day",
    "day_rate": "currency/day",
    "gang_cost_per_day": "currency/day",
    "man_hours": "h",
    "manpower": "persons",
    "working_hours": "h",
    "remaining_manhours": "h",
    "available_hours": "h",
    "remaining_qty": "qty",
    "remaining_days": "days",
    "material_price_sar_t": "SAR/t",
}

_CALC_REQUIRED_HELP: Dict[str, str] = {
    "rebar_weight": (
        "rebar_weight needs bar_diameter_mm (mm) and either "
        "total_length_m (m) or total_weight_kg (kg) with mode=weight_to_length."
    ),
    "productivity_manpower_duration": (
        "productivity_manpower_duration needs paired inputs (no invented "
        "rates): quantity_executed (qty) + man_hours (h); "
        "quantity (qty) + productivity (qty/h); "
        "quantity (qty) + daily_production (qty/day); "
        "quantity (qty) + productivity_rate (qty/gang-day) + "
        "crew_cost_per_day (currency/day); "
        "manpower (persons) + working_hours (h); "
        "remaining_manhours (h) + available_hours (h); "
        "or remaining_qty (qty) + remaining_days (days)."
    ),
}


def _param_with_unit(name: str) -> str:
    unit = _PARAM_UNITS.get(name)
    return f"{name} ({unit})" if unit else name


def required_params_error(name: str, fn: Any) -> str:
    """Error text that names every required parameter with its unit."""
    canned = _CALC_REQUIRED_HELP.get(str(name or "").strip())
    if canned:
        return canned
    try:
        sig = _inspect.signature(fn)
    except (TypeError, ValueError):
        return f"{name} needs its documented inputs; none were usable."
    required = [
        k for k, p in sig.parameters.items()
        if p.default is _inspect.Parameter.empty
        and p.kind in (p.POSITIONAL_OR_KEYWORD, p.KEYWORD_ONLY)
        and k != "self"
    ]
    if not required:
        return f"{name} needs its documented inputs; none were usable."
    return f"{name} needs: " + ", ".join(_param_with_unit(k) for k in required) + "."


def _result_is_failure(result: Dict[str, Any]) -> bool:
    """Did a calculator report failure by RETURNING rather than raising?

    Keyed on a STRING "error" deliberately: a NUMERIC field named "error" is a
    tolerance or deviation -- data, not a failure signal -- and must not be
    mistaken for one. (Swept 2026-08-15 across all registered calculators: none
    currently returns a numeric "error".)
    """
    return isinstance(result.get("error"), str)


# Keys the model / container / tool envelope add beside real calculator kwargs.
# Flatten unwraps ``params`` / ``input`` then drops these so they never
# reach fn(**kwargs). ``text`` / ``formula`` stay available for the E4 /
# F–W resolvers that run *before* bind, and are stripped at bind time.
_BIND_JUNK_KEYS = frozenset({
    "action", "calculation", "name", "calculator", "params",
    "block", "unit", "formula", "text", "ok", "status",
    "input", "project_id", "conversation_id", "user_id",
    "message", "history", "messages", "chat",
    "kwargs", "arguments", "variables", "values",
})
_FLATTEN_NEST_KEYS = ("params", "input", "kwargs", "arguments", "variables", "values")
_E4_PASSTHROUGH_KEYS = frozenset({"text", "formula"})

# Longest-first unit suffixes stripped when matching volume ↔ volume_m3.
_BIND_UNIT_SUFFIXES: Tuple[Tuple[str, str], ...] = (
    ("_kgco2e_m3", "kgCO2e/m3"),
    ("_n_mm2", "N/mm2"),
    ("_kn_m3", "kN/m3"),
    ("_kn_m2", "kN/m2"),
    ("_kn_m", "kN.m"),
    ("_mm2", "mm2"),
    ("_m2", "m2"),
    ("_m3", "m3"),
    ("_mm", "mm"),
    ("_mpa", "MPa"),
    ("_kpa", "kPa"),
    ("_kn", "kN"),
    ("_kg", "kg"),
    ("_percent", "%"),
    ("_pct", "%"),
    ("_days", "d"),
    ("_day", "d"),
    ("_deg", "deg"),
    ("_m", "m"),
    ("_s", "s"),
    ("_t", "t"),
)

# Incoming name (normalized) → candidate signature names. Only a candidate
# that is actually on *this* calculator is used, and only when unique.
# Includes the #636 PMI / PE aliases so calculate_evm binds without a
# special-case table of its own (Agent C shares this path), plus #639
# excavation/claim/bolt aliases and #652 standing-exit aliases.
_BIND_SEMANTIC_ALIASES: Dict[str, Tuple[str, ...]] = {
    "pv": ("pv", "bcws"),
    "planned_value": ("pv", "bcws"),
    "ev": ("ev", "bcwp"),
    "earned_value": ("ev", "bcwp"),
    "ac": ("ac", "acwp"),
    "actual_cost": ("ac", "acwp"),
    "budget_at_completion": ("bac",),
    "volume": ("volume_m3", "quantity_m3"),
    "vol": ("volume_m3", "quantity_m3"),
    "qty": ("quantity_m3", "quantity_kg", "quantity", "volume_m3"),
    "quantity": ("quantity_m3", "quantity_kg", "quantity"),
    "quantity_t": ("quantity_kg",),
    "qty_t": ("quantity_kg",),
    "tonnes": ("quantity_kg",),
    "tons": ("quantity_kg",),
    "tonne": ("quantity_kg",),
    "ton": ("quantity_kg",),
    "mass": ("quantity_kg", "total_mass_kg"),
    "rebar_kg": ("quantity_kg",),
    "material_price": ("material_price_sar_t",),
    "steel_price": ("material_price_sar_t",),
    "rebar_price": ("material_price_sar_t",),
    "unit_rate": ("material_price_sar_t",),
    "waste": ("waste_pct",),
    "waste_percent": ("waste_pct",),
    "bidders": ("tenderers",),
    "bids": ("tenderers",),
    "suppliers": ("tenderers",),
    "submissions": ("tenderers",),
    "tenderer": ("tenderers",),
    "diameter": ("column_diameter_mm", "diameter_mm", "bolt_diameter_mm", "diameter_m"),
    "dia": ("column_diameter_mm", "diameter_mm", "bolt_diameter_mm", "diameter_m"),
    "excavation": ("excavation_bank_m3",),
    "excavation_bank": ("excavation_bank_m3",),
    "bank": ("excavation_bank_m3",),
    "bank_volume": ("excavation_bank_m3",),
    "structure": ("structure_volume_m3",),
    "structure_volume": ("structure_volume_m3",),
    "bolt_area": ("bolt_area_mm2",),
    "ab": ("bolt_area_mm2",),
    "ab_mm2": ("bolt_area_mm2",),
    "area": ("bolt_area_mm2", "area", "floor_area_m2", "formwork_area_m2", "area_m2"),
    "fnv": ("shear_strength_mpa",),
    "fub": ("shear_strength_mpa",),
    "gross": ("gross_valuation",),
    "valuation": ("gross_valuation",),
    "gross_value": ("gross_valuation",),
    "gross_amount": ("gross_valuation",),
    "certified_gross": ("gross_valuation",),
    "ipc": ("gross_valuation",),
    "amount": ("gross_valuation", "total_cost", "claimed_amount"),
    "cost": ("total_cost",),
    "price": ("material_price_sar_t", "total_cost"),
    "total": ("total_cost",),
    "area_m2": ("area", "floor_area_m2", "area_m2"),
    "gfa": ("floor_area_m2",),
    "floor_area": ("floor_area_m2",),
    "claimed": ("claimed_amount",),
    "claim": ("claimed_amount",),
    "certified": ("certified_amount",),
    "cert": ("certified_amount",),
    "retention": ("retention_percent", "retention_rate"),
    "field": ("field_dry_density",),
    "fdd": ("field_dry_density",),
    "field_density": ("field_dry_density",),
    "mdd": ("max_dry_density",),
    "lab_density": ("max_dry_density",),
    "max_density": ("max_dry_density",),
    "axial": ("axial_load_kn",),
    "axial_load": ("axial_load_kn",),
    "load": ("axial_load_kn",),
    "n_ed": ("axial_load_kn",),
    "pu": ("axial_load_kn",),
    "column_dia": ("column_diameter_mm",),
    "col_dia": ("column_diameter_mm",),
    "w": ("udl_w_kn_m", "width_m", "seismic_weight_kn", "formwork_width_m"),
    "udl": ("udl_w_kn_m",),
    "udl_w": ("udl_w_kn_m",),
    "uniform_load": ("udl_w_kn_m",),
    "span": ("span_m",),
    "l": ("span_m", "length_m", "panel_length"),
    "as": ("steel_area_mm2",),
    "ast": ("steel_area_mm2",),
    "ag": ("gross_area_mm2",),
    "ae": ("net_area_mm2",),
    "anet": ("net_area_mm2",),
    "an": ("net_area_mm2",),
    "fy": ("fy_mpa",),
    "fu": ("fu_mpa",),
    "fc": ("fc_mpa", "fck_n_mm2"),
    "fck": ("fck_n_mm2", "fc_mpa"),
    "fm": ("masonry_strength_mpa",),
    "b": ("width_m", "width_mm"),
    "breadth": ("width_m",),
    "bw": ("width_mm",),
    "d": ("depth_m", "excavation_depth", "eff_depth_mm", "bar_diameter_mm"),
    "l0": ("base_live_load_kn_m2",),
    "at": ("tributary_area_m2", "area_m2"),
    "kll": ("kll",),
    "ll": ("live_load_kn_m2",),
    "live_load": ("live_load_kn_m2",),
    "slab": ("slab_thickness_m",),
    "slab_thickness": ("slab_thickness_m",),
    "sds": ("sds",),
    "ie": ("ie",),
    "h": ("height_m", "height_mm", "formwork_height_m"),
    "height": ("height_m", "depth_m", "height_mm", "formwork_height_m"),
    "height_m": ("height_m", "depth_m"),
    "width": ("width_mm", "formwork_width_m", "width_m"),
    "v": ("wind_velocity_m_s", "wind_speed_m_s"),
    "p": ("central_point_load_kn", "axial_load_kn", "point_load_kn"),
    "rate": ("rate_percent", "material_price_sar_t"),
    "delay_rate": ("rate_percent",),
    "aca": ("contract_amount",),
    "accepted_contract_amount": ("contract_amount",),
    "contract_value": ("contract_amount",),
    "hw": ("water_depth",),
    "h_w": ("water_depth",),
    "water_table": ("water_depth",),
    "water_head": ("water_depth",),
    "groundwater": ("water_depth",),
    "gwl": ("water_depth",),
    "gw_depth": ("water_depth",),
    "uplift_head": ("water_depth",),
    "raft": ("raft_thickness",),
    "traft": ("raft_thickness",),
    "t_raft": ("raft_thickness",),
    "raft_t": ("raft_thickness",),
    "raft_thk": ("raft_thickness",),
    "raft_depth": ("raft_thickness",),
    "floors": ("floor_count", "num_floors"),
    "n_floors": ("floor_count", "num_floors"),
    "nfloors": ("floor_count", "num_floors"),
    "num_floors": ("floor_count", "num_floors"),
    "storeys": ("floor_count", "num_floors"),
    "stories": ("floor_count", "num_floors"),
    "storey": ("floor_count", "num_floors"),
    "story": ("floor_count", "num_floors"),
    "levels": ("floor_count", "num_floors"),
    "n_storeys": ("floor_count", "num_floors"),
    "no_of_floors": ("floor_count", "num_floors"),
    "number_of_floors": ("floor_count", "num_floors"),
    "t": ("time_days", "thickness_m", "wall_thickness", "raft_thickness", "thickness_mm", "slab_thickness_m"),
    "time": ("time_days",),
    "days": ("time_days",),
    "age": ("time_days",),
    "age_days": ("time_days",),
    "esh_ult": ("ultimate_shrinkage_microstrain",),
    "ultimate_shrinkage": ("ultimate_shrinkage_microstrain",),
    "wc": ("w_c_ratio",),
    "w_c": ("w_c_ratio",),
    "wc_ratio": ("w_c_ratio",),
    "water_cement": ("w_c_ratio",),
    "water_cement_ratio": ("w_c_ratio",),
    "wcr": ("w_c_ratio",),
    "temps": ("temperature_history_c",),
    "temperatures": ("temperature_history_c",),
    "temperature": ("temperature_history_c",),
    "temperature_history": ("temperature_history_c",),
    "hours": ("time_intervals_hours",),
    "intervals": ("time_intervals_hours",),
    "time_intervals": ("time_intervals_hours",),
    "dt": ("time_intervals_hours",),
    "length": ("length_m", "panel_length", "total_length_m"),
    "thickness": ("thickness_m", "wall_thickness", "raft_thickness"),
    "depth": ("depth_m", "excavation_depth"),
    "panel_l": ("panel_length",),
    "wall_t": ("wall_thickness",),
    "excavation_d": ("excavation_depth",),
    "bulking": ("bulking_factor",),
    "swell": ("bulking_factor",),
    "bulk": ("bulking_factor",),
    "cut": ("cut_volume_m3",),
    "cut_vol": ("cut_volume_m3",),
    "cut_volume": ("cut_volume_m3",),
    "fill": ("fill_volume_m3",),
    "fill_vol": ("fill_volume_m3",),
    "fill_volume": ("fill_volume_m3",),
    "permeability": ("soil_permeability_m_s",),
    "k": ("soil_permeability_m_s",),
    "k_m_s": ("soil_permeability_m_s",),
    "soil_k": ("soil_permeability_m_s",),
    "drawdown": ("required_drawdown_m",),
    "required_drawdown": ("required_drawdown_m",),
}

# Signature defaults that must NOT silently succeed (live wrong_number /
# tool_error). Any one name in a group satisfies the group.
_REQUIRED_GROUPS: Dict[str, Tuple[Tuple[str, ...], ...]] = {
    "calculate_evm": (
        ("pv", "bcws"),
        ("ev", "bcwp"),
        ("ac", "acwp"),
    ),
    "delay_damages_daily": (
        ("rate_percent",),
        ("contract_amount",),
    ),
    "beam_shear_simple": (
        ("udl_w_kn_m", "central_point_load_kn"),
        ("span_m",),
    ),
}

_PARAM_UNIT_OVERRIDE: Dict[str, str] = {
    "pv": "currency",
    "ev": "currency",
    "ac": "currency",
    "bcws": "currency",
    "bcwp": "currency",
    "acwp": "currency",
    "bac": "currency",
    "rate_percent": "%",
    "contract_amount": "currency",
    "water_depth": "m",
    "raft_thickness": "m",
    "floor_count": "floors",
    "floor_thickness": "m",
    "time_days": "d",
    "time_constant_days": "d",
    "ultimate_shrinkage_microstrain": "µε",
    "udl_w_kn_m": "kN/m",
    "central_point_load_kn": "kN",
    "quantity_m3": "m3",
    "w_c_ratio": "ratio",
    "floor_area_m2": "m2",
    "panel_length": "m",
    "wall_thickness": "m",
    "excavation_depth": "m",
    "total_cost": "currency",
    "area": "m2",
    "gross_valuation": "currency",
    "temperature_history_c": "°C",
    "time_intervals_hours": "h",
    "axial_load_kn": "kN",
    "column_diameter_mm": "mm",
}


def _snake_key(raw: Any) -> str:
    s = str(raw or "").strip().replace("-", "_").replace("/", "_")
    out: List[str] = []
    for i, ch in enumerate(s):
        if ch.isupper() and i and (s[i - 1].islower() or s[i - 1].isdigit()):
            out.append("_")
        out.append(ch.lower())
    return "".join(out).strip("_")


def _param_stem(name: str) -> str:
    n = _snake_key(name)
    changed = True
    while changed and n:
        changed = False
        for suf, _unit in _BIND_UNIT_SUFFIXES:
            if n.endswith(suf) and len(n) > len(suf):
                n = n[: -len(suf)]
                changed = True
                break
    return n.replace("_", "")


def _unit_from_name(name: str) -> str:
    if name in _PARAM_UNIT_OVERRIDE:
        return _PARAM_UNIT_OVERRIDE[name]
    n = _snake_key(name)
    for suf, unit in _BIND_UNIT_SUFFIXES:
        if n.endswith(suf):
            return unit
    return ""


def _ann_label(annotation: Any) -> str:
    if annotation is _inspect.Parameter.empty:
        return ""
    return getattr(annotation, "__name__", None) or str(annotation).replace("typing.", "")


def _is_junk_key(key: Any) -> bool:
    return _snake_key(key) in {_snake_key(j) for j in _BIND_JUNK_KEYS}


_POSITIONAL_KEY = "_positional"
_KV_ASSIGN_RE = re.compile(
    r"([A-Za-z_][\w]*(?:/[A-Za-z_][\w]*)*)\s*[:=]\s*"
    r"([+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?|[+-]?\d{1,3}(?:,\d{3})+(?:\.\d+)?|"
    r"\"[^\"]*\"|'[^']*')",
)
_KV_JSON_START_RE = re.compile(
    r"([A-Za-z_][\w]*(?:/[A-Za-z_][\w]*)*)\s*[:=]\s*([\[{])",
)
_TONNE_INCOMING = frozenset({
    "quantity_t", "qty_t", "tonnes", "tons", "tonne", "ton",
})
_FRACTION_PCT_KEYS = frozenset({
    "waste_pct", "indirect_pct", "markup_pct",
})


def _value_has_number(val: Any) -> bool:
    if isinstance(val, (int, float)) and not isinstance(val, bool):
        return True
    if isinstance(val, (list, tuple)):
        return any(_value_has_number(item) for item in val)
    if isinstance(val, dict):
        return any(_value_has_number(item) for item in val.values())
    return False


def _parse_kv_assignments(text: str) -> Dict[str, Any]:
    """Parse ``udl_w_kn_m=20, span_m=6`` / ``temps=[20, 22, 25]``.

    Live standing-exit probe prints and often *sends* params as assignment
    strings, not JSON objects. Only keep a parse that yielded a number so
    ``status: error`` prose is not treated as kwargs.
    """
    if not text or ("=" not in text and ":" not in text):
        return {}
    out: Dict[str, Any] = {}
    consumed: List[Tuple[int, int]] = []

    def _overlaps(span: Tuple[int, int]) -> bool:
        return any(span[0] < c[1] and c[0] < span[1] for c in consumed)

    decoder = json.JSONDecoder()
    for match in _KV_JSON_START_RE.finditer(text):
        if _overlaps(match.span()):
            continue
        try:
            obj, end = decoder.raw_decode(text, match.end() - 1)
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        out[match.group(1)] = obj
        consumed.append((match.start(), end))

    for match in _KV_ASSIGN_RE.finditer(text):
        if _overlaps(match.span()):
            continue
        raw = match.group(2).strip().strip("\"'")
        number = raw.replace(",", "").strip()
        unit = re.match(r"^([+-]?\d+(?:\.\d+)?)(?:\s*[A-Za-zµμ/%²³³°]+)?$", number)
        if unit:
            token = unit.group(1)
            out[match.group(1)] = float(token) if "." in token else int(token)
        else:
            out[match.group(1)] = raw
        consumed.append(match.span())
    if not any(_value_has_number(val) for val in out.values()):
        return {}
    return out


def coerce_calc_params(raw: Any) -> Dict[str, Any]:
    """Accept a dict, JSON object/array, or ``k=v, k=v`` assignment string.

    A JSON array becomes ``{_positional: [...]}`` so run_calculation can
    zip it onto the signature (beam_shear [20, 6], tenderer list, maturity
    pair-of-lists). Never invent keys. Empty / undecodable is ``{}``.
    """
    if raw is None or raw == "":
        return {}
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, (list, tuple)):
        return {_POSITIONAL_KEY: list(raw)}
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return {}
        if text[0] in "{[":
            try:
                obj = json.loads(text)
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                logger.debug("coerce_calc_params: undecodable params string (%s)", exc)
                obj = None
            if isinstance(obj, dict):
                return dict(obj)
            if isinstance(obj, list):
                return {_POSITIONAL_KEY: obj}
        parsed = _parse_kv_assignments(text)
        if parsed:
            return parsed
        return {}
    return {}


# Length units the binder converts between when the caller's key and the
# calculator's parameter name carry the same stem but different units. No
# calculator takes a _cm parameter, so cm is not listed: an unmapped unit
# fails the bind with "missing required span_mm (mm)", which is the right
# outcome -- a named error the caller can act on.
_LENGTH_IN_MM = {"mm": 1.0, "m": 1000.0}


def _length_unit_factor(incoming: str, dest: str) -> Optional[float]:
    """Factor to convert ``incoming``'s unit to ``dest``'s, or None.

    Only when the two keys are the SAME quantity in different units --
    ``span_m`` onto ``span_mm`` -- so nothing is converted across quantities.
    """
    if "_" not in incoming or "_" not in dest:
        return None
    inc_stem, inc_unit = incoming.rsplit("_", 1)
    dest_stem, dest_unit = dest.rsplit("_", 1)
    if inc_stem != dest_stem or inc_unit == dest_unit:
        return None
    if inc_unit not in _LENGTH_IN_MM or dest_unit not in _LENGTH_IN_MM:
        return None
    return _LENGTH_IN_MM[inc_unit] / _LENGTH_IN_MM[dest_unit]


def _scale_bound_value(incoming: str, dest: str, val: Any) -> Any:
    """quantity_t / tonnes → quantity_kg, and span_m → span_mm. Never invents a
    value that was absent.

    The length case is live SET4 T20: the model called slab_thickness_min with
    ``span_m: 4.8`` against a ``span_mm`` parameter. The value bound unchanged,
    so 4.8 metres was read as 4.8 millimetres and the calculator returned
    ``min_thickness_mm: 0.2`` with status success. The answer then reported
    "200 mm" -- 0.2 read back as metres -- while its own working said
    "L/20 = 4800/20". A silently wrong unit is worse than a rejected call.
    """
    inc = _snake_key(incoming)
    if dest == "quantity_kg" and (
        inc in _TONNE_INCOMING or inc.endswith("_t")
    ):
        try:
            return float(val) * 1000.0
        except (TypeError, ValueError):
            logger.debug("quantity_t token %r is not numeric", val)
            return val
    factor = _length_unit_factor(inc, _snake_key(dest))
    if factor is not None:
        try:
            scaled = float(val) * factor
        except (TypeError, ValueError):
            logger.debug("length token %r is not numeric", val)
            return val
        logger.info("bind: converted %s=%s to %s=%s", inc, val, dest, scaled)
        return scaled
    return val


def _bind_sequence_params(
    fn: Any, values: List[Any], name: Optional[str] = None,
) -> Dict[str, Any]:
    """Zip a JSON/Python array onto this calculator's signature.

    List-of-dicts → unique list param (evaluate_tender tenderers).
    List-of-lists → list-typed params in signature order (maturity).
    Scalars → required-then-optional positional names (beam_shear [20, 6]).
    """
    seq = list(values or [])
    if not seq or fn is None:
        return {}
    try:
        sig = _inspect.signature(fn)
    except (TypeError, ValueError):
        logger.debug("positional bind: calculator has no inspectable signature")
        return {}
    dests = [
        key for key, param in sig.parameters.items()
        if param.kind in (param.POSITIONAL_OR_KEYWORD, param.KEYWORD_ONLY)
        and not key.startswith("_")
    ]
    list_dests = [
        key for key in dests
        if _annotation_wants_list(sig.parameters[key].annotation)
    ]
    if dests and all(isinstance(item, dict) for item in seq):
        calc = str(name or "").strip().lower()
        if calc == "evaluate_tender" or "tenderers" in list_dests:
            return {"tenderers": seq}
        if len(list_dests) == 1:
            return {list_dests[0]: seq}
        return {}
    if (
        list_dests
        and len(seq) == len(list_dests)
        and all(isinstance(item, (list, tuple)) for item in seq)
    ):
        return {key: list(item) for key, item in zip(list_dests, seq)}
    required = [
        key for key in dests
        if sig.parameters[key].default is sig.parameters[key].empty
    ]
    order = required + [key for key in dests if key not in required]
    return {key: val for key, val in zip(order, seq)}


def _pop_positional(params: Dict[str, Any]) -> Optional[List[Any]]:
    if not isinstance(params, dict):
        return None
    raw = params.pop(_POSITIONAL_KEY, None)
    if isinstance(raw, (list, tuple)):
        return list(raw)
    return None


def _flatten_calc_kwargs(params: Dict[str, Any]) -> Dict[str, Any]:
    """Merge nested ``params`` / ``input`` into top-level calculator kwargs.

    Live Phase-2 / #636 / DIR7 / #652: the model often puts calculator kwargs
    next to ``calculation`` *or* nests them under ``params`` or ``input``.
    Envelope keys (text, formula, input, project_id, …) are never copied
    through as calculator kwargs. ``text`` / ``formula`` are the E4
    exception — they survive flatten for the resolvers, then bind drops
    them. Nested keys lose to an explicit top-level of the same name.
    """
    if not isinstance(params, dict):
        return {}
    junk = {_snake_key(j) for j in _BIND_JUNK_KEYS}
    e4 = {_snake_key(j) for j in _E4_PASSTHROUGH_KEYS}
    out: Dict[str, Any] = {}
    for nest_key in _FLATTEN_NEST_KEYS:
        nested = coerce_calc_params(params.get(nest_key))
        if not nested:
            continue
        for key, val in nested.items():
            if val is None or val == "":
                continue
            if _snake_key(key) in junk:
                continue
            out[key] = val
    for key, val in params.items():
        if val is None or val == "":
            continue
        nk = _snake_key(key)
        if nk in junk and nk not in e4:
            continue
        out[key] = val
    return out


def describe_calculation_params(
    fn: Any,
    name: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Signature-derived expected params with units (name suffix / override)."""
    sig = _inspect.signature(fn)
    required_names: set[str] = set()
    if name:
        for group in _REQUIRED_GROUPS.get(str(name).strip().lower(), ()):
            required_names.update(group)
    rows: List[Dict[str, Any]] = []
    for key, param in sig.parameters.items():
        if param.kind not in (param.POSITIONAL_OR_KEYWORD, param.KEYWORD_ONLY):
            continue
        if key.startswith("_"):
            continue
        required = param.default is param.empty or key in required_names
        unit = _unit_from_name(key)
        row: Dict[str, Any] = {
            "name": key,
            "required": required,
            "unit": unit,
            "annotation": _ann_label(param.annotation),
        }
        if param.default is not param.empty:
            row["default"] = param.default
        rows.append(row)
    return rows


def _unique_semantic_dest(incoming: str, accepted: Dict[str, str]) -> Optional[str]:
    hits = []
    for cand in _BIND_SEMANTIC_ALIASES.get(incoming, ()):
        dest = accepted.get(_snake_key(cand))
        if dest and dest not in hits:
            hits.append(dest)
    if len(hits) == 1:
        return hits[0]
    return None


def _unique_stem_dest(
    incoming: str,
    accepted: Dict[str, str],
    required: Optional[set] = None,
) -> Optional[str]:
    """Bind volume → volume_m3 / diameter → column_diameter_mm / water_depth_m → water_depth when unique.

    Suffix matches (sand → dune_sand_pct, cement → cement_sg) only land on
    *required* dests. Optional dests need an explicit name or semantic alias
    so a mix-design table SG cannot raise dune_sand_pct.
    """
    stem = _param_stem(incoming)
    if len(stem) < 3:
        return None
    exact = [
        dest for dest in accepted.values()
        if _param_stem(dest) == stem and dest not in _EXPLICIT_BIND_ONLY
    ]
    if len(exact) == 1:
        return exact[0]
    if exact:
        return None
    suffix = [
        dest for dest in accepted.values()
        if dest not in _EXPLICIT_BIND_ONLY
        and _param_stem(dest).endswith(stem)
        and len(_param_stem(dest)) > len(stem)
    ]
    if required is not None:
        suffix = [dest for dest in suffix if dest in required]
    if len(suffix) == 1:
        return suffix[0]
    return None


def bind_calculation_params(fn: Any, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Map incoming kwargs onto ``fn``'s signature names.

    Shared with Agent C (#636 flattened the tool path + EVM aliases). This
    generalises that bind: case-insensitive keys, unit-suffix stems, and a
    small synonym table. Canonical names already present win.
    Shared with Agent C / #636 / #639 / #652.
    """
    raw = _flatten_calc_kwargs(params or {})
    sig = _inspect.signature(fn)
    accepted_list = [
        key for key, param in sig.parameters.items()
        if param.kind in (param.POSITIONAL_OR_KEYWORD, param.KEYWORD_ONLY)
    ]
    accepted_norm = {_snake_key(k): k for k in accepted_list}
    has_var_kw = any(p.kind == p.VAR_KEYWORD for p in sig.parameters.values())
    required = {
        key for key, param in sig.parameters.items()
        if param.default is param.empty
        and param.kind in (param.POSITIONAL_OR_KEYWORD, param.KEYWORD_ONLY)
    }

    bound: Dict[str, Any] = {}
    leftovers: Dict[str, Any] = {}
    for key, val in raw.items():
        if val is None or val == "":
            continue
        if _is_junk_key(key):
            continue
        dest = accepted_norm.get(_snake_key(key))
        if dest in _EXPLICIT_BIND_ONLY and _snake_key(key) != _snake_key(dest):
            dest = None
        if dest is None:
            dest = _unique_semantic_dest(_snake_key(key), accepted_norm)
        if dest is None:
            dest = _unique_stem_dest(key, accepted_norm, required=required)
        if dest is None:
            leftovers[key] = val
            continue
        if isinstance(val, (list, tuple)) and len(val) == 1:
            val = val[0]
        scaled = _scale_bound_value(str(key), dest, val)
        if dest not in bound or bound[dest] in (None, ""):
            bound[dest] = scaled

    if has_var_kw:
        for key, val in leftovers.items():
            bound.setdefault(key, val)
    return bound


_ASK_NUM = r"(\d[\d,]*(?:\.\d+)?)"
_NUM_UNIT_VALUE_RE = re.compile(
    r"^[+\-]?\s*([\d,]+(?:\.\d+)?)\s*[A-Za-zµμ/%²³³°]+"
)
_PLAIN_NUM_RE = re.compile(r"^[+\-]?\s*[\d,]+(?:\.\d+)?\s*$")
_LWD_WORDS_RE = re.compile(
    rf"{_ASK_NUM}\s*m?\s*long\b.*?{_ASK_NUM}\s*m?\s*wide\b.*?"
    rf"{_ASK_NUM}\s*m?\s*(?:deep|thick)",
    re.IGNORECASE | re.DOTALL,
)
_FORMULA_TRIPLE_RE = re.compile(
    rf"{_ASK_NUM}\s*[*×x]\s*{_ASK_NUM}\s*[*×x]\s*{_ASK_NUM}",
    re.IGNORECASE,
)


def _ask_float(raw: str) -> float:
    return float(str(raw).replace(",", ""))


def _ask_present(params: Dict[str, Any], *keys: str) -> bool:
    return any(params.get(k) not in (None, "") for k in keys)


def _ask_blob(params: Dict[str, Any]) -> str:
    return " ".join(
        str(x) for x in (
            params.get("text"),
            params.get("formula"),
            params.get("message"),
            params.get("query"),
        ) if x
    )


def _fill_lwd_from_text(text: str, out: Dict[str, Any], depth_key: str = "depth_m") -> None:
    """Bank / trench L×W×D from the ask. Does not invent numbers."""
    if _ask_present(out, "length_m") and _ask_present(out, "width_m") and _ask_present(
        out, depth_key, "height_m", "thickness_m",
    ):
        return
    dims = None
    try:
        from app.lib.construction_formulas_quantities import parse_lwt_metres
        dims = parse_lwt_metres(text)
    except (TypeError, ValueError, ImportError):
        dims = None
    if dims:
        out.setdefault("length_m", dims[0])
        out.setdefault("width_m", dims[1])
        out.setdefault(depth_key, dims[2])
        return
    match = _LWD_WORDS_RE.search(text or "")
    if match:
        out.setdefault("length_m", _ask_float(match.group(1)))
        out.setdefault("width_m", _ask_float(match.group(2)))
        out.setdefault(depth_key, _ask_float(match.group(3)))
        return
    match = _FORMULA_TRIPLE_RE.search(text or "")
    if match:
        out.setdefault("length_m", _ask_float(match.group(1)))
        out.setdefault("width_m", _ask_float(match.group(2)))
        out.setdefault(depth_key, _ask_float(match.group(3)))


def _extract_slab_thickness_from_ask(text: str, out: Dict[str, Any]) -> None:
    """Fill slab_thickness_min from 'spanning 4.8 m' / one-end continuous.

    The binder's span label does not see 'spanning'. Holes only — an
    explicit span_mm on the call wins.
    """
    try:
        from app.lib.construction_formulas_structural_rc import (
            slab_thickness_params_from_ask,
        )
    except Exception:  # noqa: BLE001 — extract must not break other calculators
        logger.exception("slab thickness ask extract unavailable")
        return
    found = slab_thickness_params_from_ask(text or "")
    for key, val in found.items():
        if out.get(key) in (None, ""):
            out[key] = val


def _extract_beam_shear_from_ask(text: str, out: Dict[str, Any]) -> None:
    if not _ask_present(out, "udl_w_kn_m"):
        match = re.search(
            rf"(?:udl(?:_w)?|w)\s*[=:]?\s*{_ASK_NUM}\s*(?:kN\s*/\s*m|kn/m)?",
            text, re.IGNORECASE,
        )
        if match is None:
            match = re.search(rf"{_ASK_NUM}\s*kN\s*/\s*m", text, re.IGNORECASE)
        if match:
            out["udl_w_kn_m"] = _ask_float(match.group(1))
    if not _ask_present(out, "span_m"):
        match = re.search(
            rf"(?:span|l)\s*[=:]?\s*{_ASK_NUM}\s*m?\b",
            text, re.IGNORECASE,
        )
        if match:
            out["span_m"] = _ask_float(match.group(1))


def _extract_dewatering_from_ask(text: str, out: Dict[str, Any]) -> None:
    if not _ask_present(out, "water_depth"):
        match = re.search(
            rf"{_ASK_NUM}\s*m(?:etre)?s?\s+(?:of\s+)?(?:water|head|groundwater|uplift)",
            text, re.IGNORECASE,
        )
        if match is None:
            match = re.search(
                rf"(?:water|head|hw|groundwater)(?:\s+depth)?(?:\s+of)?\s*[:=]?\s*{_ASK_NUM}",
                text, re.IGNORECASE,
            )
        if match:
            out["water_depth"] = _ask_float(match.group(1))
    if not _ask_present(out, "raft_thickness"):
        match = re.search(
            rf"{_ASK_NUM}\s*m(?:etre)?s?\s+(?:thick\s+)?raft",
            text, re.IGNORECASE,
        )
        if match is None:
            match = re.search(
                rf"raft(?:\s+thickness)?(?:\s+of)?\s*[:=]?\s*{_ASK_NUM}",
                text, re.IGNORECASE,
            )
        if match:
            out["raft_thickness"] = _ask_float(match.group(1))
    if not _ask_present(out, "floor_count"):
        match = re.search(
            rf"{_ASK_NUM}\s+(?:floors?|storeys?|stories|levels)\b",
            text, re.IGNORECASE,
        )
        if match:
            out["floor_count"] = int(_ask_float(match.group(1)))


def _extract_carbon_from_ask(text: str, out: Dict[str, Any]) -> None:
    if not _ask_present(out, "volume_m3"):
        match = re.search(rf"{_ASK_NUM}\s*m\s*3\b", text, re.IGNORECASE)
        if match is None:
            match = re.search(rf"{_ASK_NUM}\s*m³", text, re.IGNORECASE)
        if match:
            out["volume_m3"] = _ask_float(match.group(1))
    if not _ask_present(out, "grade"):
        match = re.search(r"\b(c\s*\d{2})\b", text, re.IGNORECASE)
        if match:
            out["grade"] = match.group(1).replace(" ", "").lower()


def _extract_mix_from_ask(text: str, out: Dict[str, Any]) -> None:
    if _ask_present(out, "w_c_ratio"):
        return
    match = re.search(
        rf"(?:w\s*/\s*c|w[_/]?c(?:\s+ratio)?)\s*[:=]?\s*{_ASK_NUM}",
        text, re.IGNORECASE,
    )
    if match:
        out["w_c_ratio"] = _ask_float(match.group(1))


def _csv_floats(raw: str) -> List[float]:
    return [_ask_float(tok) for tok in re.findall(_ASK_NUM, raw or "")]


def _extract_maturity_from_ask(text: str, out: Dict[str, Any]) -> None:
    """CSV temps / hours from the ask. Does not invent a missing series."""
    if not _ask_present(out, "temperature_history_c"):
        match = re.search(
            rf"((?:{_ASK_NUM}\s*,\s*){{1,}}{_ASK_NUM})\s*"
            rf"(?:°\s*C|deg(?:rees)?(?:\s*C)?|\bC\b)",
            text, re.IGNORECASE,
        )
        if match is None:
            match = re.search(
                rf"(?:temps?|temperatures?|temperature_history(?:_c)?)\s*[:=]?\s*\[?\s*"
                rf"((?:{_ASK_NUM}\s*,\s*){{1,}}{_ASK_NUM})\s*\]?",
                text, re.IGNORECASE,
            )
        if match:
            nums = _csv_floats(match.group(1))
            if nums:
                out["temperature_history_c"] = nums
    if not _ask_present(out, "time_intervals_hours"):
        match = re.search(
            rf"((?:{_ASK_NUM}\s*,\s*){{1,}}{_ASK_NUM})\s*"
            rf"(?:hours?|hrs?|h)\b",
            text, re.IGNORECASE,
        )
        if match is None:
            match = re.search(
                rf"(?:hours?|intervals?|time_intervals(?:_hours)?|dt)\s*[:=]?\s*\[?\s*"
                rf"((?:{_ASK_NUM}\s*,\s*){{1,}}{_ASK_NUM})\s*\]?",
                text, re.IGNORECASE,
            )
        if match:
            nums = _csv_floats(match.group(1))
            if nums:
                out["time_intervals_hours"] = nums


def _extract_rebar_cost_from_ask(text: str, out: Dict[str, Any]) -> None:
    if not _ask_present(out, "quantity_kg"):
        match = re.search(rf"{_ASK_NUM}\s*kg\b", text, re.IGNORECASE)
        if match:
            out["quantity_kg"] = _ask_float(match.group(1))
        else:
            match = re.search(
                rf"{_ASK_NUM}\s*(?:tonnes?|tons?|t)\b",
                text, re.IGNORECASE,
            )
            if match:
                out["quantity_kg"] = _ask_float(match.group(1)) * 1000.0
    if not _ask_present(out, "material_price_sar_t"):
        match = re.search(
            rf"{_ASK_NUM}\s*(?:SAR|USD|AED)?\s*/\s*t(?:onne)?s?\b",
            text, re.IGNORECASE,
        )
        if match:
            out["material_price_sar_t"] = _ask_float(match.group(1))


_TENDER_ROW_RE = re.compile(
    r"(Bidder\s+[A-Za-z0-9]+|[A-Za-z][\w.\-]{1,30})\s*[—\-:]\s*"
    r"technical\s+(\d+(?:\.\d+)?)\s*,\s*commercial\s+(\d+(?:\.\d+)?)\s*,\s*"
    r"HSE\s+(\d+(?:\.\d+)?)(?:\s*,\s*local(?:\s+content)?\s+(\d+(?:\.\d+)?))?",
    re.IGNORECASE,
)


def _extract_tender_from_ask(text: str, out: Dict[str, Any]) -> None:
    if _ask_present(out, "tenderers", "bidders", "bids"):
        return
    rows: List[Dict[str, Any]] = []
    for match in _TENDER_ROW_RE.finditer(text or ""):
        local = match.group(5)
        rows.append({
            "name": match.group(1).strip(),
            "technical_score": _ask_float(match.group(2)),
            "commercial_score": _ask_float(match.group(3)),
            "hse_score": _ask_float(match.group(4)),
            "local_content_score": _ask_float(local) if local is not None else 0,
        })
    if rows:
        out["tenderers"] = rows


def _extract_calc_kwargs_from_ask(
    name: str, params: Dict[str, Any],
) -> Dict[str, Any]:
    """Fill missing kwargs from ``text`` / ``formula``. Never invent defaults."""
    out = dict(params or {})
    blob = _ask_blob(out)
    if not blob.strip():
        return out
    # Live predispatch often ships only {text: ask}. Assignment strings
    # (temps=[20,22,25], w/c=0.48, cut=5000) must become kwargs here —
    # coerce_calc_params only sees the params object, not the ask blob.
    for key, val in _parse_kv_assignments(blob).items():
        if out.get(key) in (None, ""):
            out[key] = val
    calc = str(name or "").strip().lower()
    if calc == "beam_shear_simple":
        _extract_beam_shear_from_ask(blob, out)
    elif calc == "dewatering_uplift_check":
        _extract_dewatering_from_ask(blob, out)
    elif calc == "excavation_volume":
        _fill_lwd_from_text(blob, out, "depth_m")
    elif calc == "concrete_volume":
        _fill_lwd_from_text(blob, out, "thickness_m")
    elif calc == "carbon_footprint_concrete":
        _extract_carbon_from_ask(blob, out)
    elif calc == "concrete_mix_design_sg":
        _extract_mix_from_ask(blob, out)
    elif calc == "concrete_maturity_strength":
        _extract_maturity_from_ask(blob, out)
    elif calc == "cost_buildup_rebar":
        _extract_rebar_cost_from_ask(blob, out)
    elif calc == "evaluate_tender":
        _extract_tender_from_ask(blob, out)
    elif calc == "slab_thickness_min":
        _extract_slab_thickness_from_ask(blob, out)
    elif calc == "diaphragm_wall_panel_volume":
        _fill_lwd_from_text(blob, out, "excavation_depth")
        if _ask_present(out, "length_m") and not _ask_present(out, "panel_length"):
            out["panel_length"] = out["length_m"]
        if _ask_present(out, "thickness_m") and not _ask_present(out, "wall_thickness"):
            out["wall_thickness"] = out["thickness_m"]
        elif _ask_present(out, "width_m") and not _ask_present(out, "wall_thickness"):
            out["wall_thickness"] = out["width_m"]
        if _ask_present(out, "depth_m") and not _ask_present(out, "excavation_depth"):
            out["excavation_depth"] = out["depth_m"]
    return out


def _annotation_wants_list(annotation: Any) -> bool:
    if annotation is _inspect.Parameter.empty:
        return False
    if isinstance(annotation, str):
        low = annotation.replace(" ", "").lower()
        return low.startswith("list[") or low == "list"
    origin = getattr(annotation, "__origin__", None)
    return origin in (list, List)


def _coerce_scalar(val: Any) -> Any:
    if isinstance(val, bool) or val is None or isinstance(val, (int, float)):
        return val
    if not isinstance(val, str):
        return val
    text = val.strip()
    if not text:
        return val
    if _PLAIN_NUM_RE.match(text):
        number = text.replace(",", "").replace(" ", "")
        return float(number) if "." in number else int(number)
    match = _NUM_UNIT_VALUE_RE.match(text)
    if match:
        number = match.group(1).replace(",", "")
        return float(number) if "." in number else int(number)
    return val


def _coerce_list(val: Any) -> Any:
    if isinstance(val, (list, tuple)):
        return [_coerce_scalar(item) for item in val]
    if isinstance(val, str):
        text = val.strip()
        if text.startswith("["):
            try:
                obj = json.loads(text)
            except (TypeError, ValueError, json.JSONDecodeError):
                obj = None
            if isinstance(obj, list):
                return [_coerce_scalar(item) for item in obj]
        parts = [part for part in re.split(r"[,\s]+", text) if part]
        if parts and all(
            _PLAIN_NUM_RE.match(part) or _NUM_UNIT_VALUE_RE.match(part)
            for part in parts
        ):
            return [_coerce_scalar(part) for part in parts]
    coerced = _coerce_scalar(val)
    if isinstance(coerced, (int, float)):
        return [coerced]
    return val


def _coerce_bound_values(fn: Any, bound: Dict[str, Any]) -> Dict[str, Any]:
    """Strip unit suffixes from numeric strings; wrap list-typed scalars."""
    try:
        sig = _inspect.signature(fn)
    except (TypeError, ValueError):
        return bound
    out = dict(bound)
    for key, val in list(out.items()):
        param = sig.parameters.get(key)
        if param is None:
            continue
        if _annotation_wants_list(param.annotation):
            out[key] = _coerce_list(val)
            continue
        if isinstance(val, (list, tuple)) and len(val) == 1:
            val = val[0]
            out[key] = val
        ann = param.annotation
        ann_s = ann if isinstance(ann, str) else getattr(ann, "__name__", str(ann))
        if str(ann_s).lower() in ("str", "string"):
            continue
        if isinstance(val, str):
            out[key] = _coerce_scalar(val)
        # Live excavation ask-2 sent bulking=25 (percent) not 0.25.
        # Values in (1, 100] are percents; 1.25 stays a multiplier.
        # cost_buildup_rebar waste_pct=10 was the same class (wrong_number 40590).
        if key in {"bulking_factor", "swell_factor"} | _FRACTION_PCT_KEYS:
            number = out[key]
            if isinstance(number, (int, float)) and 1.0 < float(number) <= 100.0:
                out[key] = float(number) / 100.0
    return out


# Thousand separators must be interior (1,000) — a trailing comma is list
# punctuation ("100, man_hours 50") and must not be eaten as part of the number.
_TEXT_NUM_RE = r"[+-]?\d+(?:,\d{3})*(?:\.\d+)?"
_TEXT_UNIT_RE = (
    r"(?:kN/m2|kN/m²|N/mm2|N/mm²|mm2|mm²|m2|m²|m3|m³|"
    r"MPa|kPa|kN|mm|m/s|m|%|deg)"
)
_CODE_ACI_RE = re.compile(r"\b(?:aci|asce|aisc|tms)\b", re.IGNORECASE)
_CODE_EC_RE = re.compile(r"\b(?:eurocode|en\s*199\d)\b", re.IGNORECASE)
_UNIT_EQ = {
    "mpa": "mpa",
    "n/mm2": "mpa",
    "kpa": "kpa",
    "kn/m2": "kn/m2",
    "m2": "m2",
    "mm2": "mm2",
    "m3": "m3",
    "m": "m",
    "mm": "mm",
    "kn": "kn",
    "m/s": "m/s",
    "%": "%",
    "deg": "deg",
}


def _norm_label(raw: Any) -> str:
    return _snake_key(str(raw or "").replace(" ", "_").replace("/", "_"))


def _norm_unit_token(raw: Any) -> str:
    s = str(raw or "").strip().lower().replace("²", "2").replace("³", "3")
    s = s.replace(" ", "").replace(".", "")
    return _UNIT_EQ.get(s, s)


def _parse_text_number(raw: str) -> Optional[float]:
    try:
        return float(str(raw).replace(",", ""))
    except (TypeError, ValueError):
        logger.debug("engineer-ask token %r is not numeric", raw)
        return None


def _text_label_map(fn: Any) -> Dict[str, str]:
    """Normalized ask labels → unique signature dest for this calculator."""
    sig = _inspect.signature(fn)
    accepted_list = [
        key for key, param in sig.parameters.items()
        if param.kind in (param.POSITIONAL_OR_KEYWORD, param.KEYWORD_ONLY)
        and not key.startswith("_")
    ]
    accepted_norm = {_snake_key(k): k for k in accepted_list}
    out: Dict[str, str] = {}
    ambiguous: set[str] = set()

    def add(label: str, dest: str) -> None:
        key = _norm_label(label)
        if not key or key in ambiguous:
            return
        prev = out.get(key)
        if prev is None:
            out[key] = dest
        elif prev != dest:
            out.pop(key, None)
            ambiguous.add(key)

    for dest in accepted_list:
        add(dest, dest)
        add(dest.replace("_", " "), dest)
        if dest in _EXPLICIT_BIND_ONLY:
            continue
        stem = dest
        for suf, _unit in _BIND_UNIT_SUFFIXES:
            if stem.endswith(suf) and len(stem) > len(suf):
                stem = stem[: -len(suf)]
                add(stem, dest)
                break
        add(stem.replace("_", " "), dest)

    for incoming, dests in _BIND_SEMANTIC_ALIASES.items():
        dest = _unique_semantic_dest(_snake_key(incoming), accepted_norm)
        if dest:
            add(incoming, dest)
    return out


def extract_calculation_params_from_text(
    fn: Any,
    text: str,
) -> Dict[str, Any]:
    """Pull labeled engineering numbers out of ask text. Never invents.

    Matches As1500 / fy=420 / span 8m / W=10000 kN against this calculator's
    signature + aliases. A leftover number+unit binds only when exactly one
    still-missing required param has that unit (D7).
    """
    raw = str(text or "").strip()
    if not raw or fn is None:
        return {}
    labels = _text_label_map(fn)
    if not labels:
        return {}
    found: Dict[str, Any] = {}
    consumed: List[Tuple[int, int]] = []

    def _overlaps(span: Tuple[int, int]) -> bool:
        return any(span[0] < c[1] and c[0] < span[1] for c in consumed)

    for label, dest in sorted(labels.items(), key=lambda kv: len(kv[0]), reverse=True):
        if dest in found:
            continue
        pat = r"[\s_]+".join(re.escape(part) for part in label.split("_") if part)
        compact = label.replace("_", "")
        match = None
        if len(compact) == 1:
            rx = re.compile(
                rf"(?<![A-Za-z0-9]){pat}\s*[=:]\s*({_TEXT_NUM_RE})"
                rf"(?:\s*({_TEXT_UNIT_RE}))?",
                re.IGNORECASE,
            )
            glued = re.compile(
                rf"(?<![A-Za-z0-9]){pat}({_TEXT_NUM_RE})(?![A-Za-z])"
                rf"(?:\s*({_TEXT_UNIT_RE}))?",
                re.IGNORECASE,
            )
            match = rx.search(raw) or glued.search(raw)
        else:
            rx = re.compile(
                rf"(?<![A-Za-z0-9]){pat}(?:\s*[=:]\s*|\s+)({_TEXT_NUM_RE})"
                rf"(?:\s*({_TEXT_UNIT_RE}))?",
                re.IGNORECASE,
            )
            match = rx.search(raw)
            if match is None and len(compact) <= 4:
                glued = re.compile(
                    rf"(?<![A-Za-z0-9]){pat}({_TEXT_NUM_RE})(?![A-Za-z0-9])"
                    rf"(?:\s*({_TEXT_UNIT_RE}))?",
                    re.IGNORECASE,
                )
                match = glued.search(raw)
        if match is None or _overlaps(match.span()):
            continue
        num = _parse_text_number(match.group(1))
        if num is None:
            continue
        found[dest] = num
        consumed.append(match.span())

    accepted = {
        row["name"] for row in describe_calculation_params(fn)
    }
    if "code" in accepted and "code" not in found:
        aci = bool(_CODE_ACI_RE.search(raw))
        euro = bool(_CODE_EC_RE.search(raw))
        if aci and not euro:
            found["code"] = "aci"
        elif euro and not aci:
            found["code"] = "eurocode"

    required_by_unit: Dict[str, List[str]] = {}
    for row in describe_calculation_params(fn):
        if not row.get("required") or row["name"] in found:
            continue
        unit = _norm_unit_token(_unit_from_name(row["name"]))
        if not unit:
            continue
        required_by_unit.setdefault(unit, []).append(row["name"])

    leftover_rx = re.compile(
        rf"({_TEXT_NUM_RE})\s*({_TEXT_UNIT_RE})\b",
        re.IGNORECASE,
    )
    leftovers: Dict[str, List[float]] = {}
    for match in leftover_rx.finditer(raw):
        if _overlaps(match.span()):
            continue
        num = _parse_text_number(match.group(1))
        unit = _norm_unit_token(match.group(2))
        if num is None or not unit:
            continue
        leftovers.setdefault(unit, []).append(num)
        consumed.append(match.span())
    for unit, nums in leftovers.items():
        dests = required_by_unit.get(unit) or []
        if len(nums) == 1 and len(dests) == 1:
            found[dests[0]] = nums[0]
    return found


def _missing_required(fn: Any, bound: Dict[str, Any], name: Optional[str] = None) -> List[str]:
    missing: List[str] = []
    groups = _REQUIRED_GROUPS.get(str(name or "").strip().lower())
    if groups:
        for group in groups:
            if not any(k in bound and bound[k] not in (None, "") for k in group):
                missing.append(group[0])
        return missing
    for row in describe_calculation_params(fn, name=name):
        if row["required"] and row["name"] not in bound:
            missing.append(row["name"])
    return missing


def _format_param_label(row: Dict[str, Any]) -> str:
    name = row["name"]
    unit = row.get("unit") or ""
    if unit:
        name = f"{name} ({unit})"
    if row.get("required"):
        return name
    default = row.get("default")
    return f"{name} (default {default!r})"


def _bind_error_envelope(
    name: str,
    fn: Any,
    missing: List[str],
    extra: str = "",
) -> Dict[str, Any]:
    expected = describe_calculation_params(fn, name=name)
    by_name = {row["name"]: row for row in expected}
    missing_labels = [
        _format_param_label(by_name[m]) if m in by_name else m for m in missing
    ]
    expected_labels = [_format_param_label(row) for row in expected]
    if missing_labels:
        detail = "missing required " + ", ".join(missing_labels)
    else:
        detail = "could not bind arguments"
    expected_clause = ", ".join(expected_labels) if expected_labels else "(none)"
    msg = f"Bad parameters for {name}: {detail}. Expected: {expected_clause}."
    if extra:
        msg = f"{msg} ({extra})"
    return {
        "status": "error",
        "error": msg,
        "signature": f"{name}{_inspect.signature(fn)}",
        "expected_params": expected,
        "missing": missing,
    }


def run_calculation(name: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Run one whitelisted deterministic calculator by name with keyword params.

    Returns a standard envelope: {status, calculation, result, note} on success,
    or {status:error, error, ...} on a bad name / bad params. COST build-ups use
    the unit rates passed in ``params``; the built-in defaults are indicative GCC
    fallbacks only — pass the project's priced-BOQ rates (from RAG) for a firm
    cost so the answer stays grounded.
    """
    params = params or {}
    if not isinstance(params, dict):
        coerced = coerce_calc_params(params)
        if coerced:
            params = coerced
        elif params:
            return {"status": "error", "error": "params must be an object of keyword arguments."}
        else:
            params = {}
    positional = _pop_positional(params) if isinstance(params, dict) else None
    # Shared with Agent C / #636 / #639 / #652: nested ``params`` / ``input``
    # + top-level siblings (volume / BCWS / excavation_bank_m3 / water_depth_m)
    # must reach the calculator. Flatten before E4 so a nested concrete ask
    # still pins.
    params = _flatten_calc_kwargs(params)
    if positional is None:
        positional = _pop_positional(params)
    else:
        params.pop(_POSITIONAL_KEY, None)
    # Live UI pack E4: a concrete/raft ask (or leftover-L6 excavation name
    # plus "documented waste factor" in ``text``) must apply the project's
    # 5% waste. Resolve before the name lookup so a missing calculation
    # still reaches concrete_volume when the ask carries the dims.
    original_name = name
    try:
        from app.lib.construction_formulas_quantities import (
            resolve_concrete_volume_calc,
        )
        name, params = resolve_concrete_volume_calc(name, params)
    except Exception:  # noqa: BLE001 — never break a non-concrete calc
        logger.warning(
            "swallowed %s in resolve_concrete_volume_calc() — continuing",
            "Exception", exc_info=True,
        )
    # Phase 2 F–W (#43–84): remap resource_line / material_consumption
    # mis-picks (and undo an E4 concrete_volume steal) after the volume
    # pin so a "concrete" word in a consumption ask is not left on E4.
    try:
        from app.lib.construction_formulas_quantities import resolve_fw_calc
        name, params = resolve_fw_calc(
            name, params, original_name=original_name,
        )
    except Exception:  # noqa: BLE001 — never break a non-F–W calc
        logger.warning(
            "swallowed %s in resolve_fw_calc() — continuing",
            "Exception", exc_info=True,
        )
    fn = CALCULATORS.get(name)
    if fn is None:
        # Recover only from the ask/params prose (text/formula/message/query).
        # Do not feed the unknown *name* into calculator_name_from_text —
        # "calculate_delay_damages" contains "delay damages" and would
        # silently bind delay_damages_daily, dropping the Unknown-calculation
        # envelope (and its ``available`` list) that the model needs to retry.
        blob = _ask_blob(params) if isinstance(params, dict) else ""
        recovered = calculator_name_from_text(blob) if str(blob).strip() else None
        if recovered:
            name = recovered
            fn = CALCULATORS.get(name)
    if fn is None:
        return {
            "status": "error",
            "error": f"Unknown calculation '{name}'.",
            "available": available_calculations(),
        }
    if positional:
        for key, val in _bind_sequence_params(fn, positional, name).items():
            if params.get(key) in (None, ""):
                params[key] = val
    # Signature-derived bind (case-insensitive + unit-suffix synonyms).
    # Generalises #636's calculate_evm PMI aliases — do not re-add a
    # name-specific filter here (Agent C / DIR7 share this path).
    if str(name or "").strip().lower() == "calculate_evm":
        params = _alias_calculate_evm_params(params)
    # Standing-exit B: model aliases / numbers live in ``text`` more often
    # than in kwargs. Fill holes only — never invent defaults.
    params = _extract_calc_kwargs_from_ask(str(name), params)
    # Named-calculator / predispatch often ships ``text`` only. Pull labeled
    # numbers (As1500, span 8m, W=10000 kN) into kwargs. Explicit keys win.
    # D7: extract never invents a figure that is not in the ask.
    text_blob = " ".join(
        str(x) for x in (
            params.get("text"), params.get("formula"), params.get("message"),
        ) if x not in (None, "")
    )
    if text_blob:
        for key, val in extract_calculation_params_from_text(fn, text_blob).items():
            if params.get(key) in (None, ""):
                params[key] = val
    params = bind_calculation_params(fn, params)
    params = _coerce_bound_values(fn, params)
    missing = _missing_required(fn, params, name=str(name))
    if missing:
        env = _bind_error_envelope(str(name), fn, missing)
        # #649 canned help names paired alternatives (rebar length-or-mass,
        # productivity pairs) that the signature-required list omits.
        if str(name or "").strip() in _CALC_REQUIRED_HELP:
            env["error"] = required_params_error(name, fn)
            env["calculation"] = name
        return env
    try:
        result = fn(**params)
    except TypeError as exc:
        # Never a bare TypeError string alone — name the expected params
        # with units so the next call can bind (#639/#652 envelope + #649
        # required_params_error canned help). Keep inspect signature.
        still_missing = _missing_required(fn, params, name=str(name)) or [
            row["name"] for row in describe_calculation_params(fn, name=str(name))
            if row["required"]
        ]
        env = _bind_error_envelope(str(name), fn, still_missing, extra=str(exc))
        env["calculation"] = name
        env["cause"] = str(exc)
        if str(name or "").strip() in _CALC_REQUIRED_HELP:
            env["error"] = required_params_error(name, fn)
        return env
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
        err = result["error"]
        if name in _CALC_REQUIRED_HELP and "needs" in err.lower():
            err = _CALC_REQUIRED_HELP[name]
        out = {
            "status": "error",
            "calculation": name,
            "error": err,
        }
        if isinstance(result.get("required"), list):
            out["required"] = result["required"]
        if isinstance(result.get("required_pairs"), list):
            out["required_pairs"] = result["required_pairs"]
        return out

    # Phase 2 F–W (#43–84) leftovers: stamp unit / unitless on the four
    # results the live probe scored as "number present, unit missing".
    try:
        from app.lib.construction_formulas_quantities import (
            FW_ROUTE_NAMES,
            shape_fw_calc_result,
        )
        if name in FW_ROUTE_NAMES:
            result = shape_fw_calc_result(name, result)
    except Exception:  # noqa: BLE001 — never break a successful calc
        logger.warning(
            "swallowed %s in shape_fw_calc_result() — continuing",
            "Exception", exc_info=True,
        )

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
