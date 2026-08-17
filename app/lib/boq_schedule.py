"""Derive a construction schedule from a priced BOQ — quantities become man-hours.

Why this exists
---------------
``/export/schedule-from-document`` accepted a BOQ and produced a schedule whose
activities came from a canned per-project-type template: measured 2026-08-17,
a BOQ listing structural steel truss, composite cladding, chilled-water piping
and epoxy flooring yielded 46 activities of which ZERO referenced that scope.
The document route mines equipment lead times and target milestones; a BOQ
carries neither, so it contributed nothing.

A BOQ is the one document that states the actual scope WITH quantities, which
is exactly what a duration needs.

Man-hours are the unit
----------------------
Planning is done in MAN-HOURS, not "crew-days" (operator correction,
2026-08-17: "everything is in manhour"). A norm of 0.8 mh/m2 is a property of
the work; a crew-day figure silently bundles crew size and shift length into
one number, so it cannot be compared between projects, cannot be re-planned
when the crew changes, and cannot produce an honest manpower histogram. So:

    total_manhours  = quantity x manhours_per_unit
    duration_days   = ceil(total_manhours / (crew_size x hours_per_day))

The histogram and S-curve then fall out of the same man-hours rather than
being derived separately — the programme and the resource curve agree by
construction, which is the whole point of a cost/resource-loaded schedule.

Process, not facts
------------------
Man-hour norms are PROJECT variables — they move with crew, site, height,
access and repetition — so nothing here carries a built-in rate. They are
supplied by the caller, sourced in order: the project's own records, the
project facts store, a cited published reference, or the operator. A category
with no supplied norm is REFUSED by name, because a programme resting on an
invented output looks exactly like a real one until it slips.

Output shape matches ``generate_wbs`` activities exactly (id/name/duration_days/
predecessors/resources/wbs_phase), so the existing CPM, cost-loading bridge and
Excel writers consume it unchanged.
"""

from __future__ import annotations

import math
import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

from app.lib.boq_pricing import categorize

# Standard shift. Overridable per call — some projects run 10s, Ramadan runs 6.
DEFAULT_HOURS_PER_DAY = 8.0
DEFAULT_CREW_SIZE = 4

# Construction order for the categorizer's work packages. A schedule is a
# sequence claim, and this is the only place that claim is made.
CONSTRUCTION_SEQUENCE: Tuple[str, ...] = (
    "Preliminaries/General",
    "Demolition",
    "Earthworks/Excavation",
    "Piling/Foundations",
    "Formwork",
    "Reinforcement",
    "Concrete",
    "Structural Steel",
    "Masonry/Blockwork",
    "Waterproofing/Insulation",
    "Windows/Doors/Facade",
    "Mechanical/HVAC (MEP)",
    "Electrical (MEP)",
    "Fire Protection",
    "Pipework/Drainage",
    "Sanitary/Accessories",
    "Finishes",
    "Roads/Paving",
    "Landscape/Softscape",
    "Other/Uncategorized",
)

_TRADE_OF = {
    "Earthworks/Excavation": "earthworks",
    "Demolition": "demolition",
    "Piling/Foundations": "piling",
    "Concrete": "concrete",
    "Reinforcement": "steelfixing",
    "Formwork": "carpentry",
    "Structural Steel": "steel_erection",
    "Masonry/Blockwork": "masonry",
    "Pipework/Drainage": "plumbing",
    "Roads/Paving": "paving",
    "Windows/Doors/Facade": "facade",
    "Finishes": "finishes",
    "Waterproofing/Insulation": "waterproofing",
    "Electrical (MEP)": "electrical",
    "Mechanical/HVAC (MEP)": "hvac",
    "Fire Protection": "fire",
    "Sanitary/Accessories": "sanitary",
    "Landscape/Softscape": "landscaping",
    "Preliminaries/General": "management",
    "Other/Uncategorized": "general",
}

# Substructure markers. The pricing categorizer answers "what trade is this?",
# never "which part of the building?" — "Reinforced concrete C40 to raft
# foundation" and "...to columns" are both plain Concrete. Scheduled as one
# package in BOQ order, a raft listed second lands AFTER the columns it
# carries: a wrong programme, not a cosmetic one.
#
# Plurals are the norm in a real BOQ ("to foundations", "pile caps"), and a
# \b-anchored singular silently fails on every one of them.
_SUBSTRUCTURE_RX = re.compile(
    r"\b(foundations?|footings?|pile[\s-]?caps?|piling|rafts?|blinding|"
    r"substructure|ground\s+beams?|pad\s+bases?|tie\s+beams?|lean\s+concrete|"
    r"underground|below\s+ground)\b",
    re.IGNORECASE,
)

# Trades that recur between substructure and superstructure and so need
# splitting; finishes and facade have no substructure phase.
_STAGED_CATEGORIES = frozenset({
    "Concrete", "Reinforcement", "Formwork", "Waterproofing/Insulation",
    "Masonry/Blockwork",
})

# Categories that ARE substructure/enabling by definition — no keyword needed.
# Live 2026-08-17: without Earthworks here, "Bulk excavation to reduced level"
# sorted into the superstructure block and scheduled on day 32, AFTER the
# foundations it digs for. Demolition and site clearance are the same class:
# they precede everything, always.
_ALWAYS_SUBSTRUCTURE = frozenset({
    "Piling/Foundations", "Earthworks/Excavation", "Demolition",
})

# Enabling operations that precede the reinforcement they are poured under.
# Blinding is categorised as Concrete (it is), but a blinding layer is struck
# before rebar is fixed, not after it — trade order alone puts it last.
_ENABLING_RX = re.compile(r"(blinding|lean\s+concrete|sub[\s-]?base|"
                          r"levelling\s+course)", re.IGNORECASE)

# Vertical/applied-after work inside the substructure: tanking to retaining
# walls, protection boards to raft sides. These follow the pour, unlike the
# horizontal membrane below it.
_AFTER_POUR_RX = re.compile(
    r"(retaining\s+wall|to\s+walls?\b|wall\s+face|vertical|"
    r"tanking\s+to\s+(?:walls?|sides?)|sides?\s+of|external\s+face|"
    r"protection\s+board)",
    re.IGNORECASE,
)

SUBSTRUCTURE = "substructure"
SUPERSTRUCTURE = "superstructure"


def element_stage(description: Any, category: str) -> str:
    """``substructure`` for below-ground work, else ``superstructure``."""
    if category in _ALWAYS_SUBSTRUCTURE:
        return SUBSTRUCTURE
    if category in _STAGED_CATEGORIES and _SUBSTRUCTURE_RX.search(str(description)):
        return SUBSTRUCTURE
    return SUPERSTRUCTURE


class MissingProductivity(Exception):
    """No man-hour norm was supplied for a work category present in the BOQ.

    Carries the categories and their units so the caller asks a precise
    question ("what is your blockwork norm, in man-hours per m2?") instead of
    reporting a generic failure.
    """

    def __init__(self, missing: Dict[str, str]) -> None:
        self.missing = missing
        detail = "; ".join(f"{cat} (man-hours per {unit})"
                           for cat, unit in sorted(missing.items()))
        super().__init__(
            "No man-hour norm supplied for: " + detail +
            ". Durations are derived from quantity x man-hours per unit, so "
            "these cannot be scheduled until the norms are given — supply them "
            "from the project's own records, a cited published norm, or the "
            "operator."
        )


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(text).lower()).strip("_")[:40] or "item"


def _qty(item: Dict[str, Any]) -> float:
    for key in ("quantity", "total_quantity", "qty"):
        v = item.get(key)
        if isinstance(v, (int, float)) and v == v:
            return float(v)
        if isinstance(v, str):
            try:
                return float(re.sub(r"[,\s]", "", v))
            except ValueError:
                continue
    return 0.0


def manhours(quantity: float, manhours_per_unit: float) -> float:
    """Total man-hours for a measured quantity."""
    if manhours_per_unit <= 0:
        raise ValueError("manhours_per_unit must be > 0")
    return quantity * manhours_per_unit


def duration_days(
    total_manhours: float,
    crew_size: int = DEFAULT_CREW_SIZE,
    hours_per_day: float = DEFAULT_HOURS_PER_DAY,
) -> int:
    """Working days to burn ``total_manhours`` with ``crew_size`` on shift.

    Always at least one day: a measurable item still occupies the crew for a
    shift, and a zero-day activity makes the CPM degenerate.
    """
    if crew_size <= 0:
        raise ValueError("crew_size must be > 0")
    if hours_per_day <= 0:
        raise ValueError("hours_per_day must be > 0")
    return max(1, math.ceil(total_manhours / (crew_size * hours_per_day)))


def from_daily_output(output_per_man_day: float,
                      hours_per_day: float = DEFAULT_HOURS_PER_DAY) -> float:
    """Convert a published 'units per tradesman per day' norm to man-hours/unit.

    Estimating handbooks publish daily outputs; planning needs man-hours. This
    is the one conversion, stated once, so a cited reference can be used
    without re-deriving it at each call site.
    """
    if output_per_man_day <= 0:
        raise ValueError("output_per_man_day must be > 0")
    return hours_per_day / output_per_man_day


def group_by_category(
    line_items: Iterable[Dict[str, Any]],
    overrides: Optional[Dict[str, str]] = None,
) -> Dict[str, List[Dict[str, Any]]]:
    """Bucket BOQ lines into work packages using the shipped categorizer.

    ``categorize`` is shared with pricing and matches the EARLIEST pattern in
    the description, which is right for a rate lookup but can misplace a line
    for sequencing: "Chilled water piping DN200 insulated" classifies as
    Waterproofing/Insulation on the word "insulated". Rather than fork that
    taxonomy (and quietly change how rates resolve), a planner corrects
    individual lines through ``overrides``, keyed by ``item_key`` or exact
    description.
    """
    overrides = overrides or {}
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for item in line_items or []:
        desc = item.get("description") or item.get("item_key") or ""
        cat = (overrides.get(item.get("item_key") or "")
               or overrides.get(desc)
               or categorize(desc))
        grouped.setdefault(cat, []).append(item)
    return grouped


def uncategorized(line_items: Iterable[Dict[str, Any]],
                  overrides: Optional[Dict[str, str]] = None) -> List[str]:
    """Descriptions the classifier could not place, so they stay VISIBLE.

    An unrecognised line is still scheduled (Other package, last), but a
    planner must see what the taxonomy did not understand rather than find it
    at the end of the programme.
    """
    return [
        (i.get("description") or i.get("item_key") or "")
        for i in group_by_category(line_items, overrides).get("Other/Uncategorized", [])
    ]


def _norm_desc(text: Any) -> str:
    """Collapse a description to its work identity for aggregation."""
    return re.sub(r"\s+", " ", str(text or "").strip().lower())


def aggregate_lines(items: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Merge BOQ lines describing the SAME work into one measured item.

    A real BOQ measures the same work many times — per floor, per zone, per
    block — so scheduling one activity per line is not a programme: the live
    run of a 640-line BOQ produced 640 chained activities and an 18,400-day
    (73-year) duration, because 160 repeats of "RC C40 to columns" each became
    their own sequential 6-day task. Planners schedule WORK PACKAGES: the
    quantities add up, the activity is one.

    Quantities and costs are summed; ``line_count`` records how many BOQ lines
    the activity represents, so the aggregation stays visible rather than
    looking like a shorter BOQ.
    """
    merged: Dict[str, Dict[str, Any]] = {}
    for item in items or []:
        key = _norm_desc(item.get("description") or item.get("item_key"))
        cur = merged.get(key)
        if cur is None:
            cur = dict(item)
            cur["quantity"] = _qty(item)
            cur["line_count"] = 1
            cur["total_cost"] = float(item.get("total_cost") or 0)
            merged[key] = cur
            continue
        cur["quantity"] += _qty(item)
        cur["line_count"] += 1
        cur["total_cost"] += float(item.get("total_cost") or 0)
    return list(merged.values())


def activities_from_boq(
    line_items: Iterable[Dict[str, Any]],
    *,
    manhours_per_unit: Dict[str, float],
    crew_size: Optional[Dict[str, int]] = None,
    default_crew_size: int = DEFAULT_CREW_SIZE,
    hours_per_day: float = DEFAULT_HOURS_PER_DAY,
    min_quantity: float = 0.0,
    category_overrides: Optional[Dict[str, str]] = None,
    aggregate: bool = True,
) -> List[Dict[str, Any]]:
    """Turn priced BOQ lines into CPM-ready, man-hour-loaded activities.

    ``manhours_per_unit`` maps a work category to its norm IN THAT CATEGORY'S
    OWN UNIT (mh/m3 for concrete, mh/m2 for blockwork, mh/kg for rebar). Every
    category present in the BOQ must appear or :class:`MissingProductivity` is
    raised naming the gaps — nothing is defaulted.

    ``crew_size`` sets heads per category (default ``default_crew_size``);
    duration follows from man-hours and crew, so re-planning with a bigger
    gang is a crew change, not a new norm.

    Sequencing: substructure packages first, then superstructure, each in
    :data:`CONSTRUCTION_SEQUENCE` order; the first activity of a package
    depends on the last of the previous one (finish-to-start), and lines
    inside a package run back-to-back because they share a crew. Overlap is a
    planner's decision, not a derivation.
    """
    crew_size = crew_size or {}
    # Same work measured many times over is ONE activity (see aggregate_lines):
    # without this a 640-line BOQ becomes 640 chained tasks and a 73-year
    # programme. Pass aggregate=False for a line-by-line programme.
    line_items = aggregate_lines(line_items) if aggregate else list(line_items or [])
    grouped = group_by_category(line_items, category_overrides)
    grouped = {cat: [i for i in items if _qty(i) > min_quantity]
               for cat, items in grouped.items()}
    grouped = {cat: items for cat, items in grouped.items() if items}

    missing = {
        cat: (items[0].get("unit") or "unit")
        for cat, items in grouped.items()
        if not manhours_per_unit.get(cat)
    }
    if missing:
        raise MissingProductivity(missing)

    # Package key carries an ORDER RANK beside the category so enabling work
    # can be sequenced apart from its trade while still using that trade's
    # norm, crew and phase. Blinding is Concrete (it is), but it is struck
    # BEFORE the rebar fixed on top of it; ranking by trade alone scheduled it
    # after (live 2026-08-17).
    seq_index = {c: i for i, c in enumerate(CONSTRUCTION_SEQUENCE)}
    _enabling_rank = seq_index["Piling/Foundations"] + 0.5
    # Horizontal substructure waterproofing goes ON the blinding and UNDER the
    # raft — between the blinding and the steel fixed on top of it. Ranking it
    # by trade (Waterproofing sits after Concrete) scheduled the membrane after
    # the pour it is meant to protect (operator, 2026-08-17: "waterproofing is
    # after blinding and after raft"). Vertical/applied-after work — tanking to
    # retaining walls, protection boards to raft sides — keeps the post-pour
    # slot, which is the other half of that sentence.
    _membrane_rank = seq_index["Piling/Foundations"] + 0.7
    _after_pour_rank = seq_index["Concrete"] + 0.5

    staged: Dict[tuple, List[Dict[str, Any]]] = {}
    for cat, items in grouped.items():
        for item in items:
            desc = str(item.get("description") or item.get("item_key") or "")
            stage = element_stage(desc, cat)
            if _ENABLING_RX.search(desc):
                rank = _enabling_rank
            elif (stage == SUBSTRUCTURE
                  and cat == "Waterproofing/Insulation"):
                rank = (_after_pour_rank if _AFTER_POUR_RX.search(desc)
                        else _membrane_rank)
            else:
                rank = float(seq_index.get(cat, len(seq_index)))
            staged.setdefault((stage, rank, cat), []).append(item)

    ordered = sorted(
        staged, key=lambda k: (0 if k[0] == SUBSTRUCTURE else 1, k[1], k[2]),
    )

    activities: List[Dict[str, Any]] = []
    prev_package_tail: Optional[str] = None
    for p_idx, key in enumerate(ordered, start=1):
        stage, _rank, cat = key
        norm = float(manhours_per_unit[cat])
        crew = int(crew_size.get(cat, default_crew_size))
        trade = _TRADE_OF.get(cat, "general")
        phase = _slug(cat) if stage == SUPERSTRUCTURE else f"{_slug(cat)}_substructure"
        prev_in_package: Optional[str] = None
        for a_idx, item in enumerate(staged[key], start=1):
            qty = _qty(item)
            mh = manhours(qty, norm)
            act_id = f"{p_idx}.{a_idx}"
            preds = [prev_in_package] if prev_in_package else (
                [prev_package_tail] if prev_package_tail else [])
            activities.append({
                "id": act_id,
                "code": act_id,
                "name": (item.get("description") or item.get("item_key") or "Item")[:120],
                "duration_days": duration_days(mh, crew, hours_per_day),
                "predecessors": preds,
                # one entry per head: the manpower histogram sums these
                "resources": [trade] * crew,
                "crew_size": crew,
                "total_manhours": round(mh, 1),
                "wbs_phase": phase,
                # provenance — every number traceable to a BOQ line and a norm
                "boq": {
                    "item_key": item.get("item_key"),
                    "quantity": qty,
                    "unit": item.get("unit"),
                    "manhours_per_unit": norm,
                    "total_manhours": round(mh, 1),
                    "crew_size": crew,
                    "hours_per_day": hours_per_day,
                    "category": cat,
                    "stage": stage,
                    "line_count": int(item.get("line_count") or 1),
                    "unit_cost": item.get("unit_cost"),
                    "total_cost": item.get("total_cost"),
                },
            })
            prev_in_package = act_id
        prev_package_tail = prev_in_package
    return activities


def total_manhours(activities: Iterable[Dict[str, Any]]) -> float:
    """Programme man-hours — the figure the histogram and S-curve integrate to."""
    return round(sum(float(a.get("total_manhours") or 0) for a in activities), 1)


def schedule_basis(manhours_per_unit: Dict[str, float],
                   crew_size: Optional[Dict[str, int]] = None,
                   hours_per_day: float = DEFAULT_HOURS_PER_DAY,
                   default_crew_size: int = DEFAULT_CREW_SIZE) -> List[str]:
    """Human-readable basis — what every duration rests on."""
    crew_size = crew_size or {}
    out = [
        f"Man-hours = quantity x norm; duration = man-hours / (crew x "
        f"{hours_per_day:g} h shift), rounded up to whole working days "
        f"(minimum 1).",
    ]
    for cat in sorted(manhours_per_unit):
        crew = crew_size.get(cat, default_crew_size)
        out.append(f"{cat}: {manhours_per_unit[cat]:g} man-hours/unit, crew of {crew}")
    out.append("Work packages run substructure first, then superstructure, "
               "sequenced finish-to-start in construction order; lines within "
               "a package run consecutively (shared crew). Overlap between "
               "packages is a planning decision and is not assumed here.")
    return out


# ── learning the norms back from a built programme ─────────────────────────
#
# Operator, 2026-08-17: "the built programs and its manpower histogram should
# tell u the productivity per manhour". Exactly — a completed programme is a
# productivity record, not just a plan: each activity's man-hours (crew x
# duration x shift, or the resource assignment where one exists) divided by the
# quantity it delivered IS the norm for that work, measured on this project
# with these crews. Deriving it here puts the operator's OWN history first in
# the sourcing order, ahead of any published reference.

def activity_manhours(activity: Dict[str, Any],
                      hours_per_day: float = DEFAULT_HOURS_PER_DAY) -> Optional[float]:
    """Man-hours an activity consumed.

    Prefers an explicit figure (``total_manhours``, or a P6 resource
    assignment's ``target_qty``/``act_reg_qty``, which are already man-hours),
    and only then falls back to crew x duration x shift. Returns None when the
    activity carries neither — a silent zero would understate a norm and make
    the next programme optimistic.
    """
    for key in ("total_manhours", "actual_manhours", "target_qty", "act_reg_qty"):
        v = activity.get(key)
        if isinstance(v, (int, float)) and v > 0:
            return float(v)
    crew = activity.get("crew_size") or activity.get("manpower")
    if not crew:
        res = activity.get("resources")
        crew = len(res) if isinstance(res, list) else None
    dur = activity.get("duration_days") or activity.get("duration")
    if crew and dur:
        return float(crew) * float(dur) * hours_per_day
    return None


def norms_from_programme(
    activities: Iterable[Dict[str, Any]],
    quantities: Dict[str, float],
    *,
    hours_per_day: float = DEFAULT_HOURS_PER_DAY,
    category_overrides: Optional[Dict[str, str]] = None,
) -> Dict[str, Dict[str, Any]]:
    """Derive man-hours-per-unit per work category from a BUILT programme.

    ``quantities`` maps an activity id (or its exact name) to the quantity that
    activity delivered, in the category's own unit — the one thing a programme
    cannot know by itself.

    Returns ``{category: {manhours_per_unit, total_manhours, total_quantity,
    samples, activities}}``. The norm is quantity-weighted (total man-hours /
    total quantity), never a mean of ratios: a 4,000 m2 slab and a 20 m2
    landing must not carry equal weight.

    Activities with no quantity, or no recoverable man-hours, are skipped and
    counted rather than assumed — an unmeasured activity has no norm to teach.
    """
    overrides = category_overrides or {}
    acc: Dict[str, Dict[str, Any]] = {}
    for a in activities or []:
        key = a.get("id") or a.get("code") or a.get("name")
        qty = quantities.get(str(key))
        if qty is None:
            qty = quantities.get(str(a.get("name")))
        if not qty or qty <= 0:
            continue
        mh = activity_manhours(a, hours_per_day)
        if not mh:
            continue
        name = a.get("name") or ""
        cat = (overrides.get(str(key)) or overrides.get(name)
               or (a.get("boq") or {}).get("category") or categorize(name))
        bucket = acc.setdefault(cat, {"total_manhours": 0.0, "total_quantity": 0.0,
                                      "samples": 0, "activities": []})
        bucket["total_manhours"] += mh
        bucket["total_quantity"] += float(qty)
        bucket["samples"] += 1
        bucket["activities"].append(name)
    out: Dict[str, Dict[str, Any]] = {}
    for cat, b in acc.items():
        if b["total_quantity"] <= 0:
            continue
        out[cat] = {
            "manhours_per_unit": round(b["total_manhours"] / b["total_quantity"], 4),
            "total_manhours": round(b["total_manhours"], 1),
            "total_quantity": round(b["total_quantity"], 2),
            "samples": b["samples"],
            "activities": b["activities"],
            "basis": "measured from a built programme on this project",
        }
    return out
