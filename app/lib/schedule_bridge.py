"""Bridge ``generate_wbs`` output into ``generate_cost_loaded_schedule`` input.

``ConstructionContainer.generate_wbs`` emits activities shaped for CPM display
— ``duration_days`` (int), ``resources`` (a trade-name list), ``wbs_phase`` —
with no ``manpower`` and no ``cost``. ``generate_cost_loaded_schedule`` needs
``duration``, ``manpower``, ``wbs`` and, optionally, ``cost``. This module maps
one to the other.

Honesty contract (why cost is not fabricated here):
  A schedule generated from a brief has NO estimate behind it. Man-days
  (crew x duration) can be derived honestly and drive the progress S-curve /
  manpower histogram. Cost cannot — ``cost = man-days x day_rate`` is just the
  man-days curve rescaled by a rate we invented, so we leave ``cost`` UNSET
  unless the caller supplies a real ``day_rate`` (then it is an *indicative
  labor* figure the workbook labels as such) or the activity already carries a
  real cost. We never zero-fill cost as if it were measured.

The per-field derivation only fills what is ABSENT, so passing already
cost-loaded activities through is a no-op — safe to apply at any call site.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

# Rule-of-thumb heads per assigned trade. A guess, surfaced in ``assumptions``
# so a reader sees the basis rather than trusting a fabricated headcount.
DEFAULT_CREW_PER_TRADE = 4


def _duration(a: Dict[str, Any]) -> int:
    for k in ("duration", "duration_days"):
        v = a.get(k)
        if isinstance(v, (int, float)) and v >= 0:
            return int(v)
    return 0


def _wbs(a: Dict[str, Any]) -> str:
    return str(a.get("wbs") or a.get("wbs_phase") or a.get("phase") or "")


def _manpower(a: Dict[str, Any], crew_per_trade: int) -> int:
    v = a.get("manpower")
    if isinstance(v, (int, float)) and v > 0:
        return int(v)
    res = a.get("resources")
    n = len(res) if isinstance(res, (list, tuple)) else 0
    return max(1, n * crew_per_trade)


def bridge_activity(
    a: Dict[str, Any],
    *,
    crew_per_trade: int = DEFAULT_CREW_PER_TRADE,
    day_rate: Optional[float] = None,
) -> Dict[str, Any]:
    """Map one ``generate_wbs`` activity to a cost-loaded-schedule activity.

    Fills only absent fields, so an already cost-loaded activity is preserved.
    ``cost`` is set only when the activity already has one, or when ``day_rate``
    is supplied (indicative labor cost = man-days x rate).
    """
    dur = _duration(a)
    mp = _manpower(a, crew_per_trade)
    out: Dict[str, Any] = {
        "id": str(a.get("id") or a.get("code") or ""),
        "name": a.get("name") or a.get("activity") or "",
        "wbs": _wbs(a),
        "duration": dur,
        "predecessors": [str(p) for p in (a.get("predecessors") or [])],
        "manpower": mp,
    }
    existing = a.get("cost")
    if isinstance(existing, (int, float)) and existing:
        out["cost"] = float(existing)
    elif day_rate:
        out["cost"] = round(mp * dur * float(day_rate), 2)
    return out


def bridge_wbs_to_cost_loaded(
    activities: List[Dict[str, Any]],
    *,
    crew_per_trade: int = DEFAULT_CREW_PER_TRADE,
    day_rate: Optional[float] = None,
) -> List[Dict[str, Any]]:
    """Bridge a list of ``generate_wbs`` activities to cost-loaded shape."""
    return [
        bridge_activity(a, crew_per_trade=crew_per_trade, day_rate=day_rate)
        for a in (activities or [])
    ]


def bridge_assumptions(crew_per_trade: int = DEFAULT_CREW_PER_TRADE,
                       day_rate: Optional[float] = None) -> List[str]:
    """Human-readable basis for the derived numbers, for the ``assumptions`` list."""
    out = [
        f"Manpower derived as (trades assigned x {crew_per_trade} heads/trade) "
        "where a real headcount was not supplied.",
        "Man-days (crew x duration) drive the progress S-curve and manpower "
        "histogram; these are schedule-derived, not estimated.",
    ]
    if day_rate:
        out.append(
            f"Cost is INDICATIVE labor only (man-days x {day_rate:,.0f}/day); "
            "it is not a priced estimate."
        )
    else:
        out.append(
            "No day-rate supplied, so no cost column is generated — a schedule "
            "from a brief carries no estimate to cost-load."
        )
    return out
