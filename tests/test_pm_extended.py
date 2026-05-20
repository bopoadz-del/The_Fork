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
