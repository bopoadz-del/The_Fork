"""Formulas from app.lib.construction_formulas_commercial owned by the base package (every hat).

Moved unchanged by scripts/move_formulas.py (F-DRIVER Phase A).
"""
from __future__ import annotations
from app.lib.formula_registry import formula


@formula(
    owner='base',
    display_name='Quantity times unit rate',
    description='Line extension: quantity times unit rate.',
    inputs={'quantity': '-', 'unit_rate': '-'},
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
    inputs={'total_cost': 'currency', 'area': '-', 'area_unit': '-'},
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
    description='Output per labour-hour and per worker from an output, the hours worked and the crew size.',
    inputs={'output_quantity': '-', 'labor_hours': 'h', 'crew_size': '-'},
    outputs={'rate_per_hour': '-', 'rate_per_worker_hour': '-', 'crew_size': '-', 'unit': '-', 'value': 'currency', 'rate_per_worker_hour_unit': '-'},
)
def productivity_rate(output_quantity: float, labor_hours: float, crew_size: int = 1) -> dict:
    """Output per labour-hour and per worker-hour. rate = output/hours;
    per-worker = rate/crew_size."""
    o, h = float(output_quantity), float(labor_hours)
    if h == 0:
        return {"error": "labor_hours must be > 0"}
    rate = o / h
    per_worker = (rate / crew_size) if crew_size else rate
    rate_r = round(rate, 3)
    per_r = round(per_worker, 4)
    return {
        "rate_per_hour": rate_r,
        "rate_per_worker_hour": per_r,
        "crew_size": crew_size,
        "unit": "/hr",
        "value": rate_r,
        "rate_per_worker_hour_unit": "/worker-hr",
        "standard": "arithmetic (productivity)",
        "note": (f"Rate = output/hours = {o}/{h} = {rate:.3f}/hr; "
                 f"per worker = /{crew_size} = {per_worker:.4f}/worker-hr."),
    }
