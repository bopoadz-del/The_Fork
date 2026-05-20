"""Tests for pm_computations extended — Reasoning Engine Plan 1b."""

import pytest

from app.schemas.cpm import (
    Activity, GanttBar, HistogramPeriod, ResourceAssignment, ResourceHistogram,
)


def test_resource_assignment_defaults():
    r = ResourceAssignment(trade="electrician")
    assert r.count == 1.0


def test_activity_carries_resources():
    a = Activity(id="A", duration=5,
                 resources=[ResourceAssignment(trade="steelfixer", count=8)])
    assert a.resources[0].trade == "steelfixer"
    assert a.resources[0].count == 8


def test_histogram_and_gantt_models_construct():
    hp = HistogramPeriod(index=0, label="W1", total=12, by_trade={"x": 12})
    rh = ResourceHistogram(period_unit="week", periods=[hp], peak_total=12,
                           peak_period="W1", by_trade_totals={"x": 12},
                           total_manhours=480)
    assert rh.peak_total == 12
    bar = GanttBar(id="A", name="Mob", start_day=0, end_day=5, is_critical=True)
    assert bar.end_day == 5


from app.schemas.cpm import CPMInput, Dependency
from app.lib.pm_computations import compute_cpm, resource_histogram

_PERIOD_DAYS = {"week": 5, "month": 21}


def _act(id, dur, preds=None, resources=None):
    return Activity(
        id=id, duration=dur,
        predecessors=[Dependency(predecessor_id=p) for p in (preds or [])],
        resources=resources or [],
    )


def test_resource_histogram_buckets_by_week():
    # A: 10 working days, crew of 4 -> spans weeks 0 and 1
    acts = [_act("A", 10, resources=[ResourceAssignment(trade="civil", count=4)])]
    out = compute_cpm(CPMInput(activities=acts))
    hist = resource_histogram(out.results, acts, period_unit="week")
    assert hist.period_unit == "week"
    assert len(hist.periods) == 2
    assert hist.periods[0].by_trade["civil"] == 4
    assert hist.peak_total == 4


def test_resource_histogram_sums_concurrent_trades():
    # A and B both run in week 0, different trades
    acts = [
        _act("A", 5, resources=[ResourceAssignment(trade="civil", count=6)]),
        _act("B", 5, resources=[ResourceAssignment(trade="mep", count=3)]),
    ]
    out = compute_cpm(CPMInput(activities=acts))
    hist = resource_histogram(out.results, acts, period_unit="week")
    assert hist.periods[0].total == 9
    assert hist.peak_total == 9


def test_resource_histogram_total_manhours():
    # crew 4 x 10 days x 8 h = 320
    acts = [_act("A", 10, resources=[ResourceAssignment(trade="civil", count=4)])]
    out = compute_cpm(CPMInput(activities=acts))
    hist = resource_histogram(out.results, acts, period_unit="week")
    assert hist.total_manhours == 320


from app.lib.pm_computations import gantt_data


def test_gantt_data_one_bar_per_activity():
    acts = [_act("A", 3), _act("B", 5, ["A"])]
    out = compute_cpm(CPMInput(activities=acts))
    bars = gantt_data(out.results)
    assert len(bars) == 2
    a = next(b for b in bars if b.id == "A")
    assert (a.start_day, a.end_day) == (0, 3)
    assert a.is_critical is True


def test_gantt_data_sorted_by_start():
    acts = [_act("A", 3), _act("B", 5, ["A"]), _act("C", 2)]
    bars = gantt_data(compute_cpm(CPMInput(activities=acts)).results)
    starts = [b.start_day for b in bars]
    assert starts == sorted(starts)
