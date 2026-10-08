"""Formulas from app.lib.construction_formulas owned by the qaqc hat.

Moved unchanged by scripts/move_formulas.py (F-DRIVER Phase A).
"""
from __future__ import annotations
from app.lib.formula_registry import formula
import math
from dataclasses import dataclass, field
from typing import Any, Dict, List
from app.agents.base.formulas.construction_formulas_shared import (  # noqa: F401
    _is_aci_code,
)


@dataclass
class FormworkStrikingResult:
    recommended_hours: float
    bs8110_minimum_hours: float
    ciria_minimum_strength_n_mm2: float
    design_required_strength_n_mm2: float
    actual_early_strength_n_mm2: float
    based_on: str
    notes: List[str] = field(default_factory=list)

@formula(
    owner='qaqc',
    display_name='Formwork striking time',
    description='Earliest formwork striking time from element type, temperature and test strength.',
    inputs={'concrete_strength_7h': '-', 'design_required_strength': '-', 'ciria_surface_strength': '-', 'bs8110_minimum_hours': 'h', 'proposed_hours': 'h'},
    outputs={'recommended_hours': 'h', 'bs8110_minimum_hours': 'h', 'ciria_minimum_strength_n_mm2': 'N/mm2', 'design_required_strength_n_mm2': 'N/mm2', 'actual_early_strength_n_mm2': 'N/mm2'},
)
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

@formula(
    owner='qaqc',
    display_name='Aggregate fineness modulus',
    description='Fineness modulus of an aggregate from its cumulative sieve retentions.',
    inputs={'sieve_retained_percentages': '%'},
    outputs={'fineness_modulus': '-'},
)
def fineness_modulus(sieve_retained_percentages: List[float]) -> float:
    """FM = (cumulative % retained on all sieves) / 100."""
    if not sieve_retained_percentages:
        return 0.0
    return round(sum(sieve_retained_percentages) / 100.0, 2)

@formula(
    owner='qaqc',
    display_name='Concrete modulus of rupture',
    description='Flexural tensile strength (modulus of rupture) of concrete, to the selected code.',
    inputs={'fck_n_mm2': 'N/mm2', 'code': '-'},
    outputs={'fck_n_mm2': 'N/mm2', 'value': 'currency', 'unit': '-', 'modulus_of_rupture_n_mm2': 'N/mm2', 'split_cylinder_aci_n_mm2': 'N/mm2', 'tensile_pct_of_compressive': '-'},
)
def modulus_of_rupture(fck_n_mm2: float, code: str = "metric_technical") -> Dict[str, float]:
    """Tensile strength. Two forms, and the caller's code decides which.

    * ``metric_technical`` (default, unchanged): fr = 2.4 sqrt(f'c) with f'c
      in kg/cm2, plus the split-cylinder figure -- for C30 that is 4.157 MPa.
    * ``aci``: ACI 318-19 Eq. 19.2.3.1, fr = 0.62 sqrt(f'c) in MPa (normal
      weight, lambda = 1.0) -- for C30, 3.40 MPa.

    Live, asked as a follow-up naming ACI 318-19 for f'c = 30: this
    returned 4.157 and the operator was shown 4.16 MPa. The sibling
    ``modulus_of_elasticity_concrete`` had already been given this parameter;
    this one was left behind, so an ACI question got the metric-technical
    answer with no sign that the code had been ignored.
    """
    # Not _norm_code: it defaults every unknown string to ACI, which would
    # silently change this function's default form.
    fck = float(fck_n_mm2)
    if _is_aci_code(code):
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

@formula(
    owner='qaqc',
    display_name='Mass concrete thermal check',
    description='Mass-concrete thermal check: core temperature and core-to-surface difference against limits.',
    inputs={'core_temp_c': 'degC', 'surface_temp_c': 'degC'},
    outputs={'core_temp_c': 'degC', 'surface_temp_c': 'degC', 'delta_t_c': 'degC', 'core_ok': '-', 'delta_ok': '-', 'thermal_cracking_risk': '-'},
)
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

@formula(
    owner='qaqc',
    display_name='Post-tensioning grout checks',
    description='Grouting pressure and strength checks for post-tensioning ducts.',
    inputs={'tendon_duct_diameter_mm': 'mm', 'required_pressure_n_mm2': 'N/mm2', 'strength_28d_n_mm2': 'N/mm2', 'strength_7d_n_mm2': 'N/mm2', 'mixing_time_minutes': '-'},
    outputs={'duct_area_mm2': 'mm2', 'grout_volume_l_m': 'm', 'pressure_n_mm2': 'N/mm2', 'pressure_kg_cm2': '-', 'pressure_psi': '-', 'strength_28d_n_mm2': 'N/mm2', 'strength_7d_n_mm2': 'N/mm2', 'mixing_time_min': '-'},
)
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

@formula(
    owner='qaqc',
    display_name='Concrete strength by maturity',
    description='Concrete strength from its temperature history by the maturity method.',
    inputs={'temperature_history_c': 'degC', 'time_intervals_hours': 'h', 'datum_temperature': '-', 'strength_28d_n_mm2': 'N/mm2', 'reference_temperature_c': 'degC', 'gain_a': '-', 'gain_b': '-'},
    outputs={'maturity_index_c_hrs': 'degC.h', 'equivalent_age_days': 'days', 'predicted_strength_n_mm2': 'N/mm2', 'percent_of_28d': '%'},
)
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
