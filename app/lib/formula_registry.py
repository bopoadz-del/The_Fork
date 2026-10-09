"""Formula registry: every calculation declares who owns it and what it does.

A formula is registered by declaring it where it is defined::

    @formula(owner="base", display_name="Rectangular volume",
             description="Volume of a rectangular prism.",
             inputs={"length_m": "m", "width_m": "m", "depth_m": "m"},
             outputs={"volume_m3": "m3"})
    def some_volume(length_m, width_m, depth_m): ...

``owner`` is ``"base"`` (general formulas every hat is offered: unit
conversion, geometry, areas, volumes, percentages, simple rates) or one hat
(``design``, ``quantities``, ``commercial``, ``contracts``, ``planning``,
``procurement``, ``qaqc``, ``safety``). A turn wearing a hat is offered the
base formulas plus that hat's own.

The declaration holds descriptions only -- never a project's value, rate or
worked example. ``display_name`` is what a user reads when an answer credits
the formula ("Delay damages per day -- platform calculator"); the function
name is an internal identifier and never shown. Built-in factors a formula falls back to are returned flagged
as indicative defaults by the formula itself.

Each input is declared as a :class:`Param`: its unit, the range a supplied
value must lie in, the words a user reads for it, and -- for a strength --
the material whose grade label states it ("C30" states a concrete strength
of 30 MPa). The input's type is its annotation in the function signature.
A supplied value of the wrong type or outside the range is never run; the
user is asked for it by its label.
"""
from __future__ import annotations

import inspect
import math
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

BASE = "base"
HATS = ("design", "quantities", "commercial", "contracts", "planning",
        "procurement", "qaqc", "safety")
OWNERS = (BASE,) + HATS


#: Kinds an input's annotation gives it. Only numeric kinds carry a range.
NUMERIC_KINDS = ("number", "integer", "series")


@dataclass(frozen=True)
class Param:
    """One input as declared: unit, inclusive range of a supplied value (in
    the unit the function receives), the words a user reads, and the
    material whose grade label states this input (``grade``)."""
    unit: str = "-"
    lo: Optional[float] = None
    hi: Optional[float] = None
    label: str = ""
    grade: str = ""


@dataclass(frozen=True)
class ParamSpec:
    name: str
    kind: str
    unit: str
    lo: Optional[float]
    hi: Optional[float]
    label: str
    grade: str
    required: bool
    default: Any = None


@dataclass(frozen=True)
class FormulaSpec:
    name: str
    fn: Callable[..., Any]
    owner: str
    description: str
    display_name: str = ""
    inputs: Dict[str, str] = field(default_factory=dict)
    outputs: Dict[str, str] = field(default_factory=dict)
    params: Dict[str, Param] = field(default_factory=dict)


_REGISTRY: Dict[str, FormulaSpec] = {}


def _declared_param(value: Union[str, Param]) -> Param:
    return value if isinstance(value, Param) else Param(unit=str(value or "-"))


def formula(
    *,
    owner: str,
    description: str,
    display_name: str,
    inputs: Dict[str, Union[str, Param]],
    outputs: Dict[str, str],
    name: Optional[str] = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Declare a formula. Raises at import on an unknown owner, an empty
    description, an empty display name or a duplicate name -- a formula
    without an owner, a description or a name a user can read cannot be
    registered at all."""
    if owner not in OWNERS:
        raise ValueError(f"unknown formula owner {owner!r}; one of {OWNERS}")
    if not (description or "").strip():
        raise ValueError("a formula needs a description")
    if not (display_name or "").strip():
        raise ValueError("a formula needs a display_name")

    def _register(fn: Callable[..., Any]) -> Callable[..., Any]:
        key = name or fn.__name__
        existing = _REGISTRY.get(key)
        if existing is not None and existing.fn is not fn:
            raise ValueError(f"formula {key!r} is declared twice")
        params = {k: _declared_param(v) for k, v in inputs.items()}
        _REGISTRY[key] = FormulaSpec(key, fn, owner, description.strip(),
                                     display_name.strip(),
                                     {k: p.unit for k, p in params.items()},
                                     dict(outputs), params)
        fn.__formula__ = _REGISTRY[key]  # type: ignore[attr-defined]
        return fn

    return _register


def all_specs() -> List[FormulaSpec]:
    _load_all()
    return [spec for _key, spec in sorted(_REGISTRY.items())]


def specs_for(hat: Optional[str]) -> List[FormulaSpec]:
    """Formulas offered to a turn wearing ``hat``: base plus that hat's own.
    ``hat=None`` (no hat selected) is base only."""
    return [s for s in all_specs() if s.owner == BASE or (hat and s.owner == hat)]


def calculators() -> Dict[str, Callable[..., Any]]:
    """name -> function for every declared formula (what construction_calc runs)."""
    return {s.name: s.fn for s in all_specs()}


def get(name: str) -> Optional[FormulaSpec]:
    _load_all()
    return _REGISTRY.get(name)


def formula_modules() -> List[str]:
    """Every module of ``app.agents.base.formulas`` and of each
    ``app.agents.hats.<hat>.formulas`` -- discovered, so a hat's formulas are
    files in its own package and adding one is no core edit."""
    import importlib
    import pkgutil

    import app.agents.hats as hats

    packages = ["app.agents.base.formulas"] + [
        f"app.agents.hats.{info.name}.formulas"
        for info in sorted(pkgutil.iter_modules(hats.__path__), key=lambda i: i.name)
        if info.ispkg
    ]
    mods: List[str] = []
    for pkg in packages:
        try:
            package = importlib.import_module(pkg)
        except ModuleNotFoundError:
            continue
        mods.extend(f"{pkg}.{info.name}"
                    for info in sorted(pkgutil.iter_modules(package.__path__), key=lambda i: i.name))
    return mods


_loaded = False


def _load_all() -> None:
    global _loaded
    if _loaded:
        return
    import importlib

    for mod in formula_modules():
        importlib.import_module(mod)
    _loaded = True


def annotation_kind(annotation: Any, default: Any = inspect.Parameter.empty) -> str:
    """``number``, ``integer``, ``series`` (a list of numbers), ``flag``,
    ``table`` (rows or a mapping) or ``text`` -- read from the signature."""
    if annotation is inspect.Parameter.empty:
        if isinstance(default, bool):
            return "flag"
        if isinstance(default, int):
            return "integer"
        return "number" if isinstance(default, float) else "text"
    if isinstance(annotation, type):
        raw = annotation.__name__
    else:
        raw = str(annotation)
    low = raw.replace("typing.", "").replace(" ", "").lower()
    if "bool" in low:
        return "flag"
    if "dict" in low:
        return "table"
    if "list[" in low or low in ("list", "optional[list]"):
        return "series"
    if "float" in low:
        return "number"
    if "int" in low:
        return "integer"
    return "text"


def parameters(spec: FormulaSpec) -> Dict[str, ParamSpec]:
    """Every declared input of ``spec`` with its kind from the signature."""
    sig = inspect.signature(spec.fn)
    out: Dict[str, ParamSpec] = {}
    for key, declared in spec.params.items():
        param = sig.parameters.get(key)
        if param is None:
            continue
        required = param.default is inspect.Parameter.empty
        out[key] = ParamSpec(
            name=key, kind=annotation_kind(param.annotation, param.default),
            unit=declared.unit, lo=declared.lo, hi=declared.hi,
            label=declared.label, grade=declared.grade, required=required,
            default=None if required else param.default,
        )
    return out


# A grade label states a strength in MPa: the letter names the material and
# the first number is the strength (EN 206 / BS 8500 concrete C<cylinder>[/<cube>],
# EN 10025 structural steel S<yield>, BS 4449 / EN 10080 reinforcement B<yield>[A-C]).
_GRADE_FAMILIES: Dict[str, Tuple[str, float, float]] = {
    "c": ("concrete", 8.0, 120.0),
    "s": ("steel", 185.0, 1100.0),
    "b": ("rebar", 250.0, 700.0),
}
# Steel and reinforcement grades are written in capitals; a lowercase b or s
# before a number is a section width or a spacing.
GRADE_LABEL_RE = re.compile(
    r"(?<![A-Za-z0-9])([CSBc])(\d{1,4})(?:/(\d{1,4}))?([A-C])?(?![A-Za-z0-9/])",
)


def _grade_from_match(match: "re.Match[str]") -> Optional[Tuple[str, float, str]]:
    letter, first, second, suffix = match.groups()
    family, lo, hi = _GRADE_FAMILIES[letter.lower()]
    if (second and family != "concrete") or (suffix and family != "rebar"):
        return None
    strength = float(first)
    if not lo <= strength <= hi:
        return None
    return family, strength, f"{letter.upper()}{first}"


def grade_label(value: Any) -> Optional[Tuple[str, float, str]]:
    """``(material, strength_mpa, short_label)`` when ``value`` is a grade label."""
    if not isinstance(value, str):
        return None
    match = GRADE_LABEL_RE.fullmatch(value.strip())
    return _grade_from_match(match) if match else None


def grade_labels_in(text: str) -> List[Tuple[Tuple[int, int], str, float, str]]:
    """Every grade label written in ``text``: ``(span, material, strength, label)``."""
    found = []
    for match in GRADE_LABEL_RE.finditer(text or ""):
        grade = _grade_from_match(match)
        if grade:
            found.append((match.span(), *grade))
    return found


def _fmt_strength(strength: float) -> str:
    return str(int(strength)) if float(strength).is_integer() else str(strength)


def grade_value(spec: ParamSpec, strength: float, label: str) -> Any:
    """What a grade label supplies to an input of its material."""
    return strength if spec.kind in NUMERIC_KINDS else label


def _place_grade_labels(params: Dict[str, ParamSpec], values: Dict[str, Any]) -> Dict[str, Any]:
    """A grade label goes to the input of its own material, never to another."""
    out = dict(values)
    for key, value in list(values.items()):
        spec = params.get(key)
        if spec is None:
            continue
        if spec.grade and spec.kind == "text" and _number(value) is not None:
            letter = next(k for k, v in _GRADE_FAMILIES.items() if v[0] == spec.grade)
            value = f"{letter.upper()}{_fmt_strength(_number(value))}"
            out[key] = value
        grade = grade_label(value)
        if grade is None:
            continue
        family, strength, label = grade
        if spec.grade == family:
            out[key] = grade_value(spec, strength, label)
            continue
        owners = [k for k, p in params.items() if p.grade == family and out.get(k) in (None, "")]
        if len(owners) == 1:
            out[owners[0]] = grade_value(params[owners[0]], strength, label)
            del out[key]
    return out


_YES = ("true", "yes", "y", "1", "on")
_NO = ("false", "no", "n", "0", "off")


def _rejection(spec: ParamSpec, value: Any, reason: str) -> Dict[str, Any]:
    return {"parameter": spec.name, "label": spec.label, "unit": spec.unit, "value": value,
            "reason": reason, "lo": spec.lo, "hi": spec.hi, "kind": spec.kind}


def _number(value: Any) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _in_range(spec: ParamSpec, number: float) -> bool:
    return (spec.lo is None or number >= spec.lo) and (spec.hi is None or number <= spec.hi)


_SI_PREFIX = {"G": 1e9, "M": 1e6, "k": 1e3, "": 1.0, "c": 1e-2, "m": 1e-3}
_SI_UNIT_RE = re.compile(r"^(G|M|k|c|m|)(m|N|Pa|g)([1-4])?$")


def scale_hint(spec: ParamSpec, number: float) -> Optional[Dict[str, Any]]:
    """The other prefix of the declared SI unit under which an out-of-range
    figure lands in range -- the one nearest the middle of the range when
    several do -- or None."""
    m = _SI_UNIT_RE.match(spec.unit or "")
    if not m or number <= 0 or spec.lo is None or spec.hi is None or spec.hi <= 0:
        return None
    prefix, base, power = m.group(1), m.group(2), int(m.group(3) or 1)
    centre = (math.log10(max(spec.lo, spec.hi * 1e-12)) + math.log10(spec.hi)) / 2
    best: Optional[Dict[str, Any]] = None
    for other, size in _SI_PREFIX.items():
        if other == prefix:
            continue
        factor = (size / _SI_PREFIX[prefix]) ** power
        converted = number * factor
        if not _in_range(spec, converted) or converted <= 0:
            continue
        distance = abs(math.log10(converted) - centre)
        if best is None or distance < best["distance"]:
            best = {"unit": f"{other}{base}{power if power > 1 else ''}",
                    "value": converted, "exponent": round(math.log10(factor)), "distance": distance}
    if best is not None:
        best.pop("distance")
    return best


def check_inputs(name: str, values: Dict[str, Any]) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """Supplied ``values`` for formula ``name`` held to each input's declared
    type and range. Returns the values as the formula receives them and one
    rejection per value that cannot be run."""
    spec = get(name)
    if spec is None:
        return dict(values), []
    params = parameters(spec)
    out = _place_grade_labels(params, values)
    rejected: List[Dict[str, Any]] = []
    for key, value in list(out.items()):
        p = params.get(key)
        if p is None or value is None:
            continue
        if p.kind == "flag":
            if isinstance(value, bool):
                continue
            token = str(value).strip().lower()
            if token in _YES or token in _NO:
                out[key] = token in _YES
            else:
                rejected.append(_rejection(p, value, "not_yes_no"))
            continue
        if p.kind == "series":
            items = value if isinstance(value, (list, tuple)) else [value]
            numbers = [_number(v) for v in items]
            if not items:
                rejected.append(_rejection(p, value, "empty"))
            elif any(n is None for n in numbers):
                rejected.append(_rejection(p, value, "not_a_number"))
            elif not all(_in_range(p, n) for n in numbers):
                rejected.append(_rejection(p, value, "out_of_range"))
            continue
        if p.kind not in ("number", "integer"):
            continue
        number = _number(value)
        if number is None:
            rejected.append(_rejection(p, value, "not_a_number"))
            continue
        if p.kind == "integer":
            if not number.is_integer():
                rejected.append(_rejection(p, value, "not_whole"))
                continue
            out[key] = int(number)
        if not _in_range(p, number):
            row = _rejection(p, value, "out_of_range")
            hint = scale_hint(p, number)
            if hint:
                row["scale"] = hint
            rejected.append(row)
    return out, rejected
