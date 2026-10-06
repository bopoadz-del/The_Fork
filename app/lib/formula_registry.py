"""Formula registry: every calculation declares who owns it and what it does.

A formula is registered by declaring it where it is defined::

    @formula(owner="base", description="Volume of a rectangular prism.",
             inputs={"length_m": "m", "width_m": "m", "depth_m": "m"},
             outputs={"volume_m3": "m3"})
    def some_volume(length_m, width_m, depth_m): ...

``owner`` is ``"base"`` (general formulas every hat is offered: unit
conversion, geometry, areas, volumes, percentages, simple rates) or one hat
(``design``, ``quantities``, ``commercial``, ``contracts``, ``planning``,
``procurement``, ``qaqc``, ``safety``). A turn wearing a hat is offered the
base formulas plus that hat's own.

The declaration holds descriptions only -- never a project's value, rate or
worked example. Built-in factors a formula falls back to are returned flagged
as indicative defaults by the formula itself.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

BASE = "base"
HATS = ("design", "quantities", "commercial", "contracts", "planning",
        "procurement", "qaqc", "safety")
OWNERS = (BASE,) + HATS


@dataclass(frozen=True)
class FormulaSpec:
    name: str
    fn: Callable[..., Any]
    owner: str
    description: str
    inputs: Dict[str, str] = field(default_factory=dict)
    outputs: Dict[str, str] = field(default_factory=dict)


_REGISTRY: Dict[str, FormulaSpec] = {}


def formula(
    *,
    owner: str,
    description: str,
    inputs: Dict[str, str],
    outputs: Dict[str, str],
    name: Optional[str] = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Declare a formula. Raises at import on an unknown owner, an empty
    description or a duplicate name -- a formula without an owner or a
    description cannot be registered at all."""
    if owner not in OWNERS:
        raise ValueError(f"unknown formula owner {owner!r}; one of {OWNERS}")
    if not (description or "").strip():
        raise ValueError("a formula needs a description")

    def _register(fn: Callable[..., Any]) -> Callable[..., Any]:
        key = name or fn.__name__
        existing = _REGISTRY.get(key)
        if existing is not None and existing.fn is not fn:
            raise ValueError(f"formula {key!r} is declared twice")
        _REGISTRY[key] = FormulaSpec(key, fn, owner, description.strip(),
                                     dict(inputs), dict(outputs))
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


#: Modules whose import declares formulas. Importing them is what fills the
#: registry; a new formula module is one line here.
FORMULA_MODULES = (
    "app.lib.construction_formulas",
    "app.lib.construction_formulas_additions",
    "app.lib.construction_formulas_general",
    "app.lib.construction_formulas_structural_steel",
    "app.lib.construction_formulas_loads",
    "app.lib.construction_formulas_quantities",
    "app.lib.construction_formulas_earthwork",
    "app.lib.construction_formulas_beam_analysis",
    "app.lib.construction_formulas_structural_rc",
    "app.lib.construction_formulas_qc",
    "app.lib.construction_formulas_commercial",
    "app.lib.construction_formulas_planning",
    "app.lib.construction_formulas_safety",
    "app.lib.construction_formulas_reference_tables",
    "app.lib.construction_formulas_columns",
    "app.lib.construction_formulas_masonry",
    "app.core.construction_knowledge",
)

_loaded = False


def _load_all() -> None:
    global _loaded
    if _loaded:
        return
    import importlib

    for mod in FORMULA_MODULES:
        importlib.import_module(mod)
    _loaded = True
