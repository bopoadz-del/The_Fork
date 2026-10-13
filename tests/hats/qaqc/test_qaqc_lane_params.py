"""Lane registry and figures for the qaqc hat.

Generated from the hat's registered formulas, not from named probes.
A param is accepted only when it has a plain label, a unit or a closed
choice, and — for a number — a range. A formula is accepted only when it
recomputes a hand-checked figure.
"""
from __future__ import annotations

import ast
import inspect
import re
from pathlib import Path

import pytest

from app.lib.construction_formulas import run_calculation
from app.lib.formula_registry import NUMERIC_KINDS, all_specs, parameters

LANE = "qaqc"
_LANE_ROOT = Path(__file__).resolve().parents[3] / "app" / "agents" / "hats" / LANE
_UNIT_IN_LABEL = re.compile(
    r"\b\d+\s*(?:mm|m2|m3|m/s|n/mm2|kpa|kn|kg|h|m)\b",
    re.IGNORECASE,
)
_MEASURED = ("density", "strength", "fraction", "utilisation", "utilization")


def _lane_specs():
    return [spec for spec in all_specs() if spec.owner == LANE]


def _lane_params():
    rows = []
    for spec in _lane_specs():
        for name, p in parameters(spec).items():
            rows.append((spec, name, p))
    return rows


def _ids(rows):
    return [f"{spec.name}.{name}" for spec, name, _p in rows]


def _bakes_unit(label: str, unit: str) -> bool:
    words = (label or "").strip().lower().split()
    token = re.sub(r"[^a-z0-9%]", "", (unit or "").lower())
    if token and token not in {"-", "fraction"} and words and words[-1] == token:
        return True
    return bool(_UNIT_IN_LABEL.search(label or ""))


def _filler(spec, skip=()):
    values = {}
    for name, p in parameters(spec).items():
        if name in skip or not p.required:
            continue
        if p.kind == "series":
            lo = 0.0 if p.lo is None else p.lo
            hi = 10.0 if p.hi is None else p.hi
            values[name] = [lo, (lo + hi) / 2.0]
        elif p.kind == "integer":
            values[name] = int(p.default if p.default is not None else (p.lo or 1))
        elif p.kind in NUMERIC_KINDS:
            if p.lo is not None and p.hi is not None:
                values[name] = (p.lo + p.hi) / 2.0
            else:
                values[name] = 1.0
        elif p.kind == "flag":
            values[name] = False
        else:
            values[name] = p.default if p.default not in (None, "") else "x"
    return values


LANE_PARAMS = _lane_params()
TEXT_PARAMS = [(s, n, p) for s, n, p in LANE_PARAMS if p.kind == "text"]


@pytest.mark.parametrize("spec,name,p", LANE_PARAMS, ids=_ids(LANE_PARAMS))
def test_every_lane_param_has_a_plain_label(spec, name, p):
    label = (p.label or "").strip()
    assert label, f"{spec.name}.{name} has no display label"
    assert label != name
    assert "_" not in label
    assert not _bakes_unit(label, p.unit), (spec.name, name, label, p.unit)


@pytest.mark.parametrize("spec,name,p", LANE_PARAMS, ids=_ids(LANE_PARAMS))
def test_every_lane_param_has_a_unit_or_choice_and_a_range(spec, name, p):
    if p.kind == "text":
        return
    assert p.unit, f"{spec.name}.{name} declares no unit"
    if p.kind in NUMERIC_KINDS:
        assert p.lo is not None and p.hi is not None, f"{spec.name}.{name} has no range"
        assert p.lo < p.hi
    if any(word in name.lower() for word in _MEASURED):
        assert p.unit not in ("", "-"), f"{spec.name}.{name} is a measured input with no unit"


@pytest.mark.parametrize("spec,name,p", TEXT_PARAMS, ids=_ids(TEXT_PARAMS))
def test_every_lane_text_param_rejects_an_unknown_choice(spec, name, p):
    values = _filler(spec, skip=(name,))
    values[name] = "not-a-declared-choice"
    env = run_calculation(spec.name, values)
    assert env["status"] == "error", f"{spec.name}.{name} accepted an unknown choice"
    assert "not-a-declared-choice" in str(env.get("error") or env.get("question") or "")


def test_every_lane_formula_recomputes_a_hand_checked_figure():
    """One worked figure per qaqc formula, from the formula not a stored answer."""
    from app.agents.hats.qaqc.formulas.construction_formulas import fineness_modulus

    # ASTM C136: FM = Σ cumulative % retained / 100.
    # 0+2+15+40+68+88+97 = 310 → 3.10. A CSV string must parse as the series.
    assert fineness_modulus("0,2,15,40,68,88,97") == pytest.approx(3.10, abs=0.001)
    assert fineness_modulus([0, 2, 15, 40, 68, 88, 97]) == pytest.approx(3.10, abs=0.001)

    r = run_calculation("compaction_control", {
        "field_dry_density": 1.90, "max_dry_density": 2.00,
    })["result"]
    assert r["compaction_percent"] == pytest.approx(95.0, abs=0.01)

    r = run_calculation("formwork_striking_time", {"concrete_strength_7h": 2.0})["result"]
    assert r["recommended_hours"] == pytest.approx(12.0, abs=1e-9)
    notes = " ".join(r["notes"])
    assert "NG" not in notes
    assert r.get("verdict")
    assert "NG" not in r["verdict"]

    r = run_calculation("modulus_of_rupture", {"fck_n_mm2": 40})["result"]
    assert r["modulus_of_rupture_n_mm2"] == pytest.approx(4.8, abs=0.005)

    r = run_calculation("concrete_curing_time", {"target_strength_fraction": 0.70})["result"]
    assert r["days_to_target"] == pytest.approx(6.91, abs=0.05)

    r = run_calculation("concrete_cylinders", {
        "failure_load_kn": 530, "cylinder_diameter_mm": 150,
    })["result"]
    assert r["compressive_strength_mpa"] == pytest.approx(30.0, abs=0.05)

    r = run_calculation("concrete_shrinkage", {"time_days": 365})["result"]
    assert r["shrinkage_microstrain"] == pytest.approx(711.75, abs=0.5)

    r = run_calculation("concrete_thermal_cracking_check", {
        "core_temp_c": 65, "surface_temp_c": 50,
    })["result"]
    assert r["delta_t_c"] == pytest.approx(15.0, abs=0.05)
    assert r["thermal_cracking_risk"] is False

    r = run_calculation("grout_pressure_calc", {"tendon_duct_diameter_mm": 60})["result"]
    assert r["duct_area_mm2"] == pytest.approx(2827.43, abs=0.05)

    r = run_calculation("concrete_maturity_strength", {
        "temperature_history_c": [20, 22, 25, 24],
        "time_intervals_hours": [12, 12, 12, 12],
    })["result"]
    assert r["maturity_index_c_hrs"] == pytest.approx(1572.0, abs=0.5)

    r = run_calculation("bim_clash_tolerance", {"lod": 350})["result"]
    assert r["clash_tolerance_mm"] == pytest.approx(12.0, abs=1e-9)

    r = run_calculation("laser_scan_accuracy", {"range_m": 35})["result"]
    assert r["estimated_accuracy_mm"] == pytest.approx(7.0, abs=0.01)


def test_fineness_modulus_rejects_a_percentage_outside_0_to_100():
    from app.agents.hats.qaqc.formulas.construction_formulas import fineness_modulus

    out = fineness_modulus([0, 2, 15, 40, 68, 88, 97, 140])
    assert isinstance(out, dict) and out.get("error")
    assert "140" in out["error"] or "100" in out["error"]


def test_lod_outside_the_table_is_rejected_in_plain_words():
    env = run_calculation("bim_clash_tolerance", {"lod": 450})
    assert env["status"] == "error"
    text = str(env.get("error") or env.get("question") or "")
    assert "450" in text
    assert "[" not in text


def test_grout_diameter_outside_the_declared_range_is_rejected():
    env = run_calculation("grout_pressure_calc", {"tendon_duct_diameter_mm": 400})
    assert env["status"] == "error"
    text = str(env.get("error") or env.get("question") or "")
    assert "400" in text


def test_lane_source_names_no_company_or_client():
    """Lane code states general rules, never a company or client name."""
    named = []
    for path in _LANE_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                continue
            if re.search(r"\b(?:SABIC|Aramco|ADNOC|NEOM)\b", node.value):
                named.append(f"{path.relative_to(_LANE_ROOT.parent.parent.parent)}:{node.lineno}")
    assert not named, named


def test_lane_formulas_are_registered():
    names = {spec.name for spec in _lane_specs()}
    assert names, "qaqc hat registered no formulas"
    for required in (
        "fineness_modulus", "formwork_striking_time", "compaction_control",
        "modulus_of_rupture", "bim_clash_tolerance", "grout_pressure_calc",
        "laser_scan_accuracy", "concrete_curing_time",
    ):
        assert required in names
    assert inspect.isfunction(run_calculation)
