"""Planning / CPM + PE assist calculators (additive library, gap-fill).

Critical-path float plus the Planning Engineer formula-sheet arithmetic:
progress/quantity, productivity → manhours → manpower → duration, unit
conversions, and material/concrete-mix helpers that refuse invented rates.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)


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


# Live A2-1: "remaining 3,400 m2 of plastering cost if productivity stays
# at 42 m2 per gang-day and a gang costs SAR 1,950 per day"
_PROD_COST_ASK_RE = re.compile(
    r"(?i)\b(cost|price|sar|aed|usd|gbp|eur).{0,80}\b(gang|crew)|"
    r"\b(gang|crew).{0,40}\b(cost|price|sar|aed|usd)"
)
_QTY_M2_RE = re.compile(r"(?i)(\d[\d,]*(?:\.\d+)?)\s*m2\b")
_PER_GANG_DAY_RE = re.compile(
    r"(?i)(\d[\d,]*(?:\.\d+)?)\s*m2\s+per\s+(?:gang|crew)[- ]?day"
)
_GANG_DAY_COST_RE = re.compile(
    r"(?i)(?:(?:gang|crew).{0,32}(?:costs?|at)\s*)?(?:SAR|AED|USD|GBP|EUR)\s*"
    r"(\d[\d,]*(?:\.\d+)?)\s*per\s+day"
    r"|(?:gang|crew).{0,24}(?:costs?|at)\s*(\d[\d,]*(?:\.\d+)?)"
)
_CURRENCY_RE = re.compile(r"\b(SAR|AED|USD|GBP|EUR)\b", re.IGNORECASE)


def looks_like_productivity_cost_ask(text: str) -> bool:
    """True when the operator asked for cost from gang-day productivity."""
    raw = text or ""
    if not _PROD_COST_ASK_RE.search(raw):
        return False
    return bool(_PER_GANG_DAY_RE.search(raw) or _QTY_M2_RE.search(raw))


def parse_productivity_cost_ask(
    text: str,
) -> Tuple[Optional[float], Optional[float], Optional[float], str]:
    """Return (quantity, daily_production, crew_cost_per_day, currency)."""
    raw = text or ""
    qty = daily = cost = None
    m_qty = _QTY_M2_RE.search(raw)
    if m_qty:
        qty = float(m_qty.group(1).replace(",", ""))
    m_daily = _PER_GANG_DAY_RE.search(raw)
    if m_daily:
        daily = float(m_daily.group(1).replace(",", ""))
    m_cost = _GANG_DAY_COST_RE.search(raw)
    if m_cost:
        raw_cost = next((g for g in m_cost.groups() if g), None)
        if raw_cost:
            cost = float(raw_cost.replace(",", ""))
    curr_m = _CURRENCY_RE.search(raw)
    currency = (curr_m.group(1) if curr_m else "SAR").upper()
    return qty, daily, cost, currency


def format_productivity_cost_line(inner: dict, currency: str = "SAR") -> str:
    """User-facing duration + cost line from a productivity result dict."""
    if not isinstance(inner, dict):
        return ""
    duration = inner.get("duration")
    cost = inner.get("total_cost")
    if duration is None:
        return ""
    try:
        dur_f = float(duration)
    except (TypeError, ValueError):
        logger.debug("duration is not numeric: %r", duration)
        return ""
    qty = inner.get("quantity")
    daily = inner.get("daily_production")
    crew = inner.get("crew_cost_per_day")
    bits = [f"Duration = {dur_f:.2f} gang-days"]
    if qty not in (None, "") and daily not in (None, ""):
        bits[0] = (
            f"Duration = {float(qty):g} / {float(daily):g} = {dur_f:.2f} gang-days"
        )
    if cost is not None:
        try:
            cost_f = float(cost)
        except (TypeError, ValueError):
            cost_f = None
        if cost_f is not None:
            crew_bit = f" × {currency} {float(crew):,.2f}" if crew not in (None, "") else ""
            bits.append(
                f"Cost = {dur_f:.2f}{crew_bit} = {currency} {cost_f:,.2f}"
            )
    return ". ".join(bits) + "."


def compose_productivity_cost_from_ask(text: str) -> dict | None:
    """Run duration × gang-day rate from the operator ask (live A2-1)."""
    if not looks_like_productivity_cost_ask(text):
        return None
    qty, daily, cost, currency = parse_productivity_cost_ask(text)
    if qty is None or daily is None or cost is None:
        return None
    from app.lib import construction_formulas as _cf
    env = _cf.run_calculation(
        "productivity_manpower_duration",
        {
            "quantity": qty,
            "daily_production": daily,
            "crew_cost_per_day": cost,
        },
    )
    if not isinstance(env, dict) or env.get("status") != "success":
        return None
    inner = env.get("result") if isinstance(env.get("result"), dict) else {}
    if inner.get("total_cost") is None or inner.get("duration") is None:
        return None
    # Echo inputs so the formatter can show qty / daily × rate.
    inner = dict(inner)
    inner.setdefault("quantity", qty)
    inner.setdefault("daily_production", daily)
    inner.setdefault("crew_cost_per_day", cost)
    line = format_productivity_cost_line(inner, currency)
    if not line or currency not in line:
        return None
    return {
        "duration": inner["duration"],
        "total_cost": inner["total_cost"],
        "currency": currency,
        "line": line,
        "envelope": env,
        "result": inner,
    }


ADDITIONAL_CALCULATORS = {
    "critical_path_float": critical_path_float,
    "progress_quantity": progress_quantity,
    "productivity_manpower_duration": productivity_manpower_duration,
    "pe_unit_convert": pe_unit_convert,
    "material_consumption": material_consumption,
    "concrete_mix_proportions": concrete_mix_proportions,
}
