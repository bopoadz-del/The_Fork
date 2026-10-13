"""Every design and quantities formula declares a complete registry schema.

A complete entry has a display name, and each input has a label, a type,
and required or optional taken from its signature. A numeric input also
has a lower and upper limit. Its unit is one the converter can read, except
a generic quantity of work and the output or rate per unit of that quantity:
those are '-' so the figure keeps the unit the user wrote. A count of
discrete items stays 'count'.
"""
from __future__ import annotations

import re

from app.agents.base.formulas.construction_formulas_planning import parse_unit
from app.lib.construction_formulas import run_calculation
from app.lib.formula_registry import NUMERIC_KINDS, all_specs, parameters, schema_gaps

OWNERS = ("design", "quantities")

# A declared unit whose dimension is a count, including a rate of one.
_COUNT_UNIT = re.compile(r"(^|/)count($|/)")


def _count_family(unit: str) -> bool:
    return _COUNT_UNIT.search(str(unit or "").strip().lower()) is not None


def _discrete_item(name: str, label: str) -> bool:
    """A count of named items, not an amount of work."""
    if str(label or "").lower().startswith("number of "):
        return True
    if name.startswith(("num_", "n_")) or name.endswith("_count"):
        return True
    # One kind of item per another (doors per room), not per unit of work.
    return bool(re.fullmatch(r"[a-z]+_per_[a-z]+", name)) and not name.endswith("per_unit")


def _per_unit_or_daily(name: str, label: str, unit: str) -> bool:
    """An output or a rate per unit of a quantity, or per day of that quantity."""
    blob = f"{name} {label}".lower().replace("_", " ")
    if "per unit" in blob:
        return True
    if "daily" in blob and "output" in blob:
        return True
    return "/" in str(unit or "") and _count_family(unit)


def _generic_work(params: dict) -> set:
    """A quantity of work plus the output or rate per unit of it.

    Empty when the formula is not that shape, and when every count it
    declares is a discrete item.
    """
    rates = {
        name for name, param in params.items()
        if _per_unit_or_daily(name, param.label, param.unit)
        and not _discrete_item(name, param.label)
    }
    if not rates:
        return set()
    quantities = {
        name for name, param in params.items()
        if name not in rates
        and "quantity" in f"{name} {param.label}".lower()
        and not _discrete_item(name, param.label)
    }
    if not quantities:
        return set()
    countish = [name for name, param in params.items() if _count_family(param.unit)]
    if countish and all(_discrete_item(name, params[name].label) for name in countish):
        return set()
    return quantities | rates


def test_every_lane3_formula_declares_a_complete_schema():
    gaps = schema_gaps()
    seen = 0
    for spec in all_specs():
        if spec.owner not in OWNERS:
            continue
        seen += 1
        assert spec.display_name.strip(), spec.name
        params = parameters(spec)
        # '-' is the user's dimension. The core gap list still calls that a
        # missing unit; this lane treats it as the declaration.
        generic = _generic_work(params)
        users_unit = {f"formula:{spec.name}.{name}:unit" for name in generic}
        own = [
            g for g in gaps
            if g.startswith(f"formula:{spec.name}.") or g == f"formula:{spec.name}:display_name"
        ]
        assert [g for g in own if g not in users_unit] == [], (spec.name, own)
        for name, param in params.items():
            where = f"{spec.name}.{name}"
            assert param.label.strip(), where
            assert param.kind, where
            assert isinstance(param.required, bool), where
            if param.kind not in NUMERIC_KINDS:
                continue
            assert param.lo is not None and param.hi is not None, where
            assert param.lo < param.hi, where
            if name in generic:
                assert param.unit == "-", where
                continue
            assert param.unit and param.unit != "-", where
            assert parse_unit(param.unit) is not None, where
    assert seen > 0


def test_a_work_quantity_with_a_rate_is_not_a_count():
    """No count (or count/d, currency/count) where a companion is a per-unit
    rate or a per-day output of it, unless every such input counts discrete items."""
    seen = 0
    for spec in all_specs():
        if spec.owner not in OWNERS:
            continue
        params = parameters(spec)
        for name in _generic_work(params):
            assert not _count_family(params[name].unit), (spec.name, name, params[name].unit)
            assert params[name].unit == "-", (spec.name, name, params[name].unit)
            seen += 1
        countish = [name for name, param in params.items() if _count_family(param.unit)]
        if not countish or all(_discrete_item(name, params[name].label) for name in countish):
            continue
        rates = [
            name for name, param in params.items()
            if _per_unit_or_daily(name, param.label, param.unit)
        ]
        if not rates:
            continue
        offenders = [name for name in countish if not _discrete_item(name, params[name].label)]
        assert offenders == [], (spec.name, offenders, rates)
    assert seen > 0


def test_work_quantities_keep_the_area_or_volume_the_user_wrote():
    """500 m2 at 4 m2/bag with waste 1.05 is 131.25 bags; the same figures in
    m3 too. A line of 20 days at 350 plus 400 at 45 is 25,000, in m2 and in m3."""
    for unit in ("m2", "m3"):
        consumed = run_calculation("material_consumption", {
            "quantity_of_work": f"500 {unit}",
            "output_per_unit": f"4 {unit}/bag",
            "waste_factor": 1.05,
        })
        assert consumed["status"] == "success", consumed
        assert consumed["result"]["material_required"] == 131.25

        line = run_calculation("resource_line_cost", {
            "quantity": f"400 {unit}",
            "daily_output": 20,
            "day_rate": 350,
            "material_rate_per_unit": 45,
        })
        assert line["status"] == "success", line
        assert line["result"]["crew_days"] == 20
        assert line["result"]["labour_cost"] == 7000
        assert line["result"]["material_cost"] == 18000
        assert line["result"]["total_cost"] == 25000
