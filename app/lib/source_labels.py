"""What a user reads when an answer credits a calculation or a tool.

Every formula and tool declares a ``display_name`` where it is registered;
this module is the one place that turns a credit into words. Internal names
(``construction_calc``, ``delay_damages_daily``) are identifiers for code and
logs and never reach an answer. Input names are read as words, and an input
whose declared unit the name already spells ("length_m", unit "m") is shown
as "length 12 m".

The same reading applies to a finished answer. A registered id, or a
``key=value`` assignment of one, is shown as its display name. On a turn
whose inputs the user already supplied, a currency, unit, clause or
comparison is kept only when the request, the retrieved excerpts or the
calculator result actually state it.
"""
from __future__ import annotations

import inspect
import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

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


#: Currency tokens the cost gate already treats as money. One list, so a
#: supplied-input answer and that gate agree on what a currency is.
CURRENCY_TOKEN = r"(?:SAR|SR|USD|US\$|AED|EUR|GBP|QAR|KWD|OMR|BHD|﷼|\$|€|£)"

_CURRENCY_CODES = frozenset({
    "SAR", "SR", "USD", "AED", "EUR", "GBP", "QAR", "KWD", "OMR", "BHD",
})
_CURRENCY_SYMBOLS = frozenset({"$", "€", "£", "﷼", "US$"})

# A demonstrative comparison ("above that limit") whose noun was never
# introduced. The noun is the kind of limit a sentence can point at.
_COMPARISON_RE = re.compile(
    r"\b(?:below|above|under|over|within|outside|beneath|beyond|exceed(?:s|ed|ing)?)\b",
    re.IGNORECASE,
)
_DEMONSTRATIVE_RE = re.compile(
    r"\b(?:that|this|those|these)\s+([a-z][a-z-]{2,})\b",
    re.IGNORECASE,
)
_COMPARISON_NOUNS = frozenset({
    "band", "range", "limit", "threshold", "allowance", "cap", "ceiling",
    "floor", "minimum", "maximum", "bracket", "tolerance", "margin",
    "interval", "window", "criterion", "criteria",
})
_CLAUSE_RE = re.compile(
    r"\b(?:[A-Z][A-Za-z0-9/+.&-]{1,40}\s+){0,4}"
    r"(?:[Ss]ub-)?[Cc]lause\s+\d+(?:\.\d+)*"
)
_CODE_RE = re.compile(
    r"\b([A-Z]{2,}(?:\s*[/\-]\s*[A-Z0-9]+)*)\s+(\d+(?:\.\d+)*(?:[-/]\d+)?)"
)
_CODE_STOP = frozenset({
    "THE", "AND", "FOR", "PER", "NOT", "BUT", "ALL", "ANY", "ARE", "WAS",
    "HAS", "HAD", "YOU", "OUR", "DAY", "NET", "MAX", "MIN", "NO", "OK",
    "ID", "ITS", "ONE", "TWO",
})
_ASSIGN_RE = re.compile(
    r"(?<![A-Za-z0-9_])([A-Za-z][A-Za-z0-9_]*)=([^\s),]+)"
)
_UNIT_AFTER_RE = re.compile(
    r"(?<=\d)\s+([A-Za-z][A-Za-z0-9²³/.+-]{0,16})\b"
)
_BASIS_TAIL_RE = re.compile(
    r"\s+(?:on|under|using|via)\s+the\s+basis\b",
    re.IGNORECASE,
)
_DANGLING_PREP_RE = re.compile(
    r"\s+(?:on|under|using|via|by)\s+(?=[.?!]|$)",
    re.IGNORECASE,
)

_display_by_id: Optional[Dict[str, str]] = None
_param_units: Optional[Dict[str, str]] = None
_known_units: Optional[set] = None
_id_pattern: Optional[re.Pattern[str]] = None


def _registry_maps() -> Tuple[Dict[str, str], Dict[str, str], set]:
    """Display names, snake_case parameter units, and concrete unit tokens.

    Built once from the formula and tool registries. A token is rewritten
    only when it is one of those ids, so a file name or an engineering
    symbol that is not registered is left as written.
    """
    global _display_by_id, _param_units, _known_units, _id_pattern
    if _display_by_id is not None and _param_units is not None and _known_units is not None:
        return _display_by_id, _param_units, _known_units
    from app.agents.core.tool_registry import OWNERS, get, tools_of
    from app.lib import formula_registry

    displays: Dict[str, str] = {}
    params: Dict[str, str] = {}
    units: set = set()
    for spec in formula_registry.all_specs():
        if spec.name and spec.display_name and "_" in spec.name:
            displays[spec.name.lower()] = spec.display_name
        declared = dict(spec.inputs or {})
        declared.update(spec.outputs or {})
        for key, unit in declared.items():
            if "_" in key:
                params.setdefault(key.lower(), unit or "")
            norm = _norm(unit)
            raw = (unit or "").strip()
            if raw and norm and norm not in {_norm(item) for item in _UNITLESS}:
                units.add(raw.lower())
    for owner in OWNERS:
        for name in tools_of(owner):
            spec = get(name)
            if spec is None or not spec.display_name or "_" not in spec.name:
                continue
            displays[spec.name.lower()] = spec.display_name
            _collect_schema_params(spec.schema, params)
    _display_by_id = displays
    _param_units = params
    _known_units = units
    if displays:
        alt = "|".join(re.escape(name) for name in sorted(displays, key=len, reverse=True))
        _id_pattern = re.compile(
            rf"(?<![A-Za-z0-9_./])({alt})(?![A-Za-z0-9_])(?!\.[A-Za-z0-9])",
            re.IGNORECASE,
        )
    else:
        _id_pattern = None
    return displays, params, units


def _collect_schema_params(schema: Any, params: Dict[str, str]) -> None:
    if not isinstance(schema, dict):
        return
    props = schema.get("properties")
    if isinstance(props, dict):
        for key, row in props.items():
            if "_" not in str(key):
                continue
            unit = ""
            if isinstance(row, dict):
                unit = str(row.get("unit") or "")
            params.setdefault(str(key).lower(), unit)
    for value in schema.values():
        if isinstance(value, dict):
            _collect_schema_params(value, params)


def _coerce_assignment_value(raw: str) -> Any:
    cleaned = (raw or "").rstrip(".,;:")
    if re.fullmatch(r"-?\d[\d,]*(?:\.\d+)?", cleaned):
        number = cleaned.replace(",", "")
        if "." in number:
            value = float(number)
            return int(value) if value.is_integer() else value
        return int(number)
    return cleaned


def plain_registry_text(text: str) -> str:
    """Show registered ids as display names. Leave every other token alone.

    A formula or tool id, including one wrapped as code or written
    ``slot=<id>``, becomes that registration's display name. A snake_case
    parameter written ``key=value`` is read as words, the same way a credit
    reads an input. A file name (the id followed by an extension) and an
    assignment whose key and value are not registered are unchanged.
    """
    if not text or ("_" not in text and "=" not in text):
        return text
    displays, params, _units = _registry_maps()
    if not displays and not params:
        return text

    def replace_assignment(match: re.Match[str]) -> str:
        key, raw = match.group(1), match.group(2)
        low_key = key.lower()
        low_val = raw.rstrip(".,;:").lower()
        if low_val in displays:
            return displays[low_val]
        if low_key in displays and low_key not in params:
            value = _coerce_assignment_value(raw)
            shown = _fmt_value(value)
            return f"{displays[low_key]} {shown}".strip()
        if low_key in params:
            return input_phrase(low_key, _coerce_assignment_value(raw), params[low_key])
        return match.group(0)

    text = _ASSIGN_RE.sub(replace_assignment, text)

    def replace_code_span(match: re.Match[str]) -> str:
        inner = match.group(1).strip()
        display = displays.get(inner.lower())
        if display is None:
            return match.group(0)
        return display

    text = re.sub(r"`([^`\n]+)`", replace_code_span, text)
    pattern = _id_pattern
    if pattern is None:
        return text

    def replace_id(match: re.Match[str]) -> str:
        return displays.get(match.group(1).lower(), match.group(0))

    return pattern.sub(replace_id, text)


def _result_strings(value: Any) -> List[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        found: List[str] = []
        for item in value.values():
            found.extend(_result_strings(item))
        return found
    if isinstance(value, (list, tuple)):
        found = []
        for item in value:
            found.extend(_result_strings(item))
        return found
    return []


def _unsupplied_currency_defaults(calculation: str, supplied: Dict[str, Any]) -> set:
    """Currency codes a formula stamps when the user did not give one.

    The code is a signature default, not a unit the calculation measured
    and not a currency the user typed. It does not authorise writing that
    code next to the user's figures.
    """
    from app.lib import formula_registry

    spec = formula_registry.get(calculation or "")
    if spec is None:
        return set()
    try:
        signature = inspect.signature(spec.fn)
    except (TypeError, ValueError):
        return set()
    bound = supplied or {}
    found = set()
    for key, param in signature.parameters.items():
        if key in bound and bound[key] not in (None, ""):
            continue
        default = param.default
        if not isinstance(default, str):
            continue
        code = default.strip().upper()
        if code in _CURRENCY_CODES:
            found.add(code)
    return found


def _supplied_tokens(supplied: Dict[str, Any]) -> List[str]:
    tokens: List[str] = []
    for value in (supplied or {}).values():
        if isinstance(value, bool) or value in (None, ""):
            continue
        if isinstance(value, str) and len(value.strip()) >= 2:
            tokens.append(value.strip().casefold())
            continue
        if isinstance(value, (int, float)):
            if isinstance(value, float) and value.is_integer():
                value = int(value)
            rendered = f"{value:g}"
            if isinstance(value, int) and abs(value) >= 10:
                tokens.append(f"{value:,}")
            if len(rendered) >= 2:
                tokens.append(rendered.casefold())
    return tokens


def _citation_spans(text: str) -> List[Tuple[int, int, str]]:
    spans: List[Tuple[int, int, str]] = []
    spans.extend((match.start(), match.end(), match.group(0)) for match in _CLAUSE_RE.finditer(text))
    for match in _CODE_RE.finditer(text):
        head = match.group(1).split()[0].upper()
        if head in _CURRENCY_CODES or head in _CODE_STOP or head in _CURRENCY_SYMBOLS:
            continue
        spans.append((match.start(), match.end(), match.group(0)))
    spans.sort(key=lambda item: item[0])
    return spans


def _citation_stated(span: str, user: str, retrieval: str, result: Any,
                     supplied: Dict[str, Any]) -> bool:
    """True when this citation is in the request, the excerpts, or a result
    string that also carries a value the user supplied.

    A fixed label the formula returns for every call does not name the
    user's inputs, so it is not a source for the basis of this result.
    """
    needle = re.sub(r"\s+", " ", span).strip().casefold()
    if len(needle) < 4:
        return True
    blob = re.sub(r"\s+", " ", f"{user or ''}\n{retrieval or ''}").casefold()
    if needle in blob:
        return True
    tokens = _supplied_tokens(supplied)
    if not tokens:
        return False
    for value in _result_strings(result):
        folded = re.sub(r"\s+", " ", value).casefold()
        if needle in folded and any(token in folded for token in tokens):
            return True
    return False


def _known_unit_in(text: str, known: Iterable[str]) -> set:
    found = set()
    for unit in known:
        if re.search(rf"(?<![A-Za-z0-9]){re.escape(unit)}(?![A-Za-z0-9])", text or "", re.IGNORECASE):
            found.add(unit.lower())
    return found


def _allowed_currencies(user: str, retrieval: str, supplied: Dict[str, Any],
                        result: Any, calculation: str) -> set:
    allowed = set()
    blob = f"{user or ''}\n{retrieval or ''}"
    for code in _CURRENCY_CODES:
        if re.search(rf"\b{code}\b", blob, re.IGNORECASE):
            allowed.add(code)
    for symbol in _CURRENCY_SYMBOLS:
        if symbol in blob:
            allowed.add(symbol)
    for value in (supplied or {}).values():
        if isinstance(value, str) and value.strip().upper() in _CURRENCY_CODES:
            allowed.add(value.strip().upper())
    skip = _unsupplied_currency_defaults(calculation, supplied)
    for value in _result_strings(result):
        for code in _CURRENCY_CODES:
            if code in skip:
                continue
            if re.search(rf"\b{code}\b", value):
                allowed.add(code)
        for symbol in _CURRENCY_SYMBOLS:
            if symbol in value:
                allowed.add(symbol)
    return allowed


def _allowed_units(user: str, retrieval: str, calculation: str, result: Any,
                   known: set) -> set:
    from app.lib import formula_registry

    allowed = _known_unit_in(f"{user or ''}\n{retrieval or ''}", known)
    spec = formula_registry.get(calculation or "")
    if spec is not None:
        for unit in list((spec.inputs or {}).values()) + list((spec.outputs or {}).values()):
            raw = (unit or "").strip().lower()
            if raw in known:
                allowed.add(raw)
    for value in _result_strings(result):
        allowed.update(_known_unit_in(value, known))
    return allowed


def _currency_allowed(token: str, allowed: set) -> bool:
    if token in allowed or token.upper() in allowed:
        return True
    return False


def _strip_ungrounded_qualifiers(text: str, allowed_ccy: set, allowed_units: set,
                                 known_units: set) -> str:
    def drop_currency(match: re.Match[str]) -> str:
        if _currency_allowed(match.group(1), allowed_ccy):
            return match.group(0)
        return ""

    text = re.sub(
        rf"(?<![A-Za-z0-9])({CURRENCY_TOKEN})\s*(?=\d)",
        drop_currency,
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        rf"(?<=\d)\s*({CURRENCY_TOKEN})(?![A-Za-z0-9])",
        drop_currency,
        text,
        flags=re.IGNORECASE,
    )

    def drop_unit(match: re.Match[str]) -> str:
        token = match.group(1).lower()
        if token not in known_units:
            return match.group(0)
        if token in allowed_units:
            return match.group(0)
        return ""

    text = _UNIT_AFTER_RE.sub(drop_unit, text)
    return text


def _strip_ungrounded_citations(text: str, user: str, retrieval: str, result: Any,
                                supplied: Dict[str, Any]) -> str:
    kept: List[str] = []
    for line in text.split("\n"):
        spans = _citation_spans(line)
        removed = False
        for start, end, span in reversed(spans):
            if _citation_stated(span, user, retrieval, result, supplied):
                continue
            line = line[:start] + line[end:]
            removed = True
        if not removed:
            kept.append(line)
            continue
        line = re.sub(r"[ \t]{2,}", " ", line).strip()
        line = _BASIS_TAIL_RE.sub("", line)
        line = _DANGLING_PREP_RE.sub("", line)
        line = re.sub(r"[ \t]{2,}", " ", line).strip()
        if line and re.search(r"\d", line):
            kept.append(line)
    return "\n".join(kept)


def _sentence_is_dangling(sentence: str, user: str, retrieval: str, earlier: str) -> bool:
    if _COMPARISON_RE.search(sentence) is None:
        return False
    nouns = [
        match.group(1).lower()
        for match in _DEMONSTRATIVE_RE.finditer(sentence)
        if match.group(1).lower() in _COMPARISON_NOUNS
    ]
    if not nouns:
        return False
    without = _DEMONSTRATIVE_RE.sub(" ", sentence)
    blob = f"{user or ''}\n{retrieval or ''}\n{earlier}\n{without}"
    for noun in nouns:
        if re.search(rf"\b{re.escape(noun)}\b", blob, re.IGNORECASE) is None:
            return True
    return False


def _drop_dangling_comparisons(text: str, user: str, retrieval: str) -> str:
    earlier = ""
    kept_lines: List[str] = []
    for line in text.split("\n"):
        parts = re.split(r"((?<=[.!?])\s+)", line)
        rebuilt: List[str] = []
        for part in parts:
            if re.fullmatch(r"\s+", part or ""):
                rebuilt.append(part)
                continue
            if _sentence_is_dangling(part, user, retrieval, earlier):
                if rebuilt and re.fullmatch(r"\s+", rebuilt[-1] or ""):
                    rebuilt.pop()
                continue
            earlier += " " + part
            rebuilt.append(part)
        kept_lines.append("".join(rebuilt).strip())
    return "\n".join(line for line in kept_lines if line.strip())


def _tidy_wording(text: str) -> str:
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r" +([,.;:])", r"\1", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def guard_supplied_input_wording(
    text: str,
    user: str,
    retrieval: str,
    calculation: str,
    supplied: Optional[Dict[str, Any]] = None,
    result: Any = None,
) -> str:
    """Drop currency, unit, clause and comparison text this turn does not state.

    A qualifier attached to a figure stays when the user's words, the
    retrieved excerpts or the calculator result state it. A currency the
    formula inserts only because the user left that parameter blank does
    not count as the calculator stating it. A clause or standard counts
    when the excerpts state it, or when a result string states it and that
    string also carries a value the user supplied. A comparison to "that
    <limit>" stays when the limit was named earlier in the turn.
    """
    if not text:
        return text
    bound = dict(supplied or {})
    _displays, _params, known = _registry_maps()
    text = _strip_ungrounded_citations(text, user, retrieval, result, bound)
    text = _strip_ungrounded_qualifiers(
        text,
        _allowed_currencies(user, retrieval, bound, result, calculation),
        _allowed_units(user, retrieval, calculation, result, known),
        known,
    )
    text = _drop_dangling_comparisons(text, user, retrieval)
    return _tidy_wording(text)
