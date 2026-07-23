"""Concrete QC calculators (additive library, drop-catalog gap-fill).

Cylinder strength, drying shrinkage (ACI 209 hyperbolic form), and curing time to
a strength fraction (ACI 209 strength-gain inverse). Deterministic; parameters
carry the mix/curing constants. Arithmetic shown in ``note``.
"""
from __future__ import annotations

import math


def concrete_cylinders(failure_load_kn: float, cylinder_diameter_mm: float = 150.0) -> dict:
    """Compressive strength from a cylinder test: f = P / A, A = pi/4 * d^2.
    Standard cylinder is 150 mm dia x 300 mm."""
    P_n = float(failure_load_kn) * 1000.0
    area = math.pi / 4.0 * float(cylinder_diameter_mm) ** 2
    f = P_n / area
    return {
        "compressive_strength_mpa": round(f, 2),
        "cylinder_area_mm2": round(area, 1),
        "standard": "ASTM C39 / BS EN 12390-3",
        "note": (f"A = pi/4*{cylinder_diameter_mm}^2 = {area:.0f} mm2; "
                 f"f = {P_n:.0f}/{area:.0f} = {f:.2f} MPa."),
    }


def concrete_shrinkage(
    time_days: float,
    ultimate_shrinkage_microstrain: float = 780.0,
    time_constant_days: float = 35.0,
) -> dict:
    """Drying shrinkage strain at time t (ACI 209): esh(t) = t/(f+t)*esh_ult,
    f=35 (moist-cured), esh_ult ~780 microstrain (adjust for RH/size)."""
    t = float(time_days)
    f = float(time_constant_days)
    frac = t / (f + t)
    esh = frac * float(ultimate_shrinkage_microstrain)
    return {
        "shrinkage_microstrain": round(esh, 2),
        "shrinkage_strain": round(esh * 1e-6, 8),
        "fraction_of_ultimate": round(frac, 4),
        "standard": "ACI 209R (hyperbolic)",
        "note": (f"esh = t/(f+t)*esh_ult = {t}/({f}+{t})*{ultimate_shrinkage_microstrain} "
                 f"= {esh:.1f} microstrain."),
    }


def concrete_curing_time(
    target_strength_fraction: float = 0.70,
    gain_a: float = 4.0,
    gain_b: float = 0.85,
) -> dict:
    """Days of moist curing to reach a fraction of f28 (ACI 209 strength gain
    f(t)/f28 = t/(a+b*t); Type I moist a=4, b=0.85). Inverse:
    t = a*frac/(1 - b*frac)."""
    frac = float(target_strength_fraction)
    denom = 1.0 - gain_b * frac
    days = gain_a * frac / denom if denom > 0 else float("inf")
    return {
        "days_to_target": round(days, 2),
        "target_fraction": frac,
        "standard": "ACI 209R (strength gain) / ACI 308 curing",
        "note": (f"t = a*frac/(1 - b*frac) = {gain_a}*{frac}/(1 - {gain_b}*{frac}) "
                 f"= {days:.2f} days to reach {frac*100:.0f}% f28."),
    }


ADDITIONAL_CALCULATORS = {
    "concrete_cylinders": concrete_cylinders,
    "concrete_shrinkage": concrete_shrinkage,
    "concrete_curing_time": concrete_curing_time,
}
