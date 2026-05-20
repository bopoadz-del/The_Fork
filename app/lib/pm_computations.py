"""Reusable project-management computations — Reasoning Engine Plan 1.

Pure functions, no AI, no I/O. Generated code (Plan 4) and the reasoner
(Plan 5) import these instead of re-deriving the algorithms.

CPM math runs in working-day offsets (integers). See the plan header for the
offset conventions.
"""

from typing import Dict, List, Tuple

from app.schemas.cpm import (
    Activity, CPMInput, CPMOutput, CPMResult, DependencyType,
    GanttBar, HistogramPeriod, ResourceHistogram,
)


class CircularDependencyError(ValueError):
    """Raised when the activity network contains a cycle."""


def topological_order(activities: List[Activity]) -> List[str]:
    """Activity ids in dependency order (Kahn's algorithm).

    Raises ValueError for an unknown predecessor, CircularDependencyError for
    a cycle. Ties broken by id, so the order is deterministic.
    """
    ids = {a.id for a in activities}
    indegree: Dict[str, int] = {a.id: 0 for a in activities}
    successors: Dict[str, List[str]] = {a.id: [] for a in activities}

    for a in activities:
        for dep in a.predecessors:
            if dep.predecessor_id not in ids:
                raise ValueError(
                    f"Activity '{a.id}' references unknown predecessor "
                    f"'{dep.predecessor_id}'"
                )
            indegree[a.id] += 1
            successors[dep.predecessor_id].append(a.id)

    queue = sorted(i for i, d in indegree.items() if d == 0)
    order: List[str] = []
    while queue:
        nid = queue.pop(0)
        order.append(nid)
        for succ in successors[nid]:
            indegree[succ] -= 1
            if indegree[succ] == 0:
                queue.append(succ)
        queue.sort()

    if len(order) != len(activities):
        cycle = sorted(set(indegree) - set(order))
        raise CircularDependencyError(
            f"Circular dependency among: {', '.join(cycle)}"
        )
    return order


def cpm_forward_pass(
    acts: Dict[str, Activity], order: List[str]
) -> Dict[str, Tuple[int, int]]:
    """Compute (ES, EF) working-day offsets. `order` must be topological."""
    es: Dict[str, int] = {}
    ef: Dict[str, int] = {}
    for nid in order:
        a = acts[nid]
        start = 0
        for dep in a.predecessors:
            p_es, p_ef = es[dep.predecessor_id], ef[dep.predecessor_id]
            if dep.type == DependencyType.FS:
                cand = p_ef + dep.lag
            elif dep.type == DependencyType.SS:
                cand = p_es + dep.lag
            elif dep.type == DependencyType.FF:
                cand = p_ef + dep.lag - a.duration
            else:  # SF
                cand = p_es + dep.lag - a.duration
            start = max(start, cand)
        es[nid] = start
        ef[nid] = start + a.duration
    return {nid: (es[nid], ef[nid]) for nid in order}


def _successor_map(acts: Dict[str, Activity]) -> Dict[str, List[Tuple[str, DependencyType, int]]]:
    """Map each activity id to a list of (successor_id, dep_type, lag)."""
    succ: Dict[str, list] = {nid: [] for nid in acts}
    for a in acts.values():
        for dep in a.predecessors:
            succ[dep.predecessor_id].append((a.id, dep.type, dep.lag))
    return succ


def cpm_backward_pass(
    acts: Dict[str, Activity], order: List[str], project_duration: int
) -> Dict[str, Tuple[int, int]]:
    """Compute (LS, LF) working-day offsets. `order` must be topological."""
    succ = _successor_map(acts)
    ls: Dict[str, int] = {}
    lf: Dict[str, int] = {}
    for nid in reversed(order):
        a = acts[nid]
        finish = project_duration
        for (s_id, s_type, lag) in succ[nid]:
            s_ls, s_lf = ls[s_id], lf[s_id]
            if s_type == DependencyType.FS:
                cand = s_ls - lag
            elif s_type == DependencyType.SS:
                cand = s_ls - lag + a.duration
            elif s_type == DependencyType.FF:
                cand = s_lf - lag
            else:  # SF
                cand = s_lf - lag + a.duration
            finish = min(finish, cand)
        lf[nid] = finish
        ls[nid] = finish - a.duration
    return {nid: (ls[nid], lf[nid]) for nid in acts}


def calculate_float(
    acts: Dict[str, Activity], fwd: Dict[str, Tuple[int, int]]
) -> Dict[str, int]:
    """Free float per activity: how far it can slip without delaying any
    successor's early dates. Returns -1 for activities with no successor
    (the caller substitutes total float)."""
    succ = _successor_map(acts)
    ff: Dict[str, int] = {}
    for nid, a in acts.items():
        es_j, ef_j = fwd[nid]
        slacks = []
        for (s_id, s_type, lag) in succ[nid]:
            es_k, ef_k = fwd[s_id]
            if s_type == DependencyType.FS:
                slacks.append(es_k - (ef_j + lag))
            elif s_type == DependencyType.SS:
                slacks.append(es_k - (es_j + lag))
            elif s_type == DependencyType.FF:
                slacks.append(ef_k - (ef_j + lag))
            else:  # SF
                slacks.append(ef_k - (es_j + lag))
        ff[nid] = max(0, min(slacks)) if slacks else -1
    return ff


def compute_cpm(data: CPMInput) -> CPMOutput:
    """Run the full Critical Path Method over an activity network."""
    activities = data.activities
    if not activities:
        return CPMOutput(results=[], project_duration=0, project_finish=None,
                         critical_path=[], critical_percentage=0.0,
                         near_critical=[])

    acts = {a.id: a for a in activities}
    if len(acts) != len(activities):
        raise ValueError("Duplicate activity ids in input")

    order = topological_order(activities)
    fwd = cpm_forward_pass(acts, order)
    project_duration = max(ef for (_es, ef) in fwd.values())
    bwd = cpm_backward_pass(acts, order, project_duration)
    ff = calculate_float(acts, fwd)
    cal, start = data.calendar, data.project_start

    def proj(offset: int):
        return cal.nth_working_day(start, offset) if (start and offset >= 0) else None

    results: List[CPMResult] = []
    for nid in order:
        a = acts[nid]
        es, ef = fwd[nid]
        ls, lf = bwd[nid]
        tf = ls - es
        results.append(CPMResult(
            id=a.id, name=a.name, duration=a.duration,
            early_start_day=es, early_finish_day=ef,
            late_start_day=ls, late_finish_day=lf,
            total_float=tf,
            free_float=tf if ff[nid] < 0 else ff[nid],
            is_critical=(tf <= 0),
            early_start=proj(es), early_finish=proj(ef),
            late_start=proj(ls), late_finish=proj(lf),
        ))

    critical = sorted((r for r in results if r.is_critical),
                      key=lambda r: (r.early_start_day, r.early_finish_day))
    near = [r.id for r in results if 0 < r.total_float <= 5]
    return CPMOutput(
        results=results,
        project_duration=project_duration,
        project_finish=proj(project_duration),
        critical_path=[r.id for r in critical],
        critical_percentage=round(len(critical) / len(results) * 100, 1),
        near_critical=near,
    )


_PERIOD_LENGTH = {"week": 5, "month": 21}
_HOURS_PER_DAY = 8


def resource_histogram(
    results: List[CPMResult],
    activities: List[Activity],
    period_unit: str = "week",
) -> ResourceHistogram:
    """Time-phased manpower. An activity contributes its crew to every period
    its early-date span overlaps (concurrent headcount, not man-days)."""
    length = _PERIOD_LENGTH.get(period_unit, 5)
    res_by_id = {a.id: a.resources for a in activities}
    es_ef = {r.id: (r.early_start_day, r.early_finish_day) for r in results}

    if results:
        last_day = max(ef for (_es, ef) in es_ef.values())
        n_periods = max(1, -(-last_day // length))  # ceil division
    else:
        n_periods = 0

    periods: List[HistogramPeriod] = []
    by_trade_totals: Dict[str, float] = {}
    total_manhours = 0.0

    for p in range(n_periods):
        p_start, p_end = p * length, (p + 1) * length
        by_trade: Dict[str, float] = {}
        for rid, (es, ef) in es_ef.items():
            if ef <= p_start or es >= p_end:
                continue  # activity does not overlap this period
            for res in res_by_id.get(rid, []):
                by_trade[res.trade] = by_trade.get(res.trade, 0.0) + res.count
        periods.append(HistogramPeriod(
            index=p, label=f"{period_unit[0].upper()}{p + 1}",
            total=round(sum(by_trade.values()), 2), by_trade=by_trade,
        ))

    for a in activities:
        es, ef = es_ef.get(a.id, (0, 0))
        span = ef - es
        for res in a.resources:
            by_trade_totals[res.trade] = (
                by_trade_totals.get(res.trade, 0.0) + res.count
            )
            total_manhours += res.count * span * _HOURS_PER_DAY

    peak = max(periods, key=lambda hp: hp.total, default=None)
    return ResourceHistogram(
        period_unit=period_unit,
        periods=periods,
        peak_total=peak.total if peak else 0.0,
        peak_period=peak.label if peak else "",
        by_trade_totals=by_trade_totals,
        total_manhours=round(total_manhours, 2),
    )


def gantt_data(results: List[CPMResult]) -> List[GanttBar]:
    """One Gantt bar per activity, sorted by early start (then early finish)."""
    bars = [
        GanttBar(
            id=r.id, name=r.name,
            start_day=r.early_start_day, end_day=r.early_finish_day,
            is_critical=r.is_critical,
        )
        for r in results
    ]
    bars.sort(key=lambda b: (b.start_day, b.end_day))
    return bars


def compress_schedule(
    data: CPMInput, reductions: Dict[str, int]
) -> Tuple[CPMOutput, int]:
    """Apply working-day duration cuts to named activities and re-run CPM.

    `reductions` maps activity id -> working days to remove (floored at 0
    duration). Returns (revised CPMOutput, days saved vs the baseline).
    Raises ValueError if an id is not in the network.
    """
    ids = {a.id for a in data.activities}
    unknown = set(reductions) - ids
    if unknown:
        raise ValueError(f"Unknown activity ids: {', '.join(sorted(unknown))}")

    baseline = compute_cpm(data)
    revised_acts = []
    for a in data.activities:
        if a.id in reductions:
            new_dur = max(0, a.duration - reductions[a.id])
            revised_acts.append(a.model_copy(update={"duration": new_dur}))
        else:
            revised_acts.append(a)

    revised = compute_cpm(data.model_copy(update={"activities": revised_acts}))
    delta = baseline.project_duration - revised.project_duration
    return revised, delta
