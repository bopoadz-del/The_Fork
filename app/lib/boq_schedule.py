"""Derive a construction schedule from a priced BOQ — quantities become durations.

Why this exists
---------------
``/export/schedule-from-document`` accepted a BOQ and produced a schedule whose
activities came from a canned per-project-type template: measured 2026-08-17,
a BOQ listing structural steel truss, composite cladding, chilled-water piping
and epoxy flooring yielded 46 activities of which ZERO referenced that scope
(topographic survey, building permit, blockwork...). The document contributed
only equipment lead times and target milestones — neither of which a BOQ
carries — so in practice it contributed nothing.

A BOQ is the one document that states the actual scope WITH quantities, which
is exactly what a duration needs. This module turns each priced line into an
activity whose duration is derived, not assumed:

    duration_days = ceil(quantity / (output_per_crew_day x crews))

Process, not facts
------------------
Productivity is a PROJECT variable — it changes with crew, site, floor and
method — so nothing here carries a built-in output rate. Rates are supplied by
the caller and must be sourced, in order: the project's own documents, the
project facts store, a cited published reference, or an explicit question to
the operator. A category with no supplied rate is REFUSED by name rather than
silently defaulted, because a schedule built on an invented output rate is
indistinguishable from one built on a real one until it slips.

Output shape matches ``generate_wbs`` activities exactly (id/name/duration_days/
predecessors/resources/wbs_phase), so the existing CPM, cost-loading bridge and
Excel writers consume it unchanged.
"""

from __future__ import annotations

import math
import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

from app.lib.boq_pricing import categorize

# Construction order for the categorizer's work packages. A schedule is a
# sequence claim, and this is the only place that claim is made: earthworks
# before foundations before frame before envelope before MEP before finishes.
# Categories absent from a BOQ are simply skipped.
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

# Trade label attached to each activity's ``resources`` so the manpower
# histogram groups by something meaningful rather than one anonymous pool.
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


class MissingProductivity(Exception):
    """No output rate was supplied for a work category present in the BOQ.

    Carries the categories and their units so the caller can ask a precise
    question ("what is your blockwork output in m2 per crew-day?") instead of
    reporting a generic failure.
    """

    def __init__(self, missing: Dict[str, str]) -> None:
        self.missing = missing
        detail = "; ".join(f"{cat} (in {unit}/crew-day)" for cat, unit in sorted(missing.items()))
        super().__init__(
            "No productivity supplied for: " + detail +
            ". Durations are derived from quantity / output, so these cannot be "
            "scheduled until the output rates are given — supply them from the "
            "project's own records, a cited published norm, or the operator."
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


def duration_days(quantity: float, output_per_crew_day: float, crews: int = 1) -> int:
    """Working days for ``quantity`` at ``output_per_crew_day`` with ``crews``.

    Always at least one day: a measurable item still occupies the crew for a
    shift, and a zero-day activity makes the CPM degenerate.
    """
    if output_per_crew_day <= 0:
        raise ValueError("output_per_crew_day must be > 0")
    crews = max(1, int(crews))
    return max(1, math.ceil(quantity / (output_per_crew_day * crews)))


def group_by_category(
    line_items: Iterable[Dict[str, Any]],
    overrides: Optional[Dict[str, str]] = None,
) -> Dict[str, List[Dict[str, Any]]]:
    """Bucket BOQ lines into work packages using the shipped categorizer.

    ``categorize`` is shared with pricing and matches the EARLIEST pattern in
    the description, which is right for a rate lookup but can misplace a line
    for sequencing: "Chilled water piping DN200 insulated" classifies as
    Waterproofing/Insulation on the word "insulated", and a bare "Chilled
    water piping DN200" has no pattern at all and lands in
    Other/Uncategorized. Rather than fork that taxonomy (and quietly change
    how rates resolve), a planner corrects individual lines through
    ``overrides``, keyed by ``item_key`` or exact description.
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
    """Descriptions the classifier could not place, so they are VISIBLE.

    An unrecognised line still gets scheduled (in the Other package, last),
    but a planner should see which ones the taxonomy did not understand
    rather than discover them at the end of the programme.
    """
    return [
        (i.get("description") or i.get("item_key") or "")
        for i in group_by_category(line_items, overrides).get("Other/Uncategorized", [])
    ]


def activities_from_boq(
    line_items: Iterable[Dict[str, Any]],
    *,
    productivity: Dict[str, float],
    crews: Optional[Dict[str, int]] = None,
    crew_size: int = 6,
    min_quantity: float = 0.0,
    category_overrides: Optional[Dict[str, str]] = None,
) -> List[Dict[str, Any]]:
    """Turn priced BOQ lines into CPM-ready activities.

    ``productivity`` maps a work category to its output per crew-day IN THAT
    CATEGORY'S OWN UNIT (m3/day for concrete, m2/day for blockwork). Every
    category present in the BOQ must appear, or :class:`MissingProductivity`
    is raised naming the gaps — see the module docstring on why nothing is
    defaulted.

    Sequencing: packages follow :data:`CONSTRUCTION_SEQUENCE`; the first
    activity of a package depends on the last activity of the previous one
    (finish-to-start), and lines inside a package run back-to-back because
    they share a crew. That is a conservative, defensible baseline — overlap
    is a planner's decision, not a derivation.
    """
    crews = crews or {}
    grouped = group_by_category(line_items, category_overrides)
    grouped = {
        cat: [i for i in items if _qty(i) > min_quantity]
        for cat, items in grouped.items()
    }
    grouped = {cat: items for cat, items in grouped.items() if items}

    missing = {
        cat: (items[0].get("unit") or "unit")
        for cat, items in grouped.items()
        if not productivity.get(cat)
    }
    if missing:
        raise MissingProductivity(missing)

    ordered = [c for c in CONSTRUCTION_SEQUENCE if c in grouped]
    ordered += [c for c in grouped if c not in CONSTRUCTION_SEQUENCE]  # never drop work

    activities: List[Dict[str, Any]] = []
    prev_package_tail: Optional[str] = None
    for p_idx, cat in enumerate(ordered, start=1):
        rate = float(productivity[cat])
        n_crews = int(crews.get(cat, 1))
        trade = _TRADE_OF.get(cat, "general")
        phase = _slug(cat)
        prev_in_package: Optional[str] = None
        for a_idx, item in enumerate(grouped[cat], start=1):
            qty = _qty(item)
            act_id = f"{p_idx}.{a_idx}"
            preds = [prev_in_package] if prev_in_package else (
                [prev_package_tail] if prev_package_tail else [])
            activities.append({
                "id": act_id,
                "code": act_id,
                "name": (item.get("description") or item.get("item_key") or "Item")[:120],
                "duration_days": duration_days(qty, rate, n_crews),
                "predecessors": preds,
                "resources": [trade] * max(1, n_crews),
                "wbs_phase": phase,
                # provenance — every number here is traceable to a BOQ line
                "boq": {
                    "item_key": item.get("item_key"),
                    "quantity": qty,
                    "unit": item.get("unit"),
                    "rate_used_per_crew_day": rate,
                    "crews": n_crews,
                    "crew_size": crew_size,
                    "category": cat,
                    "unit_cost": item.get("unit_cost"),
                    "total_cost": item.get("total_cost"),
                },
            })
            prev_in_package = act_id
        prev_package_tail = prev_in_package
    return activities


def schedule_basis(productivity: Dict[str, float],
                   crews: Optional[Dict[str, int]] = None) -> List[str]:
    """Human-readable basis lines — what every duration rests on."""
    crews = crews or {}
    out = ["Durations derived as quantity / (output per crew-day x crews), "
           "rounded up to whole working days (minimum 1)."]
    for cat in sorted(productivity):
        out.append(f"{cat}: {productivity[cat]:g} per crew-day"
                   f"{f' x {crews[cat]} crews' if crews.get(cat, 1) > 1 else ''}")
    out.append("Work packages are sequenced finish-to-start in construction "
               "order; lines within a package run consecutively (shared crew). "
               "Overlap between packages is a planning decision and is not "
               "assumed here.")
    return out
