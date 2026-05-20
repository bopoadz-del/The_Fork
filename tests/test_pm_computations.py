"""Tests for pm_computations — Reasoning Engine Plan 1 (CPM core)."""

import pytest

from app.schemas.cpm import Activity, CPMInput, Dependency, DependencyType


def test_activity_defaults():
    a = Activity(id="A", duration=5)
    assert a.predecessors == [] and a.name == ""


def test_dependency_defaults_to_fs():
    d = Dependency(predecessor_id="A")
    assert d.type == DependencyType.FS and d.lag == 0


def test_negative_duration_rejected():
    with pytest.raises(Exception):
        Activity(id="A", duration=-1)


def test_empty_activity_id_rejected():
    with pytest.raises(Exception):
        Activity(id="", duration=1)


from datetime import date

from app.schemas.cpm import WorkCalendar


def test_nth_working_day_zero_is_first_working_day():
    # 2026-05-18 is a Monday
    assert WorkCalendar().nth_working_day(date(2026, 5, 18), 0) == date(2026, 5, 18)


def test_nth_working_day_skips_weekend():
    # Mon 18 + 5 working days -> Mon 25 (skips Sat 23 / Sun 24)
    assert WorkCalendar().nth_working_day(date(2026, 5, 18), 5) == date(2026, 5, 25)


def test_nth_working_day_skips_holiday():
    cal = WorkCalendar(holidays=[date(2026, 5, 19)])
    assert cal.nth_working_day(date(2026, 5, 18), 1) == date(2026, 5, 20)


def test_nth_working_day_advances_off_weekend_start():
    # Sat 23, offset 0 -> Mon 25
    assert WorkCalendar().nth_working_day(date(2026, 5, 23), 0) == date(2026, 5, 25)
