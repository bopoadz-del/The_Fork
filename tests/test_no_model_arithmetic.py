"""No figure in an answer is arithmetic the model did.

One rule for the whole registry and every figure form, so a formula or a
way of writing a product or a quotient added later is covered without
editing this file:

* a figure a registered calculator (or the formula executor) returned this
  turn is kept and carries its credit line;
* a figure the model derived from that result (a re-division, a re-label,
  a silent unit rescale) is removed;
* a figure that is only the value of arithmetic on the user's own operands,
  with no tool behind it, is either labelled improvised working (the
  executor's sandbox produced it) or removed;
* an invented example that introduces numbers the user did not state is
  removed.

Generated over the formula registry and over figure forms, not a list of
known strings.
"""
from __future__ import annotations

import inspect
import json
import math
import random
import re

import pytest

from app.agents import answer_exit
from app.agents import provenance_trail as pt
from app.agents.citation_provenance import figure_provenance
from app.lib import formula_registry, source_labels
from app.lib.construction_formulas import run_calculation
from app.lib.formula_registry import NUMERIC_KINDS, all_specs

try:
    from app.agents.citation_provenance import figure_gaps
except ImportError:  # the check does not exist yet: every case fails, not collection
    def figure_gaps():
        return []

_IMPROVISED = getattr(source_labels, "IMPROVISED_WORKING", "")
_SOURCE_IMPROVISED = getattr(pt, "SOURCE_IMPROVISED", "improvised_working")

_RNG = 20261009
_NUMERIC = NUMERIC_KINDS
_SCALAR = ("number", "integer")
_UNITLESS = frozenset({"-", "ratio", "count", "no", "nr", "fraction", ""})


def _inside(p):
    lo, hi = p.lo, p.hi
    if lo is None or hi is None:
        return None
    floor = lo if lo > 0 else hi * 1e-3
    middle = math.sqrt(floor * hi) if floor > 0 else (lo + hi) / 2
    middle = float(f"{middle:.2g}")
    if p.kind == "integer":
        return float(max(math.ceil(lo), round(middle)))
    return middle if lo <= middle <= hi else (lo + hi) / 2


def _baseline(spec):
    values = {}
    declared = formula_registry.parameters(spec)
    for name, param in inspect.signature(spec.fn).parameters.items():
        if param.default is not inspect.Parameter.empty:
            continue
        p = declared.get(name)
        if p is None or p.kind not in _NUMERIC:
            return None
        value = _inside(p)
        if value is None:
            return None
        values[name] = [value] * 2 if p.kind == "series" else value
    return values


def _shown(value: float) -> str:
    if float(value).is_integer() and abs(value) < 1e12:
        return str(int(value))
    if abs(value) >= 1e12:
        return f"{value:.0f}"
    if abs(value) < 1e-6 and value != 0:
        return f"{value:.12f}".rstrip("0").rstrip(".")
    return f"{value:.12g}"


def _writable(value: float) -> bool:
    text = _shown(value)
    return bool(re.fullmatch(r"-?\d+(?:\.\d+)?", text)) and math.isfinite(value)


def _known_unit(unit: str) -> str:
    raw = (unit or "").strip()
    low = raw.lower()
    if low in _UNITLESS:
        return "m"
    if low == "currency":
        return "$"
    if low in ("%", "percent", "fraction"):
        return "%"
    if "m3" in low or "m³" in low:
        return "m3"
    if "m2" in low or "m²" in low:
        return "m2"
    if low in ("mm", "cm", "km", "m", "kg", "t", "day", "days", "hour", "hours", "hrs"):
        return raw
    return "m"


def _write_fig(value: float, unit: str) -> str:
    unit = _known_unit(unit)
    if unit == "$":
        return f"${_shown(value)}"
    if unit == "%":
        return f"{_shown(value)}%"
    return f"{_shown(value)} {unit}"


def _close(left: float, right: float) -> bool:
    tol = 1e-3 * max(1.0, abs(left), abs(right))
    return abs(left - right) <= tol or abs(round(left, 3) - round(right, 3)) <= 1e-9


def _in_pool(value: float, pool) -> bool:
    return any(_close(value, p) for p in pool)


def _result_numbers(result: dict) -> list[float]:
    found = []
    for key, val in (result or {}).items():
        if key == "notes" or isinstance(val, bool) or not isinstance(val, (int, float)):
            continue
        if val == val and val != float("inf"):
            found.append(float(val))
    return found


def _alien(pool) -> float:
    """A figure that is not in ``pool``: a generated re-division of one of them."""
    vals = [p for p in pool if p and p == p]
    for n in (2, 3, 4, 5, 7, 8, 9, 11, 13):
        for base in vals[:8] or [1.0]:
            for cand in (base / n, base * n, abs(base) / n + n):
                if cand > 0 and not _in_pool(cand, pool):
                    return float(f"{cand:.12g}")
    acc = 1.0
    for p in vals[:6]:
        acc = acc * 2 + abs(p)
    while _in_pool(acc, pool):
        acc = acc * 2 + 1
    return acc


def _runnable():
    rows = []
    for spec in all_specs():
        base = _baseline(spec)
        if not base:
            continue
        env = run_calculation(spec.name, dict(base))
        if env.get("status") != "success" or not isinstance(env.get("result"), dict):
            continue
        nums = _result_numbers(env["result"])
        if not nums or not _writable(nums[0]):
            continue
        key = next((k for k, v in env["result"].items()
                    if not isinstance(v, bool) and isinstance(v, (int, float))
                    and k != "notes"), None)
        unit = spec.outputs.get(key or "", "") or next(iter(spec.outputs.values()), "m")
        pool = set(nums) | {float(v) for v in base.values()
                            if isinstance(v, (int, float)) and not isinstance(v, bool)}
        for seq in base.values():
            if isinstance(seq, list):
                pool.update(float(x) for x in seq if isinstance(x, (int, float)))
        derived = _alien(pool)
        rows.append((spec.name, dict(base), env, nums[0], unit, derived))
    return rows


RUNNABLE = _runnable() or [("registry", {}, {"result": {}}, 1.0, "m", 2.0)]


def _ask(params: dict) -> str:
    parts = []
    for key, val in params.items():
        words = key.replace("_", " ")
        if isinstance(val, (list, tuple)):
            parts.append(f"{words} {', '.join(_shown(float(v)) for v in val)}")
        elif isinstance(val, (int, float)) and not isinstance(val, bool):
            parts.append(f"{words} {_shown(float(val))}")
        else:
            parts.append(f"{words} {val}")
    return "Calculate with " + ", ".join(parts) + "."


def _calc_messages(name, params, env, ask):
    return [
        {"role": "user", "content": ask},
        {"role": "assistant", "content": "", "tool_calls": [{
            "id": "t1", "type": "function",
            "function": {"name": "construction_calc",
                         "arguments": json.dumps({"calculation": name, "params": params})}}]},
        {"role": "tool", "tool_call_id": "t1", "name": "construction_calc",
         "content": json.dumps(env)},
    ]


def _turn(messages, rag=None):
    turn = answer_exit.Turn()
    answer_exit.note_evidence(rag, messages, turn)
    return turn


def _has(text: str, value: float) -> bool:
    for tok in re.findall(r"-?\d[\d,]*(?:\.\d+)?", text or ""):
        try:
            if _close(float(tok.replace(",", "")), value):
                return True
        except ValueError:
            continue
    return False


def test_the_registry_gives_cases():
    assert len(RUNNABLE) >= 20, len(RUNNABLE)


@pytest.mark.parametrize("formula,params,env,result,unit,derived", RUNNABLE,
                         ids=[r[0] for r in RUNNABLE])
def test_a_calculator_figure_is_kept_and_a_derived_one_is_removed(
        formula, params, env, result, unit, derived):
    ask = _ask(params)
    messages = _calc_messages(formula, params, env, ask)
    kept = _write_fig(result, unit)
    answer = (
        f"The result is {kept}. A further split is {_write_fig(derived, 'm2')} "
        f"per worker-hour."
    )
    out = answer_exit.check_text(answer, turn=_turn(messages))
    assert _has(out, result), (formula, out)
    assert not _has(out, derived), (formula, derived, out)
    assert "platform calculator" in out.lower(), out
    assert any(g["kind"] == "removed" and _has(g["figure"], derived)
               for g in figure_gaps()), figure_gaps()


@pytest.mark.parametrize("formula,params,env,result,unit,derived", RUNNABLE,
                         ids=[r[0] for r in RUNNABLE])
def test_figure_provenance_does_not_treat_derived_working_as_a_source(
        formula, params, env, result, unit, derived):
    ask = _ask(params)
    messages = _calc_messages(formula, params, env, ask)
    answer = (
        f"The result is {_write_fig(result, unit)}. "
        f"A further split is {_write_fig(derived, 'm2')} per worker-hour."
    )
    out, trail = figure_provenance(answer, None, messages, enforce=True)
    assert _has(out, result), (formula, out)
    assert not _has(out, derived), (formula, derived, out)
    assert all(e.get("source") != pt.SOURCE_CALCULATOR or not e.get("working")
               for e in trail)


@pytest.mark.parametrize("formula,params,env,result,unit,derived", RUNNABLE,
                         ids=[r[0] for r in RUNNABLE])
def test_a_model_unit_rescale_of_a_tool_figure_is_removed(
        formula, params, env, result, unit, derived):
    ask = _ask(params)
    messages = _calc_messages(formula, params, env, ask)
    scaled = result * 1000
    pool = set(_result_numbers(env["result"])) | {
        float(v) for v in params.values()
        if isinstance(v, (int, float)) and not isinstance(v, bool)
    }
    if _in_pool(scaled, pool) or not math.isfinite(scaled):
        pytest.skip("rescale collides with a tool or user figure")
    answer = (
        f"The result is {_write_fig(result, unit)}. "
        f"That is {_write_fig(scaled, 'mm')} in millimetres."
    )
    out = answer_exit.check_text(answer, turn=_turn(messages))
    assert _has(out, result), out
    assert not _has(out, scaled), (formula, scaled, out)


def _pairs(kind: str, n: int = 8):
    rng = random.Random(_RNG + (0 if kind == "product" else 1))
    out = []
    seen = set()
    while len(out) < n:
        a = rng.choice((2, 3, 4, 5, 6, 8, 10, 12, 15, 16, 20, 24, 25, 30, 36, 40, 45, 48, 50))
        b = rng.choice((2, 3, 4, 5, 6, 8, 10, 12))
        if a == b:
            continue
        c = a * b if kind == "product" else a / b
        if not c == c:
            continue
        key = (a, b, c)
        if key in seen:
            continue
        seen.add(key)
        out.append((a, b, c))
    return out


PRODUCT_FORMS = (
    "{a} m × {b} m = {c} {unit}",
    "{a} m x {b} m = {c} {unit}",
    "{a}×{b}={c} {unit}",
    "{a} m * {b} m = {c} {unit}",
    "{a} m · {b} m = {c} {unit}",
)
QUOTIENT_FORMS = (
    "{a} ÷ {b} = {c} {unit}",
    "{a} / {b} = {c} {unit}",
    "{a} {unit} ÷ {b} days = {c} {unit} per day",
)
UNITS = ("m2", "m3", "m", "kg", "days", "hours")


def _form_cases():
    cases = []
    for form in PRODUCT_FORMS:
        for unit in UNITS:
            for a, b, c in _pairs("product"):
                cases.append((form, a, b, c, unit))
    for form in QUOTIENT_FORMS:
        for unit in UNITS:
            for a, b, c in _pairs("quotient"):
                cases.append((form, a, b, c, unit))
    return cases


FORMS = _form_cases()


def test_the_figure_forms_give_cases():
    assert len(FORMS) >= 40, len(FORMS)


@pytest.mark.parametrize("form,a,b,c,unit", FORMS,
                         ids=[f"{i}" for i in range(len(FORMS))])
def test_user_operand_arithmetic_is_improvised_or_removed(form, a, b, c, unit):
    ask = f"A is {a} m and B is {b} m. What quantity follows?"
    answer = "Working: " + form.format(a=_shown(a), b=_shown(b), c=_shown(c), unit=unit) + "."
    out = answer_exit.check_text(answer, turn=_turn([{"role": "user", "content": ask}]))
    if _has(out, float(c)):
        assert _IMPROVISED and _IMPROVISED.lower() in out.lower(), out
        assert any(g["kind"] == "improvised_working" for g in figure_gaps()), figure_gaps()
    else:
        assert not _has(out, float(c)), out
        assert any(g["kind"] == "removed" for g in figure_gaps()), figure_gaps()


EXAMPLE_FORMS = (
    "If the crew was {n} workers on {h}-hour days, that's {lh} labour-hours "
    "and the rate would be ≈ {rate} m2 per hour.",
    "For example, {n} people on {h}-hour days give {lh} hours and about {rate} m2/hour.",
    "Suppose {n} workers do {h}-hour days: {lh} labour-hours, roughly {rate} m2 per hour.",
)


def _example_cases():
    rng = random.Random(_RNG + 2)
    cases = []
    while len(cases) < 6:
        qty = rng.choice((120, 200, 300, 360, 400, 480, 500, 600))
        days = rng.choice((4, 5, 6, 8, 10))
        n = rng.choice((7, 8, 9, 11, 12))
        h = rng.choice((7, 9, 10, 11))
        if len({qty, days, n, h}) < 4:
            continue
        lh = n * h * days
        rate = qty / lh
        cases.append((qty, days, n, h, lh, rate))
    return cases


EXAMPLES = [(form, *row) for form in EXAMPLE_FORMS for row in _example_cases()]


@pytest.mark.parametrize("form,qty,days,n,h,lh,rate", EXAMPLES,
                         ids=[f"{i}" for i in range(len(EXAMPLES))])
def test_an_invented_example_with_numbers_is_removed(form, qty, days, n, h, lh, rate):
    ask = f"Calculate the rate if {qty} m2 was installed in {days} days."
    answer = (
        f"The installed quantity is {qty} m2 over {days} days. "
        + form.format(n=n, h=h, lh=_shown(lh), rate=_shown(rate))
    )
    out = answer_exit.check_text(answer, turn=_turn([{"role": "user", "content": ask}]))
    assert _has(out, float(qty)) and _has(out, float(days)), out
    assert not _has(out, float(lh)), (lh, out)
    assert not _has(out, float(rate)), (rate, out)


def _executor_cases():
    rng = random.Random(_RNG + 3)
    rows = []
    while len(rows) < 6:
        a = rng.choice((2.5, 3.2, 4.0, 5.5, 6.3, 7.5, 8.0, 9.6))
        b = rng.choice((1.5, 2.0, 2.4, 3.0, 3.5, 4.5, 5.0))
        c = a * b
        if c == c:
            rows.append((a, b, c))
    return rows


@pytest.mark.parametrize("a,b,c", _executor_cases())
def test_an_executor_figure_is_kept_with_improvised_credit(a, b, c):
    messages = [
        {"role": "user", "content": f"Area of a {_shown(a)} m by {_shown(b)} m panel."},
        {"role": "assistant", "content": "", "tool_calls": [{
            "id": "e1", "function": {"name": "formula_executor_v2",
                                     "arguments": json.dumps({
                                         "task": "area", "variables": {"a": a, "b": b}})}}]},
        {"role": "tool", "name": "formula_executor_v2", "tool_call_id": "e1",
         "content": json.dumps({"status": "success",
                                "generated_code": "result = a * b",
                                "result": c, "task": "area"})},
    ]
    answer = f"The area is {_write_fig(c, 'm2')}."
    out = answer_exit.check_text(answer, turn=_turn(messages))
    assert _has(out, float(c)), out
    assert _IMPROVISED and _IMPROVISED.lower() in out.lower(), out
    _out, trail = figure_provenance(answer, None, messages, enforce=True)
    assert any(e.get("source") == _SOURCE_IMPROVISED for e in trail), trail
