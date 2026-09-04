"""Planning / CPM + PE assist calculators (additive library, gap-fill).

Critical-path float plus the Planning Engineer formula-sheet arithmetic:
progress/quantity, productivity → manhours → manpower → duration, unit
conversions, and material/concrete-mix helpers that refuse invented rates.
"""
from __future__ import annotations

from typing import Any, Dict, Optional


def critical_path_float(
    early_start: float,
    early_finish: float,
    late_start: float,
    late_finish: float,
) -> dict:
    """Total float TF = LS - ES = LF - EF; the activity is on the critical path
    when TF <= 0. (Free float needs the successor's ES; not computed here.)

    NEGATIVE float is critical too -- MORE critical, not less: a real 2013 P6
    baseline surfaced an activity at TF = -8.6 days (behind its constraint
    dates) that the old `TF == 0` test reported as NOT critical. On a live
    schedule, negative-float activities are exactly the ones driving the
    forecast delay; a planner asking "is this critical?" must never be told
    no about one of them."""
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


# ---------------------------------------------------------------------------
# PE formula sheet — progress / productivity / manpower / duration
# ---------------------------------------------------------------------------

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
) -> dict:
    """Qty → productivity → manpower → duration (PE formula sheet).

    Computes every output whose required inputs are present. Refuses when
    nothing can be computed — never invents a productivity rate.
    """
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
                "productivity_manpower_duration needs paired inputs for at "
                "least one formula (e.g. quantity_executed+man_hours, "
                "quantity+productivity, manpower+working_hours, "
                "remaining_manhours+available_hours, quantity+daily_production, "
                "or remaining_qty+remaining_days). No invented rates."
            ),
            "required_pairs": [
                ["quantity_executed", "man_hours"],
                ["quantity", "productivity"],
                ["manpower", "working_hours"],
                ["remaining_manhours", "available_hours"],
                ["quantity", "daily_production"],
                ["remaining_qty", "remaining_days"],
            ],
        }

    results["note"] = "; ".join(results["formulas_used"])
    return results


# ---------------------------------------------------------------------------
# PE unit conversions + material / concrete mix (caller-supplied ratios only)
# ---------------------------------------------------------------------------

# Factors express "how many *to_unit* in one *from_unit*" via a common SI base.
_LENGTH_TO_M = {
    "m": 1.0,
    "meter": 1.0,
    "metre": 1.0,
    "ft": 0.3048,
    "feet": 0.3048,
    "foot": 0.3048,
}
_AREA_TO_M2 = {
    "m2": 1.0,
    "m²": 1.0,
    "sqm": 1.0,
    "ft2": 0.09290304,
    "ft²": 0.09290304,
    "sqft": 0.09290304,
    "sf": 0.09290304,
}
_VOLUME_TO_M3 = {
    "m3": 1.0,
    "m³": 1.0,
    "cum": 1.0,
    "ft3": 0.028316846592,
    "ft³": 0.028316846592,
    "cuft": 0.028316846592,
    "cf": 0.028316846592,
}
_TIME_TO_HOUR = {
    "h": 1.0,
    "hr": 1.0,
    "hour": 1.0,
    "hours": 1.0,
    "d": 24.0,
    "day": 24.0,
    "days": 24.0,
}


def pe_unit_convert(value: float, from_unit: str, to_unit: str) -> dict:
    """Common PE unit conversions from the formula sheets (m↔ft, m²↔ft², m³↔ft³, day↔hour)."""
    if value is None:
        return {"error": "pe_unit_convert requires value.", "required": ["value", "from_unit", "to_unit"]}
    src = (from_unit or "").strip().lower()
    dst = (to_unit or "").strip().lower()
    if not src or not dst:
        return {
            "error": "pe_unit_convert requires from_unit and to_unit.",
            "required": ["value", "from_unit", "to_unit"],
            "supported": sorted(
                set(_LENGTH_TO_M) | set(_AREA_TO_M2) | set(_VOLUME_TO_M3) | set(_TIME_TO_HOUR)
            ),
        }

    tables = (
        ("length", _LENGTH_TO_M),
        ("area", _AREA_TO_M2),
        ("volume", _VOLUME_TO_M3),
        ("time", _TIME_TO_HOUR),
    )
    for kind, table in tables:
        if src in table and dst in table:
            base = float(value) * table[src]
            converted = base / table[dst]
            return {
                "value_in": float(value),
                "from_unit": src,
                "to_unit": dst,
                "value_out": round(converted, 8),
                "dimension": kind,
                "standard": "PE formula sheet (Conversion Formula)",
                "note": f"{value} {src} = {converted:.8g} {dst}",
                "formula_used": f"convert via SI base ({kind})",
            }
    return {
        "error": (
            f"Unsupported conversion {from_unit!r} → {to_unit!r}. "
            "Supported families: m↔ft, m2↔ft2, m3↔ft3, day↔hour."
        ),
        "supported": sorted(
            set(_LENGTH_TO_M) | set(_AREA_TO_M2) | set(_VOLUME_TO_M3) | set(_TIME_TO_HOUR)
        ),
    }


def material_consumption(
    quantity_of_work: Optional[float] = None,
    output_per_unit: Optional[float] = None,
    waste_factor: Optional[float] = None,
    waste_percent: Optional[float] = None,
) -> dict:
    """Material Required = Quantity of Work / Output per Unit (× waste).

    Requires quantity_of_work and output_per_unit. Waste is optional: pass
    ``waste_factor`` (e.g. 1.05) or ``waste_percent`` (e.g. 5 → factor 1.05).
    Refuses invented consumption rates.
    """
    if quantity_of_work is None or output_per_unit is None:
        return {
            "error": (
                "material_consumption requires quantity_of_work and "
                "output_per_unit — refuse without inputs."
            ),
            "required": ["quantity_of_work", "output_per_unit"],
            "optional": ["waste_factor", "waste_percent"],
        }
    qty = float(quantity_of_work)
    out_u = float(output_per_unit)
    if out_u <= 0:
        return {"error": "output_per_unit must be > 0.", "required": ["output_per_unit"]}
    base = qty / out_u
    factor = None
    if waste_factor is not None:
        factor = float(waste_factor)
    elif waste_percent is not None:
        factor = 1.0 + float(waste_percent) / 100.0
    material = base * factor if factor is not None else base
    formulas = ["Material Required = Quantity of Work / Output per Unit"]
    if factor is not None:
        formulas.append("Waste Factor = 1 + %Waste/100; Material × Waste Factor")
    return {
        "material_required": round(material, 6),
        "base_without_waste": round(base, 6),
        "waste_factor": factor,
        "formulas_used": formulas,
        "standard": "PE formula sheet (Material Consumption)",
        "note": "; ".join(formulas),
    }


def concrete_mix_proportions(
    wet_volume: Optional[float] = None,
    cement_parts: Optional[float] = None,
    sand_parts: Optional[float] = None,
    aggregate_parts: Optional[float] = None,
    dry_volume_factor: float = 1.54,
    waste_factor: Optional[float] = None,
) -> dict:
    """Concrete mix by caller-supplied cement:sand:aggregate proportions.

    Dry Volume = Wet Volume × dry_volume_factor (default 1.54 from PE sheets).
    Each constituent = Dry Volume × (parts / sum of parts). Refuses when wet
    volume or any mix part is missing — does not invent a design mix.
    """
    missing = [
        n for n, v in (
            ("wet_volume", wet_volume),
            ("cement_parts", cement_parts),
            ("sand_parts", sand_parts),
            ("aggregate_parts", aggregate_parts),
        )
        if v is None
    ]
    if missing:
        return {
            "error": (
                "concrete_mix_proportions requires wet_volume and "
                "cement_parts:sand_parts:aggregate_parts — refuse invented mixes."
            ),
            "required": ["wet_volume", "cement_parts", "sand_parts", "aggregate_parts"],
            "optional": ["dry_volume_factor", "waste_factor"],
            "missing": missing,
        }
    wet = float(wet_volume)
    c = float(cement_parts)
    s = float(sand_parts)
    a = float(aggregate_parts)
    if wet <= 0 or c < 0 or s < 0 or a < 0 or (c + s + a) <= 0:
        return {
            "error": "wet_volume must be > 0 and mix parts must sum to > 0.",
        }
    dry = wet * float(dry_volume_factor)
    if waste_factor is not None:
        dry = dry * float(waste_factor)
    total_parts = c + s + a
    cement_vol = dry * (c / total_parts)
    sand_vol = dry * (s / total_parts)
    agg_vol = dry * (a / total_parts)
    formulas = [
        f"Dry Volume = Wet Volume × {dry_volume_factor}",
        "Constituent = Dry Volume × (parts / total parts)",
    ]
    if waste_factor is not None:
        formulas.append(f"Dry Volume × waste_factor ({waste_factor})")
    return {
        "wet_volume": wet,
        "dry_volume": round(dry, 6),
        "dry_volume_factor": float(dry_volume_factor),
        "proportions": f"{c:g}:{s:g}:{a:g}",
        "cement_volume": round(cement_vol, 6),
        "sand_volume": round(sand_vol, 6),
        "aggregate_volume": round(agg_vol, 6),
        "units": "same volume unit as wet_volume",
        "formulas_used": formulas,
        "standard": "PE formula sheet (Concrete Mix — Manual)",
        "note": (
            f"1:{s/c if c else '?'}:{a/c if c else '?'} mix on wet={wet}; "
            f"dry={dry:.4g}; cement={cement_vol:.4g}, sand={sand_vol:.4g}, "
            f"agg={agg_vol:.4g}."
        ),
    }


ADDITIONAL_CALCULATORS = {
    "critical_path_float": critical_path_float,
    "progress_quantity": progress_quantity,
    "productivity_manpower_duration": productivity_manpower_duration,
    "pe_unit_convert": pe_unit_convert,
    "material_consumption": material_consumption,
    "concrete_mix_proportions": concrete_mix_proportions,
}
