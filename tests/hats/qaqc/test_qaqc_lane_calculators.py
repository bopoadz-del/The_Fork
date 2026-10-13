"""Every registered qaqc calculator: units, required-only runs, defaults named.

Generated from the hat's registry. Values are synthetic mid-range figures,
never a live probe or a named project.
"""
from __future__ import annotations

import pytest

from app.agents.hats.qaqc.defaults import (
    applied_defaults,
    encodes_hour_age_on_stress,
    install_default_disclosure,
    is_dimensional,
)
from app.lib.construction_formulas import run_calculation
from app.lib.formula_registry import NUMERIC_KINDS, all_specs, parameters

LANE = "qaqc"
install_default_disclosure(LANE)


def _lane_specs():
    return [spec for spec in all_specs() if spec.owner == LANE]


def _spread(lo: float, hi: float, index: int, n: int) -> float:
    """Distinct figures inside lo..hi, largest first so a capacity stays above a take-off."""
    if hi <= lo:
        return hi if hi > 0 else 1.0
    frac = 1.0 - (index + 1) / (n + 1)
    value = lo + frac * (hi - lo)
    if value <= 0 and hi > 0:
        value = hi * max(frac, 0.1)
    return value


def _synthetic_required(spec) -> dict:
    """A figure for every input that has no signature default."""
    required = [(name, p) for name, p in parameters(spec).items() if p.required]
    numeric = [row for row in required if row[1].kind in NUMERIC_KINDS]
    n = max(len(numeric), 1)
    values = {}
    seen_numeric = 0
    for name, p in required:
        if p.kind == "series":
            lo = 0.0 if p.lo is None else float(p.lo)
            hi = 10.0 if p.hi is None else float(p.hi)
            mid = _spread(lo, hi, seen_numeric, n)
            seen_numeric += 1
            values[name] = [mid, mid]
        elif p.kind == "integer":
            if p.lo is not None and p.hi is not None:
                values[name] = int(_spread(float(p.lo), float(p.hi), seen_numeric, n))
            else:
                values[name] = int(p.default if p.default is not None else 1)
            seen_numeric += 1
        elif p.kind in NUMERIC_KINDS:
            if p.lo is not None and p.hi is not None:
                values[name] = _spread(float(p.lo), float(p.hi), seen_numeric, n)
            else:
                values[name] = float(n - seen_numeric)
            seen_numeric += 1
        elif p.kind == "flag":
            values[name] = False
        else:
            values[name] = p.default if p.default not in (None, "") else "x"
    return values


LANE_SPECS = _lane_specs()
LANE_PARAMS = [
    (spec, name, p)
    for spec in LANE_SPECS
    for name, p in parameters(spec).items()
]


def _param_ids(rows):
    return [f"{spec.name}.{name}" for spec, name, _p in rows]


def _spec_ids(specs):
    return [spec.name for spec in specs]


@pytest.mark.parametrize("spec,name,p", LANE_PARAMS, ids=_param_ids(LANE_PARAMS))
def test_every_dimensional_param_declares_a_unit(spec, name, p):
    if not is_dimensional(p.unit, p.kind):
        return
    assert p.unit and p.unit.strip() not in ("", "-"), (
        f"{spec.name}.{name} is dimensional but declares no unit"
    )
    assert not encodes_hour_age_on_stress(name, p.unit, p.label or ""), (
        f"{spec.name}.{name} names a stress by a fixed hour-age"
    )


@pytest.mark.parametrize("spec", LANE_SPECS, ids=_spec_ids(LANE_SPECS))
def test_every_lane_calculator_runs_on_required_inputs_and_names_defaults(spec):
    given = _synthetic_required(spec)
    env = run_calculation(spec.name, given)
    assert env["status"] == "success", (
        f"{spec.name} refused required-only inputs {given}: {env}"
    )
    used = applied_defaults(spec.fn, given)
    if not used:
        return
    result = env.get("result") or {}
    rows = result.get("defaults_used") or []
    named = {row.get("parameter") for row in rows if isinstance(row, dict)}
    blob = " ".join(str(part) for part in (result, env)).lower()
    for key, value in used.items():
        assert key in named or key in result, (
            f"{spec.name} applied default {key}={value} but did not name it"
        )
        assert "default" in blob, f"{spec.name} did not disclose that defaults were used"


def test_striking_check_runs_on_stated_strength_and_hours():
    """Failing-first: only the measured strength and the proposed hours.

    Code minima stay on their defaults and must be named as defaults.
    The strength figure is a stress, not an hour-age.
    """
    given = {"strength_at_striking": 11.0, "proposed_hours": 10.0}
    env = run_calculation("formwork_striking_time", given)
    assert env["status"] == "success", env
    result = env["result"]
    assert result["actual_early_strength_n_mm2"] == pytest.approx(11.0)
    assert result["recommended_hours"] == pytest.approx(10.0)
    named = {row["parameter"] for row in result["defaults_used"]}
    assert named == {
        "design_required_strength",
        "ciria_surface_strength",
        "bs8110_minimum_hours",
    }
    blob = " ".join(str(part) for part in (result, result.get("notes"))).lower()
    assert "default" in blob
    assert "7-hour" not in blob
    assert "7 h" not in blob


def test_striking_check_binds_stated_figures_from_text():
    """The leftover unit figures are the strength and the proposed hours."""
    env = run_calculation("formwork_striking_time", {
        "text": "cube 11 N/mm2 at striking, strike at 10 h",
    })
    assert env["status"] == "success", env
    result = env["result"]
    assert result["actual_early_strength_n_mm2"] == pytest.approx(11.0)
    assert result["recommended_hours"] == pytest.approx(10.0)
    named = {row["parameter"] for row in result["defaults_used"]}
    assert "design_required_strength" in named
    assert "ciria_surface_strength" in named
    assert "bs8110_minimum_hours" in named
