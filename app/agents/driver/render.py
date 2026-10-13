"""What the model and the user read of a calculation: prose, never raw results.

One renderer for every registry formula. It reads the formula's declared
display name, its inputs' display labels and units, and its outputs' units,
so a formula added tomorrow renders the same way with no new code. Parameter
and formula identifiers (``contract_amount``, ``delay_damages_daily``) never
appear in its output.

The exit check uses the same declarations the other way round: an answer
that still writes an identifier the registry knows is rewritten to the name a
user reads (``readable``) before it leaves.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

from app.lib.source_labels import CALCULATOR_SUFFIX, input_phrase

_LOG = logging.getLogger(__name__)
_MAX_LIST = 8
_NOT_OUTPUTS = frozenset({"note", "notes", "standard"})


def _spec(calculation: str):
    from app.lib import formula_registry
    return formula_registry.get(calculation or "")


def _value(value: Any) -> Optional[str]:
    if isinstance(value, (dict, list, tuple, set)):
        return None
    if value is None or value == "":
        return None
    return input_phrase("", value).strip()


def _shown_unit(unit: str) -> str:
    """A declared unit as it is printed beside a figure. A unit written as
    words that is not a unit ("same unit as specified") is a note about the
    figure, not its unit, and is not printed."""
    unit = str(unit or "").strip()
    if not unit or " " not in unit:
        return unit
    try:
        from app.agents.base.formulas.construction_formulas_planning import parse_unit

        return unit if parse_unit(unit) is not None else ""
    except Exception:  # noqa: BLE001 -- rendering never fails on a unit
        _LOG.warning("unit parser failed on declared unit %r; not printed", unit, exc_info=True)
        return ""


def _phrases(values: Dict[str, Any], labels: Dict[str, str], units: Dict[str, str]) -> List[str]:
    out = []
    for key, value in values.items():
        if _value(value) is None:
            continue
        unit = _shown_unit(units.get(key, ""))
        phrase = input_phrase(key, value, unit)
        label = labels.get(key)
        if label:  # the declared label replaces the words built from the key
            head = input_phrase(key, "", unit).strip()
            phrase = label + phrase[len(head):] if phrase.startswith(head) else f"{label} {phrase}"
        out.append(phrase)
    return out


def render_calculation(envelope: Dict[str, Any], inputs: Optional[Dict[str, Any]] = None) -> str:
    """A calculator envelope (``run_calculation``'s result) as prose."""
    from app.lib import formula_registry

    calc = str(envelope.get("calculation") or "")
    spec = _spec(calc)
    name = spec.display_name if spec and spec.display_name else "Calculation"
    head = f"{name} — {CALCULATOR_SUFFIX}"
    question = envelope.get("question")
    if envelope.get("status") != "success":
        if question:
            return f"{head}: needs input. Ask the user exactly this: {question}"
        return f"{head}: did not run. Ask the user for the inputs it needs, by their labels."
    params = formula_registry.parameters(spec) if spec else {}
    labels = {k: p.label for k, p in params.items() if p.label}
    in_units = {k: p.unit for k, p in params.items()}
    result = envelope.get("result") if isinstance(envelope.get("result"), dict) else {}
    used = {k: v for k, v in (inputs or {}).items() if k in params}
    lines = [head + "."]
    if used:
        lines.append("Inputs: " + "; ".join(_phrases(used, labels, in_units)) + ".")
    lines += result_lines(envelope, inputs)
    for key in ("standard", "note"):
        if isinstance(result.get(key), str) and result[key].strip():
            lines.append(result[key].strip())
    notes = result.get("notes")
    if isinstance(notes, list):
        lines += [str(n) for n in notes[:_MAX_LIST] if str(n).strip()]
    return "\n".join(lines)


def _outputs(envelope: Dict[str, Any], inputs: Optional[Dict[str, Any]]):
    from app.lib import formula_registry

    spec = _spec(str(envelope.get("calculation") or ""))
    params = formula_registry.parameters(spec) if spec else {}
    result = envelope.get("result") if isinstance(envelope.get("result"), dict) else {}
    used = {k for k in (inputs or {}) if k in params}
    outputs = {k: v for k, v in result.items() if k not in used and k not in _NOT_OUTPUTS}
    labels = {k: p.label for k, p in params.items() if p.label}
    return outputs, labels, (dict(spec.outputs) if spec else {})


def _items(value: Any) -> List[str]:
    """A list output's rows, each as written (rows of plain values only)."""
    if not isinstance(value, (list, tuple)):
        return []
    return [str(v).strip() for v in value[:_MAX_LIST]
            if not isinstance(v, (dict, list, tuple, set)) and str(v).strip()]


def result_lines(envelope: Dict[str, Any], inputs: Optional[Dict[str, Any]] = None) -> List[str]:
    """A successful calculation's result: its figures, then each list output's
    rows, one per line."""
    outputs, labels, units = _outputs(envelope, inputs)
    lines = []
    shown = _phrases(outputs, labels, units)
    if shown:
        lines.append("Result: " + "; ".join(shown) + ".")
    for key, value in outputs.items():
        rows = _items(value)
        if rows:
            lines.append(f"{labels.get(key) or input_phrase(key, '').strip().capitalize()}:")
            lines += [f"- {row}" for row in rows]
    return lines


_NUMBER = re.compile(r"-?\d+(?:[.,]\d+)*")


def _numbers(text: str) -> List[float]:
    out = []
    for raw in _NUMBER.findall(text or ""):
        try:
            out.append(float(raw.replace(",", "")))
        except ValueError:
            continue
    return out


def carried(envelope: Dict[str, Any], inputs: Optional[Dict[str, Any]], answer: str) -> bool:
    """Does the answer carry this calculation's result: every figure it
    produced (to the rounding a writer would use) and every row it listed?"""
    outputs, _labels, _units = _outputs(envelope, inputs)
    written = _numbers(answer)
    text = (answer or "").lower()
    for value in outputs.values():
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            if not any(abs(w - value) <= max(abs(value) * 0.005, 0.005) for w in written):
                return False
        elif any(row.lower() not in text for row in _items(value)):
            return False
    return True


def _identifier_map() -> Dict[str, str]:
    """Every identifier-shaped registry key -> the words a user reads.
    Identifier-shaped = contains an underscore: plain words a key happens to
    share ("span", "load") are never touched."""
    from app.agents.core import tool_registry
    from app.lib import formula_registry

    out: Dict[str, str] = {}
    for spec in formula_registry.all_specs():
        if "_" in spec.name:
            out[spec.name] = spec.display_name or spec.name.replace("_", " ")
        for key, p in formula_registry.parameters(spec).items():
            if "_" in key and key not in out:
                out[key] = p.label or input_phrase(key, "", p.unit).strip() or key.replace("_", " ")
    tool_registry.load()
    for name, t in tool_registry._TOOLS.items():
        if "_" in name:
            out[name] = t.display_name or name.replace("_", " ")
    return out


_MAP: Dict[int, tuple] = {}


def _compiled():
    from app.lib import formula_registry

    key = len(formula_registry.all_specs())
    hit = _MAP.get(key)
    if hit is None:
        mapping = _identifier_map()
        names = sorted(mapping, key=len, reverse=True)
        rx = re.compile(r"(?<![A-Za-z0-9_])`?(" + "|".join(map(re.escape, names)) + r")`?(?![A-Za-z0-9_])") if names else None
        hit = (rx, mapping)
        _MAP.clear()
        _MAP[key] = hit
    return hit


def readable(text: str) -> tuple:
    """``text`` with every registry identifier replaced by its display name or
    label, and how many were replaced."""
    rx, mapping = _compiled()
    if not text or rx is None:
        return text, 0
    count = 0

    def sub(m: "re.Match[str]") -> str:
        nonlocal count
        count += 1
        return mapping[m.group(1)]
    return rx.sub(sub, text), count
