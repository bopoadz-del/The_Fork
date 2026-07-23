"""Site-safety calculators (additive library, gap-fill).

Scaffold duty capacity (OSHA 1926 Subpart L, 4:1 safety factor), fall-arrest
maximum arresting force (energy balance, OSHA 8 kN limit), and crane lift
utilisation from a de-rated chart capacity. Arithmetic shown in ``note``.
"""
from __future__ import annotations

# Scaffold duty rated loads (OSHA 1926.451): light 25 psf, medium 50, heavy 75.
_SCAFFOLD_DUTY_KPA = {"light": 1.2, "medium": 2.4, "heavy": 3.6}
_OSHA_MAF_KN = 8.0  # OSHA 1926.502 maximum arresting force (body harness)


def scaffold_load_capacity(
    platform_area_m2: float,
    duty: str = "medium",
    safety_factor: float = 4.0,
) -> dict:
    """Scaffold intended load and the capacity it must be built to support.
    OSHA 1926.451: scaffolds support their own weight + 4x the intended load."""
    d = (duty or "medium").strip().lower()
    duty_kpa = _SCAFFOLD_DUTY_KPA.get(d, 2.4)
    area = float(platform_area_m2)
    intended_kn = duty_kpa * area
    required_kn = intended_kn * safety_factor
    return {
        "duty": d,
        "duty_load_kpa": duty_kpa,
        "intended_load_kn": round(intended_kn, 2),
        "required_capacity_kn": round(required_kn, 2),
        "safety_factor": safety_factor,
        "standard": "OSHA 1926.451 Subpart L (4:1)",
        "note": (f"Intended = {duty_kpa} kPa * {area} m2 = {intended_kn:.1f} kN; "
                 f"must support {safety_factor}x = {required_kn:.1f} kN."),
    }


def fall_arrest_force(
    worker_mass_kg: float = 100.0,
    free_fall_m: float = 1.8,
    deceleration_distance_m: float = 1.0,
    g: float = 9.81,
) -> dict:
    """Peak arresting force from an energy balance: F = W*(1 + H/d), where W =
    m*g, H = free-fall distance, d = deceleration (energy-absorber) distance.
    OSHA 1926.502 caps the maximum arresting force at 8 kN with a body harness."""
    w_n = float(worker_mass_kg) * g
    H = float(free_fall_m)
    d = float(deceleration_distance_m)
    f_n = w_n * (1.0 + H / d) if d > 0 else float("inf")
    f_kn = f_n / 1000.0
    within = f_kn <= _OSHA_MAF_KN
    return {
        "max_arrest_force_kn": round(f_kn, 2),
        "within_osha_limit": within,
        "osha_limit_kn": _OSHA_MAF_KN,
        "standard": "OSHA 1926.502 (MAF <= 8 kN)",
        "note": (f"F = W*(1 + H/d) = {w_n:.0f}*(1 + {H}/{d}) = {f_n:.0f} N = "
                 f"{f_kn:.2f} kN ({'OK' if within else 'EXCEEDS'} 8 kN limit)."),
    }


def crane_lift_capacity(
    chart_capacity_t: float,
    deductions_t: float,
    load_t: float,
    max_utilization: float = 0.85,
) -> dict:
    """Net crane capacity after rigging/block/jib deductions, and the lift
    utilisation. Net = chart - deductions; util = load/net; pass if <= max
    (typical lift-plan limit 85% of chart)."""
    net = float(chart_capacity_t) - float(deductions_t)
    util = (float(load_t) / net) if net > 0 else float("inf")
    passed = util <= max_utilization
    return {
        "net_capacity_t": round(net, 2),
        "utilization_percent": round(util * 100.0, 2),
        "passed": passed,
        "max_utilization_percent": round(max_utilization * 100.0, 1),
        "standard": "crane lift plan (de-rated chart)",
        "note": (f"Net = {chart_capacity_t} - {deductions_t} = {net:.1f} t; "
                 f"util = {load_t}/{net:.1f} = {util*100:.1f}% "
                 f"({'PASS' if passed else 'FAIL'} vs {max_utilization*100:.0f}%)."),
    }


ADDITIONAL_CALCULATORS = {
    "scaffold_load_capacity": scaffold_load_capacity,
    "fall_arrest_force": fall_arrest_force,
    "crane_lift_capacity": crane_lift_capacity,
}
