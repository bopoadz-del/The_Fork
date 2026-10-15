"""Formulas from app.lib.construction_formulas_commercial owned by the base package (every hat).

Moved unchanged by scripts/move_formulas.py (F-DRIVER Phase A).
"""
from __future__ import annotations
from app.lib.formula_registry import Param, formula


@formula(
    owner='base',
    display_name='Quantity times unit rate',
    description='Line extension: quantity times unit rate.',
    inputs={
        'quantity': Param('nr', 0, 1e12, label='quantity'),
        'unit_rate': Param('currency', 0, 1e12, label='unit rate'),
    },
    outputs={'total_cost': 'currency'},
)
def unit_cost_total(quantity: float, unit_rate: float) -> dict:
    """Total = quantity * unit_rate (a line-item extension)."""
    q, r = float(quantity), float(unit_rate)
    total = q * r
    return {
        "total_cost": round(total, 2),
        "standard": "arithmetic (BOQ line item)",
        "note": f"Total = qty * rate = {q} * {r} = {total:.2f}.",
    }

@formula(
    owner='base',
    display_name='Cost per area',
    description='Unit cost per area: a total cost divided by its area.',
    inputs={
        'total_cost': Param('currency', 0, 1e13, label='total cost'),
        'area': Param('ratio', 0, 1e9, label='area'),
        'area_unit': Param('-', label='area unit'),
    },
    outputs={'cost_per_area': 'currency', 'area_unit': '-'},
)
def cost_per_area(total_cost: float, area: float, area_unit: str = "m2") -> dict:
    """Unit area cost = total_cost / area (per m2 or per sf, caller's unit)."""
    t, a = float(total_cost), float(area)
    if a <= 0:
        return {"error": "area must be > 0 — cannot compute a unit cost from a zero or negative area."}
    rate = t / a
    return {
        "cost_per_area": round(rate, 2),
        "area_unit": area_unit,
        "standard": "arithmetic",
        "note": f"Cost/{area_unit} = {t}/{a} = {rate:.2f} per {area_unit}.",
    }

@formula(
    owner='base',
    display_name='Productivity rate',
    description='Output per man-hour. The hours are already man-hours, so the crew size is not applied again.',
    inputs={
        'output_quantity': Param('nr', 0, 1e12, label='output quantity'),
        'labor_hours': Param('man-hour', 0, 1e8, label='man-hours'),
        'crew_size': Param('nr', 1, 10000, label='crew size'),
    },
    outputs={'rate_per_hour': '-', 'rate_per_worker_hour': '-', 'rate_per_crew_hour': '-', 'crew_hours': 'h', 'crew_size': '-', 'unit': '-', 'value': 'currency', 'rate_per_worker_hour_unit': '-'},
)
def productivity_rate(output_quantity: float, labor_hours: float, crew_size: int = 1) -> dict:
    """Output per man-hour. ``labor_hours`` is already man-hours (one
    person's hours), so the per-worker rate is output/man-hours. Dividing
    by the crew again would count the same people twice."""
    o, h = float(output_quantity), float(labor_hours)
    if h == 0:
        return {"error": "labor_hours must be > 0"}
    rate = o / h
    per_worker = rate
    rate_r = round(rate, 3)
    per_r = round(per_worker, 4)
    # The crew works together: its hours are the man-hours shared across the
    # crew, and its output per crew-hour is the rate per worker-hour times
    # the crew (lane 5: 120 m2 in 40 man-hours by 4 = 12 m2 per crew-hour).
    crew = max(int(crew_size or 1), 1)
    crew_hours = h / crew
    return {
        "rate_per_hour": rate_r,
        "rate_per_worker_hour": per_r,
        "rate_per_crew_hour": round(o / crew_hours, 4),
        "crew_hours": round(crew_hours, 4),
        "crew_size": crew_size,
        "unit": "/hr",
        "value": rate_r,
        "rate_per_worker_hour_unit": "/worker-hr",
        "standard": "arithmetic (productivity)",
        "note": (f"Rate = output/man-hours = {o}/{h} = {rate:.4f}/hr; "
                 f"per worker-hour = {per_worker:.4f}/worker-hr "
                 f"(man-hours are not divided by the crew of {crew_size} again); "
                 f"per crew-hour = output/(man-hours/crew) = {o}/{crew_hours:.4f} = {o / crew_hours:.4f}."),
    }
