"""A project figure must not be stated as a named code's requirement.

Live P4b on 209bc83: "Per NFPA 51B (2019), how long must a fire watch
be maintained after hot work?" The retriever returned the project's
hot-work permit (30 minutes). The answer presented 30 minutes as
NFPA 51B's. NFPA 51B was not a retrieved document. Same shape for a
project lux value named as Dubai Municipality's, and a project 32°C
named as ACI 305's.

A retrieved document backs the named code only when the code is in
the filename, or the excerpt is knowledge-base text that states the
code. A project or master-corpus record that merely cites the code
does not. Otherwise the answer says the code is not in the retrieved
excerpts, and any project figure it keeps is labelled project-only.

The relabel keeps the clause that states the figure the question
asked for (fresh-concrete placing temperature at clause 3.1.23.1,
for example). It does not replace that clause with every other
temperature in the same file.

Kill switch: ``NAMED_STANDARD_ATTRIBUTION_GATE=0``.
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass

_LOG = logging.getLogger(__name__)


def gate_enabled() -> bool:
    """ON by default. ``NAMED_STANDARD_ATTRIBUTION_GATE=0`` restores the old path."""
    raw = (os.getenv("NAMED_STANDARD_ATTRIBUTION_GATE", "1") or "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


@dataclass(frozen=True)
class NamedStandard:
    """One code or authority the operator named."""

    display: str
    pattern: re.Pattern

    def in_text(self, text: str) -> bool:
        return bool(self.pattern.search(text or ""))


@dataclass(frozen=True)
class Excerpt:
    source_name: str
    source_class: str
    text: str


@dataclass(frozen=True)
class _Quantity:
    num_key: str
    family: str
    display: str


def _org_re(org: str) -> str:
    return r"\s+".join(re.escape(part) for part in org.split())


def _code_pattern(org: str, number: str, suffix: str) -> re.Pattern:
    """Match ``org`` + ``number`` with flexible separators.

    A suffix the operator named is required (NFPA 51B is not NFPA 51).
    No suffix still rejects a longer number (ACI 305 is not ACI 3050)
    and still matches a trailing letter on the document (ACI 305R).
    """
    if suffix:
        tail = re.escape(suffix) + r"(?![A-Za-z0-9])"
    else:
        tail = r"(?![0-9])"
    return re.compile(
        rf"\b{_org_re(org)}\s*[-_]?\s*{re.escape(number)}{tail}",
        re.IGNORECASE,
    )


def _numbered(org: str, number: str, suffix: str = "") -> NamedStandard:
    suf = (suffix or "").upper()
    display = f"{org} {number}{suf}".strip()
    return NamedStandard(display, _code_pattern(org, number, suf))


# Longer labels first so "BS EN 1992" is not also recorded as "EN 1992".
_NUMBERED_RES: tuple[tuple[str, re.Pattern], ...] = (
    ("BS EN", re.compile(r"\bBS\s+EN\s+(\d+(?:-\d+)*)\b", re.IGNORECASE)),
    ("NFPA", re.compile(r"\bNFPA\s*[-_]?\s*(\d+)([A-Z])?\b", re.IGNORECASE)),
    ("ACI", re.compile(r"\bACI\s*[-_]?\s*(\d+)([A-Z])?\b", re.IGNORECASE)),
    ("ASTM", re.compile(
        r"\bASTM\s*[-_]?\s*([A-Z]\d+(?:[./-]\d+)*)\b", re.IGNORECASE,
    )),
    ("IEEE", re.compile(r"\bIEEE\s*[-_]?\s*(\d+(?:[./-]\d+)*)\b", re.IGNORECASE)),
    ("ASHRAE", re.compile(
        r"\bASHRAE\s*[-_]?\s*(\d+(?:\.\d+)?)\b", re.IGNORECASE,
    )),
    ("IEC", re.compile(r"\bIEC\s*[-_]?\s*(\d+(?:-\d+)*)\b", re.IGNORECASE)),
    ("ISO", re.compile(r"\bISO\s*[-_]?\s*(\d+(?:-\d+)*)\b", re.IGNORECASE)),
    ("SBC", re.compile(r"\bSBC\s*[-_]?\s*(\d+)\b", re.IGNORECASE)),
    ("AISC", re.compile(r"\bAISC\s*[-_]?\s*(\d+)\b", re.IGNORECASE)),
    ("AWS", re.compile(r"\bAWS\s*[-_]?\s*([A-Z]?\d+(?:\.\d+)?)\b", re.IGNORECASE)),
    ("BS", re.compile(r"\bBS\s*[-_]?\s*(\d+(?:-\d+)*)\b", re.IGNORECASE)),
    ("EN", re.compile(r"\bEN\s+(\d+(?:-\d+)*)\b", re.IGNORECASE)),
)

_AUTHORITY_RES: tuple[re.Pattern, ...] = (
    re.compile(r"\bDubai\s+Municipality\b", re.IGNORECASE),
)


def extract_named_standards(query: str) -> list[NamedStandard]:
    """Codes and authorities the operator named. Empty when they named none."""
    if not query:
        return []
    found: list[NamedStandard] = []
    occupied: list[tuple[int, int]] = []

    def _free(start: int, end: int) -> bool:
        return all(end <= lo or start >= hi for lo, hi in occupied)

    for org, rx in _NUMBERED_RES:
        for match in rx.finditer(query):
            if not _free(match.start(), match.end()):
                continue
            occupied.append((match.start(), match.end()))
            groups = match.groups()
            number = groups[0]
            suffix = groups[1] if len(groups) > 1 and groups[1] else ""
            found.append(_numbered(org, number, suffix))
    for rx in _AUTHORITY_RES:
        for match in rx.finditer(query):
            if not _free(match.start(), match.end()):
                continue
            occupied.append((match.start(), match.end()))
            display = "Dubai Municipality"
            found.append(NamedStandard(
                display,
                re.compile(r"\bDubai\s+Municipality\b", re.IGNORECASE),
            ))
    deduped: list[NamedStandard] = []
    seen: set[str] = set()
    for item in found:
        key = item.display.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


_MARKER_RE = re.compile(
    r"\[doc_id=(?P<doc>[^\s\]]+)\s+chunk=\d+\s+score=[0-9.]+(?P<attrs>[^\]]*)\]"
)
_CLASS_RE = re.compile(r"\bclass=([A-Za-z_]+)")
_SRC_RE = re.compile(r"\bsrc=(.*)\s*$")


def excerpts_from_rag_message(content: str) -> list[Excerpt]:
    """Excerpt bodies from a formatted RAG system message. The steering
    header is not an excerpt — a note that names the code must not count
    as the code having been retrieved.
    """
    text = content or ""
    marks = list(_MARKER_RE.finditer(text))
    out: list[Excerpt] = []
    for index, mark in enumerate(marks):
        attrs = mark.group("attrs") or ""
        class_match = _CLASS_RE.search(attrs)
        src_match = _SRC_RE.search(attrs)
        start = mark.end()
        end = marks[index + 1].start() if index + 1 < len(marks) else len(text)
        out.append(Excerpt(
            source_name=(src_match.group(1).strip() if src_match else ""),
            source_class=(class_match.group(1) if class_match else ""),
            text=text[start:end].strip(),
        ))
    return out


def excerpts_from_chunks(chunks) -> list[Excerpt]:
    """Same shape as the formatted message, read off the chunk objects."""
    from app.core.rag.source_class import classify_chunk

    out: list[Excerpt] = []
    for chunk in chunks or []:
        try:
            source_class = classify_chunk(chunk)
        except Exception:
            _LOG.exception("named-standard source class failed")
            source_class = "project_corpus"
        out.append(Excerpt(
            source_name=(getattr(chunk, "source_name", "") or ""),
            source_class=source_class,
            text=(getattr(chunk, "text", "") or ""),
        ))
    return out


def _excerpt_backs(excerpt: Excerpt, standard: NamedStandard) -> bool:
    """True when this excerpt IS the named code, not a project cite of it."""
    if standard.in_text(excerpt.source_name):
        return True
    if excerpt.source_class == "knowledge_base" and standard.in_text(excerpt.text):
        return True
    return False


def standard_is_backed(standard: NamedStandard, excerpts: list[Excerpt]) -> bool:
    return any(_excerpt_backs(excerpt, standard) for excerpt in excerpts)


_QTY_RE = re.compile(
    r"\b(?P<num>\d+(?:\.\d+)?)\s*[-–]?\s*(?P<unit>"
    r"minutes?|mins?|hours?|hrs?|"
    r"°\s*[CFcf]|degrees?\s*[CFcf]|"
    r"lux|lx"
    r")\b",
    re.IGNORECASE,
)


def _family(unit: str) -> str:
    compact = re.sub(r"[\s°]", "", unit or "").lower()
    if compact.startswith("min"):
        return "min"
    if compact.startswith("hour") or compact.startswith("hr"):
        return "hour"
    if compact in {"lux", "lx"}:
        return "lux"
    if compact.endswith("f"):
        return "f"
    if compact.endswith("c"):
        return "c"
    return compact


def _num_key(raw: str) -> str:
    value = float(raw)
    if value == int(value):
        return str(int(value))
    return str(value)


def _quantities(text: str) -> list[_Quantity]:
    found: list[_Quantity] = []
    seen: set[tuple[str, str]] = set()
    for match in _QTY_RE.finditer(text or ""):
        key = (_num_key(match.group("num")), _family(match.group("unit")))
        if key in seen:
            continue
        seen.add(key)
        found.append(_Quantity(key[0], key[1], re.sub(r"\s+", " ", match.group(0).strip())))
    return found


def _qty_in(qty: _Quantity, text: str) -> bool:
    return any(other.num_key == qty.num_key and other.family == qty.family
               for other in _quantities(text))


def _sentences(text: str) -> list[str]:
    return [part.strip() for part in re.split(r"(?<=[.!?])\s+|\n+", text or "") if part.strip()]


def _disclaims(sentence: str, standard: NamedStandard) -> bool:
    if re.search(r"project[-\s]?only|project requirement", sentence, re.IGNORECASE):
        return True
    for match in re.finditer(r"\bnot\b", sentence, re.IGNORECASE):
        window = sentence[match.end():match.end() + 80]
        if standard.in_text(window):
            return True
    return False


def _attributes_unbacked(answer: str, missing: list[NamedStandard]) -> bool:
    for sentence in _sentences(answer):
        if not _quantities(sentence):
            continue
        for standard in missing:
            if standard.in_text(sentence) and not _disclaims(sentence, standard):
                return True
    return False


_ABSENCE_RE = re.compile(
    r"not in the retrieved|not in the corpus|not in the indexed|"
    r"not in these excerpts|could not find|cannot find|"
    r"cannot state what|was not retrieved",
    re.IGNORECASE,
)
_LABEL_RE = re.compile(r"project[-\s]?only|project requirement", re.IGNORECASE)
# 3.1.23.1, not a 2-part decimal and not the quantity itself.
_CLAUSE_NUM_RE = re.compile(r"(?<!\d)(\d+(?:\.\d+){2,})(?!\d)")
_WORD_RE = re.compile(r"[A-Za-z]{4,}")
_TOPIC_STOP = frozenset({
    "what", "when", "where", "which", "that", "this", "with", "from",
    "must", "have", "been", "after", "before", "under", "does",
    "required", "requirement", "recommended", "applies", "apply",
    "about", "there", "their", "your", "into", "only", "than",
    "long", "much", "many",
})


def _absence_for(answer: str, standard: NamedStandard) -> bool:
    if not standard.in_text(answer):
        return False
    return bool(_ABSENCE_RE.search(answer))


def _project_figure_unlabelled(
    answer: str,
    excerpts: list[Excerpt],
    missing: list[NamedStandard],
) -> bool:
    if _LABEL_RE.search(answer) and all(_absence_for(answer, standard) for standard in missing):
        return False
    for excerpt in excerpts:
        if any(_excerpt_backs(excerpt, standard) for standard in missing):
            continue
        for qty in _quantities(excerpt.text):
            if _qty_in(qty, answer):
                return True
    return False


def _needs_relabel(
    answer: str,
    missing: list[NamedStandard],
    excerpts: list[Excerpt],
) -> bool:
    if _attributes_unbacked(answer, missing):
        return True
    return _project_figure_unlabelled(answer, excerpts, missing)


def _whose(missing: list[NamedStandard]) -> str:
    if len(missing) == 1:
        return f"{missing[0].display}'s requirement"
    names = " or ".join(standard.display for standard in missing)
    return f"a requirement of {names}"


def _topic_terms(query: str, missing: list[NamedStandard]) -> set[str]:
    cleaned = query or ""
    for standard in missing:
        cleaned = standard.pattern.sub(" ", cleaned)
    return {
        word.lower()
        for word in _WORD_RE.findall(cleaned)
        if word.lower() not in _TOPIC_STOP
    }


def _qty_span(text: str, qty: _Quantity) -> tuple[int, int] | None:
    for match in _QTY_RE.finditer(text or ""):
        key = (_num_key(match.group("num")), _family(match.group("unit")))
        if key == (qty.num_key, qty.family):
            return match.start(), match.end()
    return None


_SENTENCE_BOUND_RE = re.compile(r"[.!?]\s+|\n+")


def _sentence_bounds(text: str, start: int, end: int) -> tuple[int, int]:
    left = 0
    for match in _SENTENCE_BOUND_RE.finditer(text, 0, start):
        left = match.end()
    match = _SENTENCE_BOUND_RE.search(text, end)
    if not match:
        return left, len(text)
    if text[match.start()] in ".!?":
        return left, match.start() + 1
    return left, match.start()


def _local_context(text: str, qty: _Quantity) -> str:
    """The quantity's own sentence, plus a previous heading with no figure.

    A character window around 70°C also covered the 32°C placing sentence
    and the relabel then called 70°C the placing limit. Clause numbers
    such as 3.1.23.1 are not sentence breaks.
    """
    raw = text or ""
    span = _qty_span(raw, qty)
    if span is None:
        return ""
    left, right = _sentence_bounds(raw, span[0], span[1])
    own = raw[left:right].strip()
    if left == 0:
        return own
    prev_left, prev_right = _sentence_bounds(raw, max(0, left - 1), max(0, left - 1))
    prev = raw[prev_left:prev_right].strip()
    if prev and not _quantities(prev):
        return f"{prev}\n{own}"
    return own


def _clause_before(text: str, qty: _Quantity) -> str:
    span = _qty_span(text, qty)
    if span is None:
        return ""
    window = text[max(0, span[0] - 800): span[0]]
    found = list(_CLAUSE_NUM_RE.finditer(window))
    return found[-1].group(1) if found else ""


def _window_score(window: str, terms: set[str]) -> int:
    words = {word.lower() for word in _WORD_RE.findall(window or "")}
    return len(words & terms)


def _is_fresh_placing(window: str, qty: _Quantity) -> bool:
    if qty.family != "c":
        return False
    low = (window or "").lower()
    return "fresh" in low and "concrete" in low and "plac" in low


def _pretty_qty(qty: _Quantity) -> str:
    if qty.family == "c":
        return f"{qty.num_key} °C"
    if qty.family == "f":
        return f"{qty.num_key} °F"
    return qty.display


def _candidate_quantities(
    excerpts: list[Excerpt],
    answer: str,
    missing: list[NamedStandard],
) -> tuple[list[tuple[Excerpt, _Quantity]], list[tuple[Excerpt, _Quantity]]]:
    matched: list[tuple[Excerpt, _Quantity]] = []
    others: list[tuple[Excerpt, _Quantity]] = []
    seen: set[tuple[str, str]] = set()
    for excerpt in excerpts:
        if any(_excerpt_backs(excerpt, standard) for standard in missing):
            continue
        for qty in _quantities(excerpt.text):
            key = (qty.num_key, qty.family)
            if key in seen:
                continue
            seen.add(key)
            if _qty_in(qty, answer):
                matched.append((excerpt, qty))
            else:
                others.append((excerpt, qty))
    return matched, others


def _select_quantities(
    excerpts: list[Excerpt],
    answer: str,
    missing: list[NamedStandard],
    query: str,
) -> list[tuple[Excerpt, _Quantity]]:
    """Quantities that answer the question, not every figure in the file.

    A concrete-temperature chunk also states curing and water limits.
    Listing each of them dropped the placing-temperature clause. When
    one figure's neighbourhood matches the question, only that figure
    is kept. With no topical match, the previous matched-or-two rule
    still applies.
    """
    matched, others = _candidate_quantities(excerpts, answer, missing)
    pool = matched + others
    if not pool:
        return []
    terms = _topic_terms(query, missing)
    scored: list[tuple[int, str, Excerpt, _Quantity]] = []
    for excerpt, qty in pool:
        window = _local_context(excerpt.text, qty).lower()
        scored.append((_window_score(window, terms), window, excerpt, qty))
    best = max(item[0] for item in scored)
    if best <= 0:
        return matched[:4] if matched else others[:2]
    top = [item for item in scored if item[0] == best]
    if "fresh" in terms and "concrete" in terms:
        focused = [
            item for item in top
            if "fresh" in item[1] and "concrete" in item[1]
        ]
        if focused:
            top = focused
    if any("plac" in item[1] for item in top):
        placed = [item for item in top if "plac" in item[1]]
        if placed:
            top = placed
    return [(item[2], item[3]) for item in top[:2]]


def _figure_note(
    excerpt: Excerpt,
    qty: _Quantity,
    whose: str,
) -> str:
    label = excerpt.source_name or "a project document"
    window = _local_context(excerpt.text, qty)
    clause = _clause_before(excerpt.text, qty)
    if clause and _is_fresh_placing(window, qty):
        return (
            f'The project document "{label}" states, at clause {clause}, '
            f"that {_pretty_qty(qty)} is the fresh-concrete placing "
            f"temperature limit. That figure is project-only and is not "
            f"{whose}."
        )
    return (
        f'The project document "{label}" states {qty.display}. '
        f"That figure is project-only and is not {whose}."
    )


def _placing_clause_line(
    excerpts: list[Excerpt],
    missing: list[NamedStandard],
    query: str,
) -> str:
    """Project-only placing-limit line when the excerpt states the clause.

    Empty unless the question is about fresh concrete and a project
    excerpt states a placing temperature under a numbered clause.
    """
    terms = _topic_terms(query, missing)
    if "fresh" not in terms or "concrete" not in terms:
        return ""
    whose = _whose(missing)
    best: tuple[int, str] | None = None
    for excerpt in excerpts:
        if any(_excerpt_backs(excerpt, standard) for standard in missing):
            continue
        for qty in _quantities(excerpt.text):
            window = _local_context(excerpt.text, qty)
            if not _is_fresh_placing(window, qty):
                continue
            clause = _clause_before(excerpt.text, qty)
            if not clause:
                continue
            score = _window_score(window, terms)
            line = _figure_note(excerpt, qty, whose)
            if best is None or score > best[0]:
                best = (score, line)
    return best[1] if best else ""


def _answer_has_clause(answer: str, clause: str) -> bool:
    if not clause:
        return False
    return bool(re.search(
        rf"(?<!\d){re.escape(clause)}(?!\d)",
        answer or "",
    ))


# The old relabel listed every temperature as its own "states N°C" line
# and dropped the clause those numbers came from.
_BARE_TEMPERATURE_NOTE_RE = re.compile(
    r'The project document "[^"]+" states \d+(?:\.\d+)?\s*°\s*[CFcf]\. '
    r"That figure is project-only and is not .+?requirement\.",
    re.IGNORECASE,
)


def _strip_bare_temperature_notes(answer: str) -> str:
    cleaned = _BARE_TEMPERATURE_NOTE_RE.sub("", answer or "")
    cleaned = re.sub(r"[ \t]+\n", "\n", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def _with_placing_clause(
    answer: str,
    missing: list[NamedStandard],
    excerpts: list[Excerpt],
    query: str,
) -> str:
    """State the placing-limit clause when the answer does not already.

    Bare "states 70°C / 20°C / 25°C" lines are the unrelated figures the
    old relabel appended. They come off once the clause line is known.
    """
    line = _placing_clause_line(excerpts, missing, query)
    if not line:
        return answer
    clause_match = _CLAUSE_NUM_RE.search(line)
    clause = clause_match.group(1) if clause_match else ""
    base = _strip_bare_temperature_notes(answer or "")
    if _answer_has_clause(base, clause):
        return base
    if not base:
        return line
    return f"{base}\n\n{line}"


def _honest_absence(
    missing: list[NamedStandard],
    excerpts: list[Excerpt],
    answer: str,
    query: str = "",
) -> str:
    if len(missing) == 1:
        name = missing[0].display
        absence = (
            f"{name} is not in the retrieved excerpts, so this answer "
            f"cannot state what {name} requires."
        )
    else:
        listed = ", ".join(standard.display for standard in missing[:-1])
        listed = f"{listed} and {missing[-1].display}"
        absence = (
            f"{listed} are not in the retrieved excerpts, so this answer "
            f"cannot state what they require."
        )
    whose = _whose(missing)
    notes = [
        _figure_note(excerpt, qty, whose)
        for excerpt, qty in _select_quantities(excerpts, answer, missing, query)
    ]
    text = "\n\n".join([absence, *notes])
    return _with_placing_clause(text, missing, excerpts, query)


def _slab_calculator_note(query: str) -> str:
    """Steering when slab_thickness_min answers the ask.

    The named-standard absence note would otherwise tell the model not
    to state what ACI 318 requires, and the turn ends before the
    calculator runs. A computed result's provenance is the calculator's
    standard field.
    """
    try:
        from app.lib.construction_formulas_structural_rc import (
            looks_like_slab_thickness_min_ask,
        )
    except Exception:
        _LOG.exception("slab thickness steering check failed")
        return ""
    if not looks_like_slab_thickness_min_ask(query or ""):
        return ""
    return (
        "COMPUTED BY CALCULATOR — registered calculator "
        "slab_thickness_min answers this question. Run construction_calc "
        "and state its result. Cite the calculator standard field as "
        "provenance. Do not refuse for corpus absence. Do not attribute "
        "a project-document figure to the named code.\n"
    )


def absence_note(query: str, chunks) -> str:
    """Steering line for the RAG system message. Empty when it does not apply."""
    if not gate_enabled():
        return ""
    computed = _slab_calculator_note(query)
    if computed:
        return computed
    standards = extract_named_standards(query or "")
    if not standards:
        return ""
    excerpts = excerpts_from_chunks(chunks)
    missing = [standard for standard in standards if not standard_is_backed(standard, excerpts)]
    if not missing:
        return ""
    names = ", ".join(standard.display for standard in missing)
    return (
        "NAMED STANDARD ABSENT — the question names "
        f"{names} and no retrieved excerpt is that document "
        "(its name is not in a filename and not in a knowledge-base "
        "excerpt). Do not state what it requires. Do not attribute any "
        "figure below to it. If you mention a figure from a project "
        "document, label it as a project requirement only, and say the "
        "named document is not in the retrieved excerpts.\n"
    )


def relabel_answer(query: str, answer: str, rag_content: str) -> str:
    """Replace a mis-attribution. Leave an already-honest answer unchanged."""
    if not gate_enabled():
        return answer
    standards = extract_named_standards(query or "")
    if not standards:
        return answer or ""
    excerpts = excerpts_from_rag_message(rag_content or "")
    missing = [standard for standard in standards if not standard_is_backed(standard, excerpts)]
    if not missing:
        return answer
    try:
        from app.lib.construction_formulas_structural_rc import (
            answer_states_slab_thickness_result,
        )
        if answer_states_slab_thickness_result(query or "", answer or ""):
            return answer
    except Exception:
        _LOG.exception("calculator provenance check failed")
    if not _needs_relabel(answer or "", missing, excerpts):
        return _with_placing_clause(answer or "", missing, excerpts, query or "")
    return _honest_absence(missing, excerpts, answer or "", query or "")
