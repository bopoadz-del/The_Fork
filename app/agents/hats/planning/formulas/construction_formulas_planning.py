"""Formulas from app.lib.construction_formulas_planning owned by the planning hat.

Moved unchanged by scripts/move_formulas.py (F-DRIVER Phase A).
"""
from __future__ import annotations
from app.lib.formula_registry import Param, formula
import logging
from typing import Any, Dict, Optional


logger = logging.getLogger(__name__)

@formula(
    owner='planning',
    display_name='Total float and criticality',
    description='Total float of an activity from its early and late dates, and whether it is critical.',
    inputs={
        'early_start': Param('-', 0, 100000),
        'early_finish': Param('-', 0, 100000),
        'late_start': Param('-', 0, 100000),
        'late_finish': Param('-', 0, 100000),
    },
    outputs={'total_float': '-', 'is_critical': '-', 'consistency_check_lf_minus_ef': '-'},
)
def critical_path_float(
    early_start: float,
    early_finish: float,
    late_start: float,
    late_finish: float,
) -> dict:
    """Total float TF = LS - ES = LF - EF; the activity is on the critical path
    when TF <= 0. (Free float needs the successor's ES; not computed here.)

    NEGATIVE float is critical too -- MORE critical, not less:     a real 2013 P6
    baseline surfaced an activity at TF = -8.6 days (behind its constraint
    dates) that the old `TF == 0` test reported as NOT critical. On a live
    schedule, negative-float activities are exactly the ones driving the
    forecast delay; a planner asking "is this critical?" must never be told
    no about one of them."""
    if float(early_finish) < float(early_start) or float(late_finish) < float(late_start):
        return {"error": "finish dates must be >= the matching start dates."}
    es, ef = float(early_start), float(early_finish)
    ls, lf = float(late_start), float(late_finish)
    tf = ls - es
    tf_check = lf - ef
    on_critical = tf < 1e-9
    behind = tf < -1e-9
    return {
        "total_float": round(tf, 3),
        "is_critical": on_critical,
        "consistency_check_lf_minus_ef": round(tf_check, 3),
        "standard": "CPM (PMBOK / planning)",
        "note": (f"TF = LS - ES = {ls} - {es} = {tf:.1f} (= LF - EF = {lf} - {ef} "
                 f"= {tf_check:.1f}); "
                 + ("NEGATIVE float - critical and behind schedule."
                    if behind else
                    ("critical" if on_critical else "has float") + ".")),
    }

@formula(
    owner='planning',
    display_name='Progress and remaining quantity',
    description='Planned and actual percent complete, remaining quantity and progress variance.',
    inputs={
        'total_qty': Param('-', 0, 1e12, label='total quantity'),
        'planned_qty': Param('-', 0, 1e12, label='planned quantity'),
        'actual_qty': Param('-', 0, 1e12, label='actual quantity'),
    },
    outputs={'planned_percent': '%', 'actual_percent': '%', 'planned_qty': '-', 'actual_qty': '-', 'remaining_qty': '-', 'progress_variance_percent': '%'},
)
def progress_quantity(
    total_qty: float,
    planned_qty: Optional[float] = None,
    actual_qty: Optional[float] = None,
) -> dict:
    """Progress & quantity sheet: Planned %, Actual %, Remaining Qty, Progress Variance.

    Requires ``total_qty`` > 0. Planned and/or actual quantities are optional —
    only the outputs whose inputs are present are returned. Never invents a qty.
    """
    total = float(total_qty)
    if total <= 0:
        return {
            "error": "progress_quantity requires total_qty > 0 — refuse empty total.",
            "required": ["total_qty"],
            "optional": ["planned_qty", "actual_qty"],
        }
    out: Dict[str, Any] = {
        "total_qty": total,
        "units": "same as input quantities",
        "formulas_used": [],
        "standard": "PE formula sheet (Progress & Quantity)",
    }
    if planned_qty is not None:
        pq = float(planned_qty)
        planned_pct = (pq / total) * 100.0
        out["planned_qty"] = pq
        out["planned_percent"] = round(planned_pct, 3)
        out["formulas_used"].append("Planned % = Planned Qty / Total Qty × 100")
    if actual_qty is not None:
        aq = float(actual_qty)
        actual_pct = (aq / total) * 100.0
        remaining = total - aq
        out["actual_qty"] = aq
        out["actual_percent"] = round(actual_pct, 3)
        out["remaining_qty"] = round(remaining, 6)
        out["formulas_used"].append("Actual % = Actual Qty / Total Qty × 100")
        out["formulas_used"].append("Remaining Qty = Total Qty − Actual Qty")
    if planned_qty is not None and actual_qty is not None:
        out["progress_variance_percent"] = round(
            out["actual_percent"] - out["planned_percent"], 3
        )
        out["formulas_used"].append("Progress Variance = Actual % − Planned %")
    if planned_qty is None and actual_qty is None:
        return {
            "error": (
                "progress_quantity needs planned_qty and/or actual_qty in "
                "addition to total_qty — no invented quantities."
            ),
            "required": ["total_qty", "planned_qty|actual_qty"],
        }
    out["note"] = "; ".join(out["formulas_used"])
    return out

def _crew_cost_from_aliases(
    crew_cost_per_day: Optional[float],
    day_rate: Optional[float],
    gang_cost_per_day: Optional[float],
) -> Optional[float]:
    for raw in (crew_cost_per_day, day_rate, gang_cost_per_day):
        if raw is None or raw == "":
            continue
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue
        if value > 0:
            return value
    return None

def _daily_production_from_rate_alias(
    *,
    productivity_rate: Optional[float],
    rate_unit: Optional[str],
    crew_cost: Optional[float],
) -> Optional[float]:
    """Live A2-1: ``productivity_rate`` + gang-day unit (or a crew day-rate).

    ``productivity`` on this calculator is qty / man-hour. A rate quoted
    per gang-day / crew-day is ``daily_production``, not that field.
    """
    if productivity_rate is None or productivity_rate == "":
        return None
    try:
        rate = float(productivity_rate)
    except (TypeError, ValueError):
        logger.debug("productivity_rate is not numeric: %r", productivity_rate)
        return None
    if rate <= 0:
        return None
    unit = str(rate_unit or "").lower()
    per_day = any(
        token in unit
        for token in ("gang-day", "gang day", "crew-day", "crew day", "per day", "/day")
    )
    if per_day or crew_cost is not None or not unit:
        # No unit + crew cost (or a bare rate next to a day-cost) is the
        # live plastering pairing. A unit that names hours stays on the
        # man-hour path via ``productivity``.
        if "hour" in unit or "/h" in unit:
            return None
        return rate
    return None

@formula(
    owner='planning',
    display_name='Quantity, productivity, manpower and duration',
    description='Links quantity, productivity, manpower and duration: any one from the others.',
    inputs={
        'quantity_executed': Param('-', 0, 1e12),
        'man_hours': Param('man-hour', 0, 1e8),
        'quantity': Param('-', 0, 1e12),
        'productivity': Param('-', 0, 1e9),
        'manpower': Param('-', 0, 1e6),
        'working_hours': Param('h', 0, 1e8),
        'remaining_manhours': Param('man-hour', 0, 1e10, label='remaining man-hours'),
        'available_hours': Param('h', 0, 1e8),
        'daily_production': Param('-', 0, 1e9),
        'remaining_qty': Param('-', 0, 1e12, label='remaining quantity'),
        'remaining_days': Param('days', 0, 36500, label='remaining duration'),
        'productivity_rate': Param('-', 0, 1e9),
        'rate_unit': Param('-'),
        'crew_cost_per_day': Param('currency/day', 0, 1e9),
        'day_rate': Param('-', 0, 1e9),
        'gang_cost_per_day': Param('currency/day', 0, 1e9),
    },
    outputs={'productivity': 'per productivity_units', 'required_manpower': '-', 'manhours_required': 'h', 'daily_required_production': '-', 'duration': 'duration_units', 'total_cost': 'currency'},
)
def productivity_manpower_duration(
    *,
    quantity_executed: Optional[float] = None,
    man_hours: Optional[float] = None,
    quantity: Optional[float] = None,
    productivity: Optional[float] = None,
    manpower: Optional[float] = None,
    working_hours: Optional[float] = None,
    remaining_manhours: Optional[float] = None,
    available_hours: Optional[float] = None,
    daily_production: Optional[float] = None,
    remaining_qty: Optional[float] = None,
    remaining_days: Optional[float] = None,
    productivity_rate: Optional[float] = None,
    rate_unit: Optional[str] = None,
    crew_cost_per_day: Optional[float] = None,
    day_rate: Optional[float] = None,
    gang_cost_per_day: Optional[float] = None,
) -> dict:
    """Qty → productivity → manpower → duration (PE formula sheet).

    Computes every output whose required inputs are present. Refuses when
    nothing can be computed — never invents a productivity rate.

    Live A2-1: ``productivity_rate`` (m2 per gang-day) pairs with
    ``quantity`` as daily production, and ``crew_cost_per_day`` prices
    the resulting duration. Those aliases were previously stripped, so
    the first live call failed "paired inputs" and the retry shipped a
    formula-only note.
    """
    crew_cost = _crew_cost_from_aliases(
        crew_cost_per_day, day_rate, gang_cost_per_day,
    )
    if daily_production is None:
        aliased_daily = _daily_production_from_rate_alias(
            productivity_rate=productivity_rate,
            rate_unit=rate_unit,
            crew_cost=crew_cost,
        )
        if aliased_daily is not None:
            daily_production = aliased_daily
    if productivity is None and productivity_rate is not None and daily_production is None:
        try:
            prod_alias = float(productivity_rate)
        except (TypeError, ValueError):
            prod_alias = None
        if prod_alias is not None and prod_alias > 0:
            productivity = prod_alias

    results: Dict[str, Any] = {
        "formulas_used": [],
        "standard": "PE formula sheet (Productivity / Manpower / Duration)",
    }
    computed = False

    # Productivity = Quantity Executed / Man-hours
    if quantity_executed is not None and man_hours is not None:
        mh = float(man_hours)
        if mh <= 0:
            return {
                "error": "man_hours must be > 0 to compute productivity.",
                "required": ["quantity_executed", "man_hours"],
            }
        prod = float(quantity_executed) / mh
        results["productivity"] = round(prod, 6)
        results["productivity_units"] = "qty / man-hour"
        results["formulas_used"].append(
            "Productivity = Quantity Executed / Man-hours"
        )
        computed = True
        if productivity is None:
            productivity = prod

    # Manhours Required = Quantity / Productivity
    if quantity is not None and productivity is not None:
        prod = float(productivity)
        if prod <= 0:
            return {
                "error": "productivity must be > 0 to compute manhours required.",
                "required": ["quantity", "productivity"],
            }
        mh_req = float(quantity) / prod
        results["manhours_required"] = round(mh_req, 4)
        results["formulas_used"].append(
            "Manhours Required = Quantity / Productivity"
        )
        computed = True
        if remaining_manhours is None:
            remaining_manhours = mh_req

    # Manhours = Manpower × Working Hours
    if manpower is not None and working_hours is not None:
        mh = float(manpower) * float(working_hours)
        results["manhours"] = round(mh, 4)
        results["formulas_used"].append(
            "Manhours = Manpower × Working Hours"
        )
        computed = True

    # Required Manpower = Remaining Manhours / Available Hours
    if remaining_manhours is not None and available_hours is not None:
        avail = float(available_hours)
        if avail <= 0:
            return {
                "error": "available_hours must be > 0 to compute required manpower.",
                "required": ["remaining_manhours", "available_hours"],
            }
        req_mp = float(remaining_manhours) / avail
        results["required_manpower"] = round(req_mp, 4)
        results["formulas_used"].append(
            "Required Manpower = Remaining Manhours / Available Hours"
        )
        computed = True

    # Duration = Quantity / Daily Production
    if quantity is not None and daily_production is not None:
        daily = float(daily_production)
        if daily <= 0:
            return {
                "error": "daily_production must be > 0 to compute duration.",
                "required": ["quantity", "daily_production"],
            }
        dur = float(quantity) / daily
        results["duration"] = round(dur, 4)
        results["duration_units"] = "days (same period as daily_production)"
        results["formulas_used"].append(
            "Duration = Quantity / Daily Production"
        )
        computed = True
        if crew_cost is not None:
            cost = dur * crew_cost
            results["total_cost"] = round(cost, 2)
            results["crew_cost_per_day"] = round(crew_cost, 2)
            results["formulas_used"].append(
                "Cost = Duration × Crew Cost per Day"
            )

    # Daily Required Production = Remaining Qty / Remaining Days
    if remaining_qty is not None and remaining_days is not None:
        days = float(remaining_days)
        if days <= 0:
            return {
                "error": "remaining_days must be > 0 to compute daily required production.",
                "required": ["remaining_qty", "remaining_days"],
            }
        daily_req = float(remaining_qty) / days
        results["daily_required_production"] = round(daily_req, 6)
        results["formulas_used"].append(
            "Daily Required Production = Remaining Qty / Remaining Days"
        )
        computed = True

    if not computed:
        return {
            "error": (
                "productivity_manpower_duration needs paired inputs (no invented "
                "rates): quantity_executed (qty) + man_hours (h); "
                "quantity (qty) + productivity (qty/h); "
                "quantity (qty) + daily_production (qty/day); "
                "quantity (qty) + productivity_rate (qty/gang-day) + "
                "crew_cost_per_day (currency/day); "
                "manpower (persons) + working_hours (h); "
                "remaining_manhours (h) + available_hours (h); "
                "or remaining_qty (qty) + remaining_days (days)."
            ),
            "required_pairs": [
                ["quantity_executed", "man_hours"],
                ["quantity", "productivity"],
                ["manpower", "working_hours"],
                ["remaining_manhours", "available_hours"],
                ["quantity", "daily_production"],
                ["remaining_qty", "remaining_days"],
                ["quantity", "productivity_rate", "crew_cost_per_day"],
            ],
        }

    note_parts = list(results["formulas_used"])
    if results.get("duration") is not None and quantity is not None and daily_production is not None:
        note_parts.append(
            f"{float(quantity):g} / {float(daily_production):g} = "
            f"{results['duration']:g} days"
        )
    if results.get("total_cost") is not None and results.get("duration") is not None:
        note_parts.append(
            f"{results['duration']:g} × {results['crew_cost_per_day']:g} = "
            f"{results['total_cost']:g}"
        )
    results["note"] = "; ".join(note_parts)
    return results
