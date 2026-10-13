"""Calculator outputs print the unit and label the registry declares.

A unit token that names another field (``duration_units``) is not a unit.
A per-day rate is a duration in days, so it is not labelled as man-hours.
"""
from __future__ import annotations

import pytest

from app.agents.base.formulas.construction_formulas_planning import parse_unit
from app.agents.driver import render
from app.lib.construction_formulas import run_calculation
from app.lib.formula_registry import all_specs

_BARE = {"", "-", "currency", "ratio", "count"}


def test_lane_output_units_are_real_units():
    """Planning and procurement declare a converter unit, or no unit."""
    offenders = []
    for spec in all_specs():
        if spec.owner not in ("planning", "procurement"):
            continue
        for name, unit in spec.outputs.items():
            token = str(unit or "")
            if token in _BARE or token.startswith("currency"):
                continue
            if parse_unit(token) is None:
                offenders.append(f"{spec.name}.{name}={token}")
    assert offenders == []


@pytest.mark.parametrize("rate_unit", ["per day", "m2 per gang-day"])
@pytest.mark.parametrize(
    "params",
    [
        {"quantity": 80, "productivity": 2, "productivity_rate": 2},
        {"quantity": 80, "productivity": 2},
    ],
)
def test_a_per_day_rate_prints_days_not_a_placeholder_or_manhours(params, rate_unit):
    params = {**params, "rate_unit": rate_unit}
    env = run_calculation("productivity_manpower_duration", params)
    assert env["status"] == "success", env
    text = render.render_calculation(env, params)
    assert "Result: duration 40 days." in text
    assert "manhours required" not in text.lower()
    assert "40 h" not in text
    assert "duration_units" not in text
    assert "duration units" not in text.lower()


def test_manhour_productivity_keeps_the_declared_hour_unit():
    params = {"quantity": 80, "productivity": 2}
    env = run_calculation("productivity_manpower_duration", params)
    text = render.render_calculation(env, params)
    assert "manhours required 40 h" in text
