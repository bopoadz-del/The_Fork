"""Quantity take-off calculators (additive library, drop-catalog gap-fill).

Geometry + material density — code-agnostic (no ACI/EC distinction). Rebar mass
uses the physical relation mass/m = (pi/4)*d^2 * density, which reproduces the
BS 8666 / standard bar-mass table (d=16 -> 1.578 kg/m). Rates/densities are
parameters; arithmetic shown in ``note``.
"""
from __future__ import annotations

import math

_STEEL_DENSITY = 7850.0  # kg/m^3


def concrete_volume(
    length_m: float = 0.0,
    width_m: float = 0.0,
    thickness_m: float = 0.0,
    shape: str = "rectangular",
    diameter_m: float = 0.0,
    height_m: float = 0.0,
    top_width_m: float = 0.0,
    bottom_width_m: float = 0.0,
    depth_m: float = 0.0,
    waste_factor: float = 0.05,
) -> dict:
    """Concrete volume for a rectangular slab/element, a cylinder (column/pile),
    or a trapezoidal section (channel/footing), plus a waste allowance.

    rectangular: L*W*T. cylinder: pi*(D/2)^2*H.
    trapezoidal: ((top+bottom)/2 * depth) * length.
    """
    s = (shape or "rectangular").strip().lower()
    if s == "cylinder":
        net = math.pi * (diameter_m / 2.0) ** 2 * height_m
        expr = f"pi*({diameter_m}/2)^2*{height_m}"
    elif s == "trapezoidal":
        area = (top_width_m + bottom_width_m) / 2.0 * depth_m
        net = area * length_m
        expr = f"(({top_width_m}+{bottom_width_m})/2*{depth_m})*{length_m}"
    else:
        s = "rectangular"
        net = length_m * width_m * thickness_m
        expr = f"{length_m}*{width_m}*{thickness_m}"
    with_waste = net * (1.0 + waste_factor)
    return {
        "shape": s,
        "net_volume_m3": round(net, 3),
        "volume_with_waste_m3": round(with_waste, 3),
        "waste_factor": waste_factor,
        "standard": "geometry",
        "note": (f"Net = {expr} = {net:.3f} m3; "
                 f"+{waste_factor*100:.0f}% waste = {with_waste:.3f} m3."),
    }


def rebar_weight(
    bar_diameter_mm: float,
    total_length_m: float,
    quantity: int = 1,
    density_kg_m3: float = _STEEL_DENSITY,
) -> dict:
    """Mass of reinforcement bars. Unit mass = (pi/4)*d^2 * density (kg/m),
    reproducing the standard bar-mass table (d=16 -> 1.578 kg/m at 7850 kg/m3)."""
    d = float(bar_diameter_mm)
    area_m2 = math.pi / 4.0 * (d / 1000.0) ** 2
    unit_mass = area_m2 * density_kg_m3  # kg/m
    total = unit_mass * float(total_length_m) * int(quantity)
    return {
        "unit_mass_kg_m": round(unit_mass, 4),
        "total_mass_kg": round(total, 2),
        "total_mass_t": round(total / 1000.0, 4),
        "standard": "BS 8666 / bar-mass relation",
        "note": (f"Unit mass = (pi/4)*({d}/1000)^2*{density_kg_m3:.0f} = "
                 f"{unit_mass:.4f} kg/m; x {total_length_m} m x {quantity} = "
                 f"{total:.2f} kg."),
    }


def rebar_by_area(
    area_m2: float,
    spacing_mm: float,
    bar_diameter_mm: float,
    both_ways: bool = False,
    density_kg_m3: float = _STEEL_DENSITY,
) -> dict:
    """Reinforcement mass for a slab/wall area from a bar spacing. Bars per metre
    = 1000/spacing; length per m2 ~= bars/m (one way) x area; both_ways doubles."""
    d = float(bar_diameter_mm)
    unit_mass = math.pi / 4.0 * (d / 1000.0) ** 2 * density_kg_m3  # kg/m
    bars_per_m = 1000.0 / float(spacing_mm)
    length_per_m2 = bars_per_m  # 1 m run of bar per bar-line across a 1 m width
    ways = 2 if both_ways else 1
    total_length = length_per_m2 * float(area_m2) * ways
    total_mass = total_length * unit_mass
    return {
        "bars_per_m": round(bars_per_m, 3),
        "total_bar_length_m": round(total_length, 2),
        "total_mass_kg": round(total_mass, 2),
        "both_ways": both_ways,
        "standard": "geometry / bar-mass relation",
        "note": (f"{bars_per_m:.2f} bars/m x {area_m2} m2 x {ways} way(s) = "
                 f"{total_length:.1f} m; x {unit_mass:.4f} kg/m = {total_mass:.1f} kg."),
    }


ADDITIONAL_CALCULATORS = {
    "concrete_volume": concrete_volume,
    "rebar_weight": rebar_weight,
    "rebar_by_area": rebar_by_area,
}
