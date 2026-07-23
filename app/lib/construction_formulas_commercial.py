"""Commercial / PM arithmetic calculators (additive library, gap-fill).

Deterministic arithmetic — currency/units are whatever the caller passes
(SAR, USD, m2, sf). Code-agnostic. Rates and quantities are parameters.
"""
from __future__ import annotations


def roi_calculator(gain: float, cost: float) -> dict:
    """Return on investment: ROI% = (gain - cost) / cost * 100."""
    g, c = float(gain), float(cost)
    net = g - c
    roi = (net / c * 100.0) if c else 0.0
    return {
        "net_profit": round(net, 2),
        "roi_percent": round(roi, 2),
        "standard": "arithmetic",
        "note": f"ROI = (gain - cost)/cost*100 = ({g} - {c})/{c}*100 = {roi:.2f}%.",
    }


def unit_cost_total(quantity: float, unit_rate: float) -> dict:
    """Total = quantity * unit_rate (a line-item extension)."""
    q, r = float(quantity), float(unit_rate)
    total = q * r
    return {
        "total_cost": round(total, 2),
        "standard": "arithmetic (BOQ line item)",
        "note": f"Total = qty * rate = {q} * {r} = {total:.2f}.",
    }


def cost_per_area(total_cost: float, area: float, area_unit: str = "m2") -> dict:
    """Unit area cost = total_cost / area (per m2 or per sf, caller's unit)."""
    t, a = float(total_cost), float(area)
    rate = (t / a) if a else 0.0
    return {
        "cost_per_area": round(rate, 2),
        "area_unit": area_unit,
        "standard": "arithmetic",
        "note": f"Cost/{area_unit} = {t}/{a} = {rate:.2f} per {area_unit}.",
    }


def productivity_rate(output_quantity: float, labor_hours: float, crew_size: int = 1) -> dict:
    """Output per labour-hour and per worker-hour. rate = output/hours;
    per-worker = rate/crew_size."""
    o, h = float(output_quantity), float(labor_hours)
    rate = (o / h) if h else 0.0
    per_worker = (rate / crew_size) if crew_size else rate
    return {
        "rate_per_hour": round(rate, 3),
        "rate_per_worker_hour": round(per_worker, 4),
        "crew_size": crew_size,
        "standard": "arithmetic (productivity)",
        "note": (f"Rate = output/hours = {o}/{h} = {rate:.3f}/hr; "
                 f"per worker = /{crew_size} = {per_worker:.4f}/worker-hr."),
    }


ADDITIONAL_CALCULATORS = {
    "roi_calculator": roi_calculator,
    "unit_cost_total": unit_cost_total,
    "cost_per_area": cost_per_area,
    "productivity_rate": productivity_rate,
}
