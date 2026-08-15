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




def interior_finishes_takeoff(
    floor_area_m2: float,
    perimeter_m: float,
    room_count: int,
    floor_to_ceiling_m: float,
    door_width_m: float = 0.0,
    door_height_m: float = 0.0,
    doors_per_room: float = 0.0,
    shared_wall_fraction: float = 0.5,
    window_deduction_m2: float = 0.0,
) -> dict:
    """Interior finishes quantities from a room take-off -- PROCESS, not facts.

    Every project-specific value is a PARAMETER: the floor-to-ceiling height,
    door schedule, and window deductions change per project and per floor, so
    none of them has a baked-in value here. ``floor_to_ceiling_m`` is required
    -- calling without it is a refusal, not a guess. ``shared_wall_fraction``
    models internal walls being shared between two rooms (each wall face is
    counted once per room in the summed perimeters, so the wall ELEMENT area
    is roughly half the summed face area); it is an explicit modelling
    parameter, stated in the basis, adjustable per project.

    Outputs: screed/tiling/ceiling areas (= floor area), skirting length
    (perimeter minus door widths), wall paint area (perimeter x height minus
    door and window deductions), and blockwork element area (shared-wall
    fraction of the net wall face area). The ``basis`` echoes every input so
    the take-off is auditable line by line.
    """
    if floor_to_ceiling_m <= 0:
        return {
            "status": "error",
            "error": "floor_to_ceiling_m is required (> 0): the height varies "
                     "per project and per floor and is never assumed here. "
                     "Take it from the section drawing or project facts.",
        }
    fa = float(floor_area_m2)
    pm = float(perimeter_m)
    rooms = int(room_count)
    h = float(floor_to_ceiling_m)
    doors_total = rooms * float(doors_per_room)
    door_area = float(door_width_m) * float(door_height_m)
    door_area_total = doors_total * door_area
    door_width_total = doors_total * float(door_width_m)

    gross_wall_face = pm * h
    net_wall_paint = max(gross_wall_face - door_area_total - float(window_deduction_m2), 0.0)
    blockwork = net_wall_paint * float(shared_wall_fraction)
    skirting = max(pm - door_width_total, 0.0)

    return {
        "floor_screed_m2": round(fa, 2),
        "floor_tiling_m2": round(fa, 2),
        "ceiling_finish_m2": round(fa, 2),
        "skirting_m": round(skirting, 2),
        "wall_paint_m2": round(net_wall_paint, 2),
        "blockwork_m2": round(blockwork, 2),
        "gross_wall_face_m2": round(gross_wall_face, 2),
        "door_deduction_m2": round(door_area_total, 2),
        "standard": "geometry (parameterised process)",
        "basis": [
            f"floor area {fa} m2; perimeter {pm} m; rooms {rooms}",
            f"floor-to-ceiling {h} m (project input, not a default)",
            f"doors: {doors_per_room}/room x {rooms} rooms, "
            f"{door_width_m} x {door_height_m} m = {door_area_total:.2f} m2 deducted",
            f"window deduction {window_deduction_m2} m2 (project input)",
            f"shared wall fraction {shared_wall_fraction} (modelling parameter)",
            "wall paint = perimeter x height - doors - windows; "
            "blockwork = wall face x shared fraction; "
            "skirting = perimeter - door widths",
        ],
    }


def resource_line_cost(
    quantity: float,
    daily_output: float,
    day_rate: float,
    material_rate_per_unit: float = -1.0,
    plant_fraction_of_labour: float = 0.0,
) -> dict:
    """Material / labour / plant split for one estimate line -- PROCESS only.

    The productivity (daily output per tradesman) and the day rate vary per
    market, project, and even floor: both are required inputs, never
    defaults. Labour = quantity / daily_output x day_rate. Material rate is
    optional; when absent (< 0) the material cell is reported as
    NO SOURCED RATE rather than a number, mirroring the estimating rule that
    a missing rate is a refusal, not an invention. Plant is modelled as a
    declared fraction of labour (small plant and scaffold allowance).
    """
    if daily_output <= 0 or day_rate <= 0:
        return {
            "status": "error",
            "error": "daily_output and day_rate are required (> 0): "
                     "productivity and rates vary per project and are never "
                     "assumed here. Supply them from project facts, the "
                     "knowledge base, or the operator.",
        }
    q = float(quantity)
    crew_days = q / float(daily_output)
    labour = crew_days * float(day_rate)
    plant = labour * float(plant_fraction_of_labour)
    has_material = float(material_rate_per_unit) >= 0
    material = q * float(material_rate_per_unit) if has_material else None
    total = labour + plant + (material or 0.0)
    return {
        "quantity": q,
        "crew_days": round(crew_days, 2),
        "labour_cost": round(labour, 2),
        "plant_cost": round(plant, 2),
        "material_cost": round(material, 2) if has_material else "NO SOURCED RATE",
        "total_cost": round(total, 2),
        "total_is_partial": not has_material,
        "standard": "resource build-up (parameterised process)",
        "note": (f"labour = {q}/{daily_output} x {day_rate} = {labour:.2f}; "
                 f"plant = {plant_fraction_of_labour} x labour = {plant:.2f}; "
                 + (f"material = {q} x {material_rate_per_unit} = {material:.2f}."
                    if has_material else "material: NO SOURCED RATE (no rate supplied).")),
    }


ADDITIONAL_CALCULATORS = {
    "concrete_volume": concrete_volume,
    "rebar_weight": rebar_weight,
    "rebar_by_area": rebar_by_area,
    "interior_finishes_takeoff": interior_finishes_takeoff,
    "resource_line_cost": resource_line_cost,
}
