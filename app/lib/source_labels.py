"""What a user reads when an answer credits a calculation or a tool.

Every formula and tool declares a ``display_name`` where it is registered;
this module is the one place that turns a credit into words. Internal names
(``construction_calc``, ``delay_damages_daily``) are identifiers for code and
logs and never reach an answer. Input names are read as words, and an input
whose declared unit the name already spells ("length_m", unit "m") is shown
as "length 12 m".
"""
from __future__ import annotations

import re
from typing import Any, Dict, Optional

#: Shown after a formula's display name, so the reader knows it was computed.
CALCULATOR_SUFFIX = "platform calculator"

#: Units that carry no reading of their own after a number.
_UNITLESS = frozenset({"", "-", "currency", "ratio", "count", "no", "nr"})


def _norm(unit: str) -> str:
    return re.sub(r"[^a-z0-9%]", "", (unit or "").lower())


def formula_display_name(calculation: str) -> Optional[str]:
    from app.lib import formula_registry

    spec = formula_registry.get(calculation or "")
    return spec.display_name if spec and spec.display_name else None


def tool_display_name(tool: str) -> Optional[str]:
    from app.agents.core import tool_registry

    spec = tool_registry.get(tool or "")
    return spec.display_name if spec and spec.display_name else None


def _fmt_value(value: Any) -> str:
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if isinstance(value, int):
        return f"{value:,}"
    return str(value)


def input_phrase(key: str, value: Any, unit: str = "") -> str:
    """``contract_amount, 50000000`` -> "contract amount 50,000,000";
    ``length_m, 12, "m"`` -> "length 12 m"; ``rate_percent, 0.1, "%"`` -> "rate 0.1%"."""
    words = [w for w in str(key).split("_") if w]
    norm = _norm(unit)
    if len(words) > 1 and norm and (_norm(words[-1]) == norm
                                    or (norm == "%" and words[-1].lower() in ("pct", "percent"))):
        words = words[:-1]
    shown = _fmt_value(value)
    if norm == "%":
        shown += "%"
    elif norm not in _UNITLESS and not isinstance(value, (bool, str)):
        shown += f" {unit}"
    return f"{' '.join(words)} {shown}".strip()


def calculator_label(calculation: str, inputs: Optional[Dict[str, Any]] = None) -> str:
    """"Delay damages per day -- platform calculator (rate 0.1%, contract amount 50,000,000)"."""
    from app.lib import formula_registry

    spec = formula_registry.get(calculation or "")
    name = spec.display_name if spec and spec.display_name else "Calculation"
    units = spec.inputs if spec else {}
    shown = ", ".join(input_phrase(k, v, units.get(k, "")) for k, v in (inputs or {}).items()
                      if v not in (None, ""))
    return f"{name} — {CALCULATOR_SUFFIX}" + (f" ({shown})" if shown else "")


def tool_label(tool: str, inputs: Optional[Dict[str, Any]] = None) -> str:
    """"Payment certificate (gross valuation 2,400,000, retention 10%)"."""
    name = tool_display_name(tool) or "Platform tool"
    shown = ", ".join(input_phrase(k, v) for k, v in (inputs or {}).items()
                      if v not in (None, "") and not isinstance(v, (dict, list)))
    return name + (f" ({shown})" if shown else "")
