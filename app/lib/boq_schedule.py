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

import logging
import math
import os
import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

from app.lib.boq_pricing import categorize

logger = logging.getLogger(__name__)

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


# ── leftover F1: high-level WBS from retrieved BOQ demolition rows ─────────
#
# generate_wbs is a template scheduler. Live F1 asked for a WBS over "the
# demolition and site clearance scope in this project's BOQ" and still got
# the building scaffold (Site Preparation / Substructure / Superstructure)
# because retrieved CESMM rows were never elected into the tree. These
# helpers parse measured rows that are already in the excerpts, filter to
# demolition / site-clearance, and build a high-level tree. They do not
# invent item codes or descriptions. Kill-switch: BOQ_SCOPE_WBS=0.

_BOQ_SCOPE_WBS_PHASE = "Demolition and Site Clearance"

_PIPE_BOQ_ROW_RE = re.compile(
    r"\|\s*(?P<code>[A-Za-z]\s*\d{2,4}(?:\.\d+)?)\s*"
    r"\|\s*(?P<desc>[^|\n]+?)\s*\|",
)
_INDEXER_BOQ_LINE_RE = re.compile(
    r"(?i)BOQ line item(?:[^—\n]*)?[—–-]\s*(?P<desc>.+?):\s*total quantity",
)
_CESMM_PROSE_ROW_RE = re.compile(
    r"(?i)(?<![A-Za-z0-9])(?P<code>[A-Z]\s*\d{2,4}(?:\.\d+)?)\s+"
    r"(?P<desc>[A-Za-z][^|\n]{3,120})",
)
_SKIP_BOQ_DESC_RE = re.compile(
    r"(?i)\b(?:part\s+summary|grand\s+total|carried\s+forward|"
    r"total\s+this\s+page|brought\s+forward|item\s*$|description\s*$)\b",
)
_TRAILING_MEASURED_RE = re.compile(
    r"\s+\d[\d,]*(?:\.\d+)?\s*"
    r"(?:ha|m2|m²|m3|m³|nr|nos|no\.?|m|lm|sum|item|kg|t)\b.*$",
    re.IGNORECASE,
)
_DEMO_SITE_CLEAR_ITEM_RE = re.compile(
    r"(?i)\b(?:demolit|site\s+clear|clearance|clearing|grubbing|"
    r"tree\s+remov|remov(?:al|e)\s+of\s+(?:trees?|fence|culvert|pavement|"
    r"carriageway)|breaking\s+out|chain\s+link|carriageway|sidewalk|"
    r"road\s+markings?)\b",
)
_CESMM_D_CODE_RE = re.compile(r"(?i)^D\d{2,4}(?:\.\d+)?$")


def boq_scope_wbs_enabled() -> bool:
    """ON by default — live leftover F1 BOQ-grounded WBS.

    ``BOQ_SCOPE_WBS=0`` restores the building-template scaffold.
    """
    return (os.getenv("BOQ_SCOPE_WBS", "1") or "").strip().lower() not in (
        "0", "false", "no", "off",
    )


def _compact_cesmm_code(code: str) -> str:
    return re.sub(r"\s+", "", (code or "").strip()).upper()


def _clean_boq_description(desc: str) -> str:
    text = re.sub(r"\s+", " ", (desc or "").strip())
    text = _TRAILING_MEASURED_RE.sub("", text).strip(" |-")
    return text[:120]


def parse_boq_measured_rows(text: str) -> List[Dict[str, Any]]:
    """Extract CESMM / priced-bill rows from retrieved chunk text.

    Accepts pipe tables, indexer ``BOQ line item — …`` lines, and
    ``D110 General site clearance`` prose. Returns only rows that
    already appear in ``text`` — nothing is invented.
    """
    blob = text or ""
    if not blob.strip():
        return []
    found: List[Dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    def _add(code: str, desc: str) -> None:
        item_key = _compact_cesmm_code(code)
        description = _clean_boq_description(desc)
        if not description or _SKIP_BOQ_DESC_RE.search(description):
            return
        if item_key and not re.match(r"(?i)^[A-Z]\d{2,4}(?:\.\d+)?$", item_key):
            return
        key = (item_key, _norm_desc(description))
        if not key[1] or key in seen:
            return
        seen.add(key)
        found.append({
            "item_key": item_key or None,
            "description": description,
        })

    for match in _PIPE_BOQ_ROW_RE.finditer(blob):
        _add(match.group("code"), match.group("desc"))
    for match in _INDEXER_BOQ_LINE_RE.finditer(blob):
        _add("", match.group("desc"))
    for match in _CESMM_PROSE_ROW_RE.finditer(blob):
        _add(match.group("code"), match.group("desc"))
    return found


def item_is_demolition_or_site_clearance(item: Dict[str, Any]) -> bool:
    """True for CESMM class D or a demolition / site-clearance description."""
    code = _compact_cesmm_code(str(item.get("item_key") or ""))
    if _CESMM_D_CODE_RE.match(code):
        return True
    desc = str(item.get("description") or item.get("item_key") or "")
    if _DEMO_SITE_CLEAR_ITEM_RE.search(desc):
        return True
    return categorize(desc) == "Demolition"


def filter_demolition_site_clearance_items(
    items: Iterable[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Keep retrieved demolition / site-clearance rows only."""
    out: List[Dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for item in items or []:
        if not isinstance(item, dict):
            continue
        if not item_is_demolition_or_site_clearance(item):
            continue
        key = (
            _compact_cesmm_code(str(item.get("item_key") or "")),
            _norm_desc(item.get("description") or item.get("item_key")),
        )
        if not key[1] or key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def _chunk_looks_like_measured_boq(text: str, filename: str) -> bool:
    """True when the excerpt is a bill row, not contract prose about the bill."""
    try:
        from app.core.rag.retriever import document_is_a_bill_of_quantities
    except Exception:  # noqa: BLE001 — retrieval helper must not break WBS
        document_is_a_bill_of_quantities = None
    if document_is_a_bill_of_quantities and document_is_a_bill_of_quantities(filename):
        return True
    blob = text or ""
    if re.search(r"(?i)BOQ line item", blob):
        return True
    if blob.count("|") >= 4 and _CESMM_PROSE_ROW_RE.search(blob):
        return True
    return False


# Live leftover F1 on Master Corpus: the WBS ask ranks Conditions-of-Contract
# prose about demolition over the priced bill. Lexical / filename rescue
# pulls the measured rows that query similarity missed. Kill-switch is the
# existing ``BOQ_SCOPE_WBS=0`` (this helper is only reached after election).
_DEMO_BOQ_RETRIEVE_QUERY = (
    "demolition and site clearance BOQ CESMM D110 D290 D549 D599 "
    "general site clearance removal of trees chain link fence"
)
_DEMO_BOQ_RESCUE_PHRASES: Tuple[str, ...] = (
    "BOQ line item",
    "General site clearance",
    "Demolition and Site Clearance",
    "site clearance",
    "Removal of trees",
    "chain link fence",
    "D110",
    "D290.1",
    "D549.2",
    "D599.5",
)
_DEMO_BOQ_FILENAME_TERMS = ("demolition", "boq", "site", "clearance")


def resolve_boq_wbs_project_id(project_id: str) -> str:
    """Chat's ``master_corpus`` alias stores no chunks — RAG lives on the source.

    Live F1 after #524 called ``retrieve_with_filter`` with the alias. The
    WBS-shaped query then ranked fallback contract prose, parsed zero
    measured rows, and generate_wbs kept the building template.
    """
    if not project_id:
        return project_id
    try:
        from app.core.projects import _master_corpus_source
        return _master_corpus_source(project_id) or project_id
    except Exception:  # noqa: BLE001 — alias helper must not break WBS
        return project_id


def _chunk_source_name(chunk: Any, doc_name_for_id=None) -> str:
    name = getattr(chunk, "source_name", "") or ""
    if name:
        return name
    if doc_name_for_id is None:
        try:
            from app.core.rag.retriever import _doc_name_for_id
            doc_name_for_id = _doc_name_for_id
        except Exception:  # noqa: BLE001
            return ""
    try:
        return doc_name_for_id(getattr(chunk, "doc_id", "") or "") or ""
    except Exception:  # noqa: BLE001
        return ""


def _items_from_measured_chunks(chunks: Iterable[Any]) -> List[Dict[str, Any]]:
    """Parse CESMM / indexer rows from chunks that already look like a bill."""
    collected: List[Dict[str, Any]] = []
    for chunk in chunks or []:
        text = getattr(chunk, "text", "") or ""
        filename = _chunk_source_name(chunk)
        if not _chunk_looks_like_measured_boq(text, filename):
            continue
        for row in parse_boq_measured_rows(text):
            item = dict(row)
            if filename:
                item["source"] = filename
            collected.append(item)
    return filter_demolition_site_clearance_items(collected)


def _retrieve_measured_boq_items(query: str, project_id: str) -> List[Dict[str, Any]]:
    try:
        from app.core.rag.retriever import retrieve_with_filter
        chunks, _noise = retrieve_with_filter(query, project_id, k=8)
    except Exception as exc:  # noqa: BLE001 — fall through to lexical rescue
        logger.warning("BOQ-scope WBS retrieval failed: %s", exc)
        return []
    return _items_from_measured_chunks(chunks)


def _lexical_rescue_demolition_boq_chunks(project_id: str) -> List[Any]:
    """Pull measured demolition-BOQ chunks by identifier / filename.

    Project-only. Failures never raise — empty means keep the template.
    """
    try:
        from app.core.rag.vector_store import get_store
        store = get_store()
    except Exception as exc:  # noqa: BLE001
        logger.warning("BOQ-scope WBS store open failed: %s", exc)
        return []

    by_id: Dict[str, Any] = {}
    fetch = getattr(store, "identifier_search", None)
    if callable(fetch):
        try:
            hits = fetch(project_id, list(_DEMO_BOQ_RESCUE_PHRASES), k=20) or []
        except Exception as exc:  # noqa: BLE001
            logger.warning("BOQ-scope WBS lexical rescue failed: %s", exc)
            hits = []
        for chunk in hits:
            cid = getattr(chunk, "chunk_id", None) or id(chunk)
            by_id[cid] = chunk
        neighbors = getattr(store, "chunks_for_docs", None)
        if callable(neighbors) and hits:
            doc_ids = list({getattr(c, "doc_id", "") for c in hits if getattr(c, "doc_id", "")})
            try:
                extra = neighbors(project_id, doc_ids, k_per_doc=24) or []
            except Exception as exc:  # noqa: BLE001
                logger.warning("BOQ-scope WBS neighbor fetch failed: %s", exc)
                extra = []
            for chunk in extra:
                cid = getattr(chunk, "chunk_id", None) or id(chunk)
                by_id.setdefault(cid, chunk)

    try:
        from app.core.projects import documents_matching_filename_terms
        from app.core.rag.retriever import document_is_a_bill_of_quantities
        named = documents_matching_filename_terms(
            project_id,
            list(_DEMO_BOQ_FILENAME_TERMS),
            min_terms=2,
            limit=8,
        ) or []
    except Exception as exc:  # noqa: BLE001
        logger.warning("BOQ-scope WBS filename rescue failed: %s", exc)
        named = []
        document_is_a_bill_of_quantities = None

    demo_doc_ids: List[str] = []
    names_by_id: Dict[str, str] = {}
    for doc in named:
        if not isinstance(doc, dict):
            continue
        doc_id = str(doc.get("id") or "")
        filename = str(doc.get("original_name") or doc.get("file_path") or "")
        if not doc_id or not filename:
            continue
        if document_is_a_bill_of_quantities and not document_is_a_bill_of_quantities(filename):
            continue
        if not re.search(r"(?i)(?:demolit|site[\s_-]*clear)", filename):
            continue
        demo_doc_ids.append(doc_id)
        names_by_id[doc_id] = filename

    neighbors = getattr(store, "chunks_for_docs", None)
    if callable(neighbors) and demo_doc_ids:
        try:
            extra = neighbors(project_id, demo_doc_ids, k_per_doc=32) or []
        except Exception as exc:  # noqa: BLE001
            logger.warning("BOQ-scope WBS demo-BOQ chunk fetch failed: %s", exc)
            extra = []
        for chunk in extra:
            if not getattr(chunk, "source_name", ""):
                chunk.source_name = names_by_id.get(getattr(chunk, "doc_id", "") or "", "")
            cid = getattr(chunk, "chunk_id", None) or id(chunk)
            by_id.setdefault(cid, chunk)
    return list(by_id.values())


def retrieve_boq_scope_items(query: str, project_id: str) -> List[Dict[str, Any]]:
    """Retrieve measured BOQ rows for a demolition / site-clearance WBS ask.

    Remaps the Master Corpus alias to the backing project, then prefers
    measured rows from ``retrieve_with_filter``. When the WBS phrasing
    ranks contract prose instead of the bill, a lexical / filename rescue
    pulls CESMM D-rows that already exist in the corpus. Empty when there
    is no project, retrieval fails, or no measured rows land.
    """
    if not project_id or not (query or "").strip():
        return []
    resolved = resolve_boq_wbs_project_id(str(project_id))
    items = _retrieve_measured_boq_items(query, resolved)
    if items:
        return items
    if query.strip() != _DEMO_BOQ_RETRIEVE_QUERY:
        items = _retrieve_measured_boq_items(_DEMO_BOQ_RETRIEVE_QUERY, resolved)
        if items:
            return items
    try:
        rescued = _items_from_measured_chunks(
            _lexical_rescue_demolition_boq_chunks(resolved)
        )
    except Exception as exc:  # noqa: BLE001 — fall through to template scaffold
        logger.warning("BOQ-scope WBS rescue failed: %s", exc)
        return []
    if rescued:
        logger.info("BOQ-scope WBS rescue recovered %d demolition row(s)", len(rescued))
    return rescued


def wbs_tree_from_boq_items(
    items: Iterable[Dict[str, Any]],
    *,
    phase: str = _BOQ_SCOPE_WBS_PHASE,
) -> Dict[str, str]:
    """High-level tree: one phase, one package per retrieved BOQ row.

    Package names keep the CESMM code when the row had one. No template
    packages are added.
    """
    scoped = filter_demolition_site_clearance_items(items)
    if not scoped:
        return {}
    tree: Dict[str, str] = {"1": phase}
    for idx, item in enumerate(scoped, start=1):
        code = _compact_cesmm_code(str(item.get("item_key") or ""))
        desc = _clean_boq_description(
            str(item.get("description") or item.get("item_key") or "")
        )
        name = f"{code} {desc}".strip() if code else desc
        if name:
            tree[f"1.{idx}"] = name
    return tree


def activities_from_boq_scope_outline(
    items: Iterable[Dict[str, Any]],
    *,
    phase: str = _BOQ_SCOPE_WBS_PHASE,
) -> List[Dict[str, Any]]:
    """One lightweight activity per retrieved row so CPM / cost-load can run.

    Durations are placeholders (1 day). This is a high-level scope WBS, not
    a productivity-derived programme — callers must say so.
    """
    scoped = filter_demolition_site_clearance_items(items)
    activities: List[Dict[str, Any]] = []
    prev: Optional[str] = None
    phase_slug = _slug(phase)
    for idx, item in enumerate(scoped, start=1):
        act_id = f"1.{idx}"
        code = _compact_cesmm_code(str(item.get("item_key") or ""))
        desc = _clean_boq_description(
            str(item.get("description") or item.get("item_key") or "Item")
        )
        name = f"{code} {desc}".strip() if code else desc
        activities.append({
            "id": act_id,
            "code": act_id,
            "name": name[:120] or f"Item {idx}",
            "duration_days": 1,
            "predecessors": [prev] if prev else [],
            "resources": ["demolition"],
            "wbs_phase": phase_slug,
            "boq": {
                "item_key": item.get("item_key") or code or None,
                "description": desc,
                "source": item.get("source"),
                "category": "Demolition",
            },
        })
        prev = act_id
    return activities


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
