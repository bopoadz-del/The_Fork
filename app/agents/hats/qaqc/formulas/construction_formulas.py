"""Formulas from app.lib.construction_formulas owned by the qaqc hat.

Moved unchanged by scripts/move_formulas.py (F-DRIVER Phase A).
"""
from __future__ import annotations
from app.lib.formula_registry import Param, formula, grade_label
import json
import math
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from app.agents.base.formulas.construction_formulas_shared import (  # noqa: F401
    _is_aci_code,
)

_SERIES_FIGURE = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?"


def _series_figures(value: Any) -> List[float]:
    """Every figure written in a series: a list, a JSON array, or numbers
    separated by commas or spaces. One figure is one reading."""
    if value is None:
        return []
    if isinstance(value, bool):
        return []
    if isinstance(value, (int, float)):
        return [float(value)]
    if isinstance(value, (list, tuple)):
        out: List[float] = []
        for item in value:
            out.extend(_series_figures(item))
        return out
    text = str(value).strip()
    if not text:
        return []
    if text.startswith("["):
        try:
            obj = json.loads(text)
        except (TypeError, ValueError, json.JSONDecodeError):
            obj = None
        if isinstance(obj, (list, tuple)):
            return _series_figures(obj)
    return [float(match.group(0)) for match in re.finditer(_SERIES_FIGURE, text)]


def _code_token(code: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(code or "").lower())


def _is_ec2_code(code: Any) -> bool:
    """Eurocode 2 / EN 1992, however the caller writes the name."""
    token = _code_token(code)
    if not token:
        return False
    return (
        "en1992" in token
        or token.startswith("ec2")
        or token.startswith("eurocode2")
        or token in {"eurocode", "ec"}
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
    verdict: str = ""

@formula(
    owner='qaqc',
    display_name='Formwork striking time',
    description='Earliest formwork striking time from element type, temperature and test strength.',
    inputs={
        'concrete_strength_7h': Param('N/mm2', 0, 150, label='cube strength at striking'),
        'design_required_strength': Param('N/mm2', 0, 150, label='required striking strength'),
        'ciria_surface_strength': Param('N/mm2', 0, 150, label='CIRIA surface strength'),
        'bs8110_minimum_hours': Param('h', 0, 1000, label='minimum striking time'),
        'proposed_hours': Param('h', 0, 1000, label='proposed striking time'),
    },
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
        f"BS 8110 asks for at least {bs8110_minimum_hours} hours for wall formwork.",
        f"CIRIA asks for a surface strength of at least {ciria_surface_strength} N/mm².",
        f"The design required strength is {design_required_strength} N/mm².",
        f"The cube test recorded {concrete_strength_7h} N/mm² at striking.",
    ]
    if concrete_strength_7h >= ciria_surface_strength:
        notes.append(
            f"The cube strength {concrete_strength_7h} N/mm² meets the CIRIA "
            f"surface minimum of {ciria_surface_strength} N/mm²."
        )
    else:
        notes.append(
            f"The cube strength {concrete_strength_7h} N/mm² is below the CIRIA "
            f"surface minimum of {ciria_surface_strength} N/mm²."
        )
    strength_ok = (
        concrete_strength_7h >= ciria_surface_strength
        and concrete_strength_7h >= design_required_strength
    )
    based_on = "test_data" if strength_ok else "insufficient"
    recommended_hours = (
        proposed_hours if strength_ok else max(proposed_hours, bs8110_minimum_hours)
    )
    if strength_ok:
        verdict = (
            f"Formwork may be struck after {recommended_hours} hours; "
            "the cube strength meets both minima."
        )
    else:
        verdict = (
            f"Formwork should stay in place for {recommended_hours} hours; "
            "the cube strength does not yet meet both minima."
        )
    notes.append(verdict)
    return FormworkStrikingResult(
        recommended_hours=recommended_hours,
        bs8110_minimum_hours=bs8110_minimum_hours,
        ciria_minimum_strength_n_mm2=ciria_surface_strength,
        design_required_strength_n_mm2=design_required_strength,
        actual_early_strength_n_mm2=concrete_strength_7h,
        based_on=based_on,
        notes=notes,
        verdict=verdict,
    )

@formula(
    owner='qaqc',
    display_name='Aggregate fineness modulus',
    description='Fineness modulus of an aggregate from its cumulative sieve retentions.',
    inputs={
        'sieve_retained_percentages': Param('%', 0, 100, label='cumulative percentages retained'),
    },
    outputs={'fineness_modulus': '-'},
)
def fineness_modulus(sieve_retained_percentages: List[float]) -> float:
    """FM = (cumulative % retained on all sieves) / 100."""
    figures = _series_figures(sieve_retained_percentages)
    if not figures:
        return 0.0
    outside = next((value for value in figures if value < 0.0 or value > 100.0), None)
    if outside is not None:
        return {
            "error": (
                "Each cumulative percentage retained must be between 0 and 100 %, "
                f"not {outside}."
            ),
        }
    return round(sum(figures) / 100.0, 2)

@formula(
    owner='qaqc',
    display_name='Concrete modulus of rupture',
    description='Flexural tensile strength of concrete from the named design code.',
    inputs={
        'fck_n_mm2': Param('N/mm2', 5, 150, label='characteristic concrete strength', grade='concrete'),
        'code': Param('-', label='design code'),
        'lambda_factor': Param('-', 0.5, 1.0, label='lightweight concrete factor'),
        'h_mm': Param('mm', 1, 5000, label='section depth'),
    },
    outputs={
        'fck_n_mm2': 'N/mm2',
        'value': 'N/mm2',
        'unit': '-',
        'modulus_of_rupture_n_mm2': 'N/mm2',
    },
)
def modulus_of_rupture(
    fck_n_mm2: float,
    code: str,
    lambda_factor: float = 1.0,
    h_mm: Optional[float] = None,
) -> Dict[str, Any]:
    """Flexural tensile strength. The named code picks the clause.

    * ACI 318-19 Eq. 19.2.3.1: fr = 0.62 λ √f'c (MPa). λ defaults to 1.0.
    * EN 1992-1-1 Table 3.1: fctm = 0.30 fck^(2/3) for fck ≤ 50 MPa,
      else 2.12 ln(1 + (fck + 8)/10). When a depth h is given,
      §3.1.8: fctm,fl = max((1.6 − h/1000) fctm, fctm).

    No code is not a default. An unknown code is refused.
    """
    if not str(code or "").strip():
        return {
            "error": "The design code is required.",
            "needs_input": ["code"],
        }
    fck = float(fck_n_mm2)
    if fck <= 0:
        return {
            "error": "The characteristic concrete strength must be greater than zero.",
        }

    if _is_aci_code(code):
        lam = float(lambda_factor)
        fr = 0.62 * lam * math.sqrt(fck)
        fr_mpa = round(fr, 3)
        return {
            "fck_n_mm2": fck,
            "value": fr_mpa,
            "unit": "MPa",
            "modulus_of_rupture_n_mm2": fr_mpa,
            "lambda_factor": lam,
            "standard": (
                "ACI 318-19 Eq. 19.2.3.1: fr = 0.62*λ*√f'c MPa "
                f"(λ = {lam})"
            ),
        }

    if _is_ec2_code(code):
        if fck <= 50.0:
            fctm = 0.30 * (fck ** (2.0 / 3.0))
            fctm_clause = (
                "EN 1992-1-1 Table 3.1: fctm = 0.30*fck^(2/3) "
                "for fck ≤ 50 MPa"
            )
        else:
            fctm = 2.12 * math.log(1.0 + (fck + 8.0) / 10.0)
            fctm_clause = (
                "EN 1992-1-1 Table 3.1: fctm = 2.12*ln(1+(fck+8)/10) "
                "for fck > 50 MPa"
            )
        depth = None
        if h_mm not in (None, ""):
            depth = float(h_mm)
        if depth is not None and depth > 0:
            fctm_fl = max((1.6 - depth / 1000.0) * fctm, fctm)
            value = round(fctm_fl, 3)
            return {
                "fck_n_mm2": fck,
                "value": value,
                "unit": "MPa",
                "modulus_of_rupture_n_mm2": value,
                "fctm_n_mm2": round(fctm, 3),
                "fctm_fl_n_mm2": value,
                "h_mm": depth,
                "standard": (
                    f"{fctm_clause}; EN 1992-1-1 §3.1.8: "
                    "fctm,fl = max((1.6-h/1000)*fctm, fctm)"
                ),
            }
        value = round(fctm, 3)
        return {
            "fck_n_mm2": fck,
            "value": value,
            "unit": "MPa",
            "modulus_of_rupture_n_mm2": value,
            "fctm_n_mm2": value,
            "standard": fctm_clause,
        }

    shown = str(code).strip()
    return {
        "error": (
            f"The design code {shown} is not one of the codes this calculator "
            "supports. It supports ACI 318 and Eurocode 2."
        ),
    }

@formula(
    owner='qaqc',
    display_name='Mass concrete thermal check',
    description='Mass-concrete thermal check: core temperature and core-to-surface difference against limits.',
    inputs={
        'core_temp_c': Param('degC', -30, 120, label='core temperature'),
        'surface_temp_c': Param('degC', -50, 100, label='surface temperature'),
    },
    outputs={'core_temp_c': 'degC', 'surface_temp_c': 'degC', 'delta_t_c': 'degC', 'core_ok': '-', 'delta_ok': '-', 'thermal_cracking_risk': '-'},
)
def concrete_thermal_cracking_check(core_temp_c: float, surface_temp_c: float) -> Dict[str, Any]:
    """Mass concrete limits: core <= 70C, delta_T <= 20C."""
    delta_t = core_temp_c - surface_temp_c
    core_ok = core_temp_c <= 70
    delta_ok = delta_t <= 20
    notes = []
    if not core_ok:
        notes.append(f"The core temperature {core_temp_c} °C is above the 70 °C limit.")
    if not delta_ok:
        notes.append(f"The core-to-surface difference {delta_t} °C is above the 20 °C limit.")
    if core_ok and delta_ok:
        notes.append(
            f"The core temperature {core_temp_c} °C and the difference {delta_t} °C "
            "are within the limits."
        )
    return {
        "core_temp_c": core_temp_c, "surface_temp_c": surface_temp_c,
        "delta_t_c": round(delta_t, 1), "core_ok": core_ok, "delta_ok": delta_ok,
        "thermal_cracking_risk": not (core_ok and delta_ok), "notes": notes,
    }

@formula(
    owner='qaqc',
    display_name='Post-tensioning grout checks',
    description='Grouting pressure and strength checks for post-tensioning ducts.',
    inputs={
        'tendon_duct_diameter_mm': Param('mm', 20, 150, label='duct diameter'),
        'required_pressure_n_mm2': Param('N/mm2', 0, 10, label='grout pressure'),
        'strength_28d_n_mm2': Param('N/mm2', 1, 150, label='28-day strength'),
        'strength_7d_n_mm2': Param('N/mm2', 1, 150, label='7-day strength'),
        'mixing_time_minutes': Param('min', 0, 120, label='mixing time'),
    },
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
    inputs={
        'temperature_history_c': Param('degC', -30, 100, label='recorded temperatures'),
        'time_intervals_hours': Param('h', 0, 100000, label='time intervals'),
        'datum_temperature': Param('degC', -30, 10, label='datum temperature'),
        'strength_28d_n_mm2': Param('N/mm2', 1, 150, label='28-day strength', grade='concrete'),
        'reference_temperature_c': Param('degC', -30, 80, label='reference temperature'),
        'gain_a': Param('-', 0, 50, label='strength-gain constant a'),
        'gain_b': Param('-', 0, 1, label='strength-gain constant b'),
    },
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
    temps = _series_figures(temperature_history_c)
    hours = _series_figures(time_intervals_hours)
    if len(hours) == 1 and not isinstance(time_intervals_hours, (list, tuple)):
        hours = hours * len(temps) if temps else hours
    if not temps or not hours:
        return {
            "error": "The recorded temperatures and the time intervals are required and must not be empty.",
        }
    if len(temps) != len(hours):
        return {
            "error": "The recorded temperatures and the time intervals must have the same number of values.",
        }
    if any(float(dt) <= 0 for dt in hours):
        return {"error": "Each time interval must be greater than zero."}
    grade = grade_label(strength_28d_n_mm2)
    if grade is not None:
        strength_28d_n_mm2 = grade[1]
    if float(strength_28d_n_mm2) <= 0:
        return {"error": "strength_28d_n_mm2 must be > 0."}
    te_denom = float(reference_temperature_c) - float(datum_temperature)
    if te_denom <= 0:
        return {"error": "reference_temperature_c must be greater than datum_temperature."}

    mi = sum(
        max(0.0, float(t) - float(datum_temperature)) * float(dt)
        for t, dt in zip(temps, hours)
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
