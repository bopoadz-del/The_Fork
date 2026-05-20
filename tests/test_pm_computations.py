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


from app.lib.pm_computations import CircularDependencyError, topological_order


def _act(id, dur, preds=None):
    return Activity(
        id=id, duration=dur,
        predecessors=[Dependency(predecessor_id=p) for p in (preds or [])],
    )


def test_topological_order_linear_chain():
    acts = [_act("C", 1, ["B"]), _act("A", 1), _act("B", 1, ["A"])]
    assert topological_order(acts) == ["A", "B", "C"]


def test_topological_order_detects_cycle():
    acts = [_act("A", 1, ["B"]), _act("B", 1, ["A"])]
    with pytest.raises(CircularDependencyError):
        topological_order(acts)


def test_topological_order_rejects_unknown_predecessor():
    with pytest.raises(ValueError):
        topological_order([_act("A", 1, ["GHOST"])])


from app.lib.pm_computations import cpm_forward_pass


def _index(acts):
    return {a.id: a for a in acts}


def test_forward_pass_linear_chain():
    acts = [_act("A", 3), _act("B", 2, ["A"]), _act("C", 4, ["B"])]
    fwd = cpm_forward_pass(_index(acts), topological_order(acts))
    assert fwd["A"] == (0, 3)
    assert fwd["B"] == (3, 5)
    assert fwd["C"] == (5, 9)


def test_forward_pass_parallel_takes_max():
    acts = [_act("A", 3), _act("B", 2, ["A"]), _act("C", 5, ["A"]),
            _act("D", 1, ["B", "C"])]
    fwd = cpm_forward_pass(_index(acts), topological_order(acts))
    assert fwd["D"] == (8, 9)  # max(B.EF 5, C.EF 8)


def test_forward_pass_fs_lag():
    acts = [_act("A", 3),
            Activity(id="B", duration=2, predecessors=[
                Dependency(predecessor_id="A", type=DependencyType.FS, lag=2)])]
    fwd = cpm_forward_pass(_index(acts), topological_order(acts))
    assert fwd["B"] == (5, 7)


def test_forward_pass_negative_lag_overlaps():
    # FS lag -2: B starts 2 working days before A finishes
    acts = [_act("A", 6),
            Activity(id="B", duration=4, predecessors=[
                Dependency(predecessor_id="A", type=DependencyType.FS, lag=-2)])]
    fwd = cpm_forward_pass(_index(acts), topological_order(acts))
    assert fwd["B"] == (4, 8)  # A.EF 6 + lag -2 = 4


def test_forward_pass_start_to_start():
    acts = [_act("A", 10),
            Activity(id="B", duration=4, predecessors=[
                Dependency(predecessor_id="A", type=DependencyType.SS, lag=2)])]
    fwd = cpm_forward_pass(_index(acts), topological_order(acts))
    assert fwd["B"] == (2, 6)


from datetime import date as _date

from app.lib.pm_computations import compute_cpm


def test_compute_cpm_identifies_critical_path():
    # long path A(3)->B(5)->D(2)=10; A->C(2)->D has float
    acts = [_act("A", 3), _act("B", 5, ["A"]), _act("C", 2, ["A"]),
            _act("D", 2, ["B", "C"])]
    out = compute_cpm(CPMInput(activities=acts))
    assert out.project_duration == 10
    assert out.critical_path == ["A", "B", "D"]
    by_id = {r.id: r for r in out.results}
    assert by_id["C"].total_float == 3
    assert by_id["C"].is_critical is False
    assert by_id["A"].is_critical is True


def test_compute_cpm_free_float():
    acts = [_act("A", 3), _act("B", 5, ["A"]), _act("C", 2, ["A"]),
            _act("D", 2, ["B", "C"])]
    by_id = {r.id: r for r in compute_cpm(CPMInput(activities=acts)).results}
    assert by_id["C"].free_float == 3


def test_compute_cpm_near_critical():
    acts = [_act("A", 3), _act("B", 5, ["A"]), _act("C", 4, ["A"]),
            _act("D", 2, ["B", "C"])]
    assert "C" in compute_cpm(CPMInput(activities=acts)).near_critical


def test_compute_cpm_projects_dates():
    out = compute_cpm(CPMInput(activities=[_act("A", 5)],
                               project_start=_date(2026, 5, 18)))
    assert out.results[0].early_start == _date(2026, 5, 18)
    assert out.results[0].early_finish == _date(2026, 5, 25)


def test_compute_cpm_empty_input():
    out = compute_cpm(CPMInput(activities=[]))
    assert out.project_duration == 0 and out.results == []


def test_compute_cpm_rejects_duplicate_ids():
    with pytest.raises(ValueError):
        compute_cpm(CPMInput(activities=[_act("A", 1), _act("A", 2)]))
