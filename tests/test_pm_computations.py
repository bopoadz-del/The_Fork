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
