"""Formulas from app.lib.construction_formulas owned by the safety hat.

Moved unchanged by scripts/move_formulas.py (F-DRIVER Phase A).
"""
from __future__ import annotations
from app.lib.formula_registry import formula
from typing import Dict


@formula(
    owner='safety',
    description='Wind force and overturning moment on climbing formwork from wind speed and exposed area.',
    inputs={'wind_velocity_m_s': 'm/s', 'formwork_area_m2': 'm2', 'formwork_height_m': 'm', 'formwork_width_m': 'm', 'shape_factor': '-'},
    outputs={'wind_pressure_kpa': 'kPa', 'wind_force_kn': 'kN', 'overturning_moment_kn_m': 'kN.m', 'bending_stress_n_m2': 'N/m2'},
)
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
