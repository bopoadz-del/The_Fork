"""First-line figure + source, enforced on the answer the operator sees.

The Hard rule in the project-assistant and heavy-reasoning prompts already
tells the model to open with the figure and the document that carries it.
Live SET5 close-out on 209bc83 still scored the first line, and the model
still opened with a narrative, a bare figure, or "properly compacted".

This guard runs at the end of answer post-processing. It does not invent a
number: a qualitative clause stays qualitative when the retrieved excerpts
hold no numeric figure. When they do, the first line names that figure and
the file that states it. A question that names a source class prefers a
numeric figure from a document of that class (the specification's own 98%
over another file's 95%). A different file's figure is labelled as not the
named source. Copies of one figure, including signed and unsigned copies of
one clause, collapse to one line that cites one copy. The line asks which
document only when two or more figures match the same condition the
question names, or when the question names no condition and the figures
cannot be separated. A condition is read from the figure's own sentence,
from the nearest clause heading above it (several lines up, or at the
chunk start), from a section title, and from the document title. Figures
that each carry a different material or condition are stated together,
each tied to that condition and to the document, clause, or drawing that
contains it. An unbound figure whose document is a different system does
not force that question against a figure that matches. An optional higher
compaction degree in the same clause ("could be compacted to … under the
approval of the engineer") is not a second figure. A millimetre counts as
concrete cover only when it is tied to that quantity, not to a panel,
tile, or paint band. "That document is not the specification" is emitted
only when the document is positively a drawing, a report, or a design
note, and never when the chunk calls itself a Specification section.
"Which contract governs this project?" names the one contract in the
excerpts, or asks which when more than one non-template contract is
visible.

Kill-switch: FIRST_LINE_HARD_RULE=0.
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass

_LOG = logging.getLogger(__name__)

_COVER_ASK_RE = re.compile(
    r"(?i)\b(?:concrete|reinforcement|rebar)\b.*\bcovers?\b"
    r"|\bcovers?\b.*\b(?:concrete|reinforcement|rebar|foundation)"
)
_COMPACTION_ASK_RE = re.compile(r"(?i)\bcompact")
_WHICH_CONTRACT_RE = re.compile(
    r"(?i)\b(?:which|what)\s+contract\s+governs\b"
    r"|\bwhich\s+contract\b[^?\n]{0,80}\bgovern"
)
_TEMPLATE_RE = re.compile(r"(?i)\btemplate\b")
_CONTRACT_WORD_RE = re.compile(r"(?i)\bcontracts?\b")
_MM_RE = re.compile(r"(?i)\b(\d+(?:\.\d+)?)\s*mm\b")
# Concrete cover to reinforcement, not a lid, a tile, or a paint band.
# "clear cover" / "nominal cover" / "concrete cover" / "cover to reinforcement".
_CONCRETE_COVER_NEAR_RE = re.compile(
    r"(?i)(?:"
    r"\b(?:concrete|clear|nominal)\s+covers?\b"
    r"|\bcovers?\s+to\s+(?:the\s+)?(?:reinforcement|rebar|bars?|steel)\b"
    r")"
)
# "200 x 200 mm" and "600mm x 600mm" are a panel or tile size.
_PLAN_SIZE_BEFORE_RE = re.compile(r"(?i)\d+(?:\.\d+)?\s*[x×]\s*$")
_PLAN_SIZE_AFTER_RE = re.compile(r"(?i)^\s*[x×]\s*\d")
# "could be compacted to 98% or even 100% of maximum dry density … under
# the approval of the engineer" is permission to go higher, not a second
# requirement. The specified degree stays in the clause ahead of this span.
_OPTIONAL_HIGHER_COMPACTION_RE = re.compile(
    r"(?i)\b(?:could|may|might|can)\s+be\s+compacted\s+to\b"
    r".{0,240}?\bmaximum\s+dry\s+density\b"
)
# "95% of maximum dry density" and "ninety five percent (95%) of maximum dry density".
_MDD_RES = (
    re.compile(
        # No trailing \\b after "%": "%" is not a word character, so a
        # boundary never sits between "%" and the following space.
        r"(?i)\b(\d+(?:\.\d+)?)\s*(?:%|percent\b)"
        r"(?:\s+of)?(?:\s+the)?\s+maximum\s+dry\s+density"
    ),
    re.compile(
        r"(?i)\(\s*(\d+(?:\.\d+)?)\s*%\s*\)[^\n]{0,80}maximum\s+dry\s+density"
    ),
    re.compile(
        r"(?i)maximum\s+dry\s+density[^\n]{0,40}?(\d+(?:\.\d+)?)\s*%"
    ),
)
# Most specific first. "structural backfill" must beat a bare "backfill",
# and "structural fill" must not match inside "structural backfill".
_COMPACTION_MATERIALS = (
    ("structural backfill", re.compile(r"(?i)\bstructural\s+backfill\b")),
    ("structural fill", re.compile(r"(?i)\bstructural\s+fill\b")),
    ("general fill", re.compile(r"(?i)\bgeneral\s+fill\b")),
    ("embankment fill", re.compile(r"(?i)\bembankment\s+fill\b")),
    ("subgrade", re.compile(r"(?i)\bsub-?grade\b")),
    ("backfill", re.compile(r"(?i)\bbackfill\b")),
)
# "below foundations" and "beneath footings" are the same place as
# "under foundations". The label stays canonical so a heading and a
# question still meet.
_COMPACTION_LOCATIONS = (
    ("under foundations", re.compile(
        r"(?i)\b(?:under|below|beneath)\s+(?:the\s+)?"
        r"(?:foundations?|footings?)\b"
    )),
    ("under road pavement", re.compile(
        r"(?i)\b(?:under|below|beneath)\s+(?:the\s+)?road\s+pavements?\b"
    )),
)
# "8.4 Backfill" at the start of a line. A bare report number such as
# "RSM 15492" does not match: it is not a dotted clause at line start.
_CLAUSE_HEADING_RE = re.compile(
    r"(?i)^(?:clause[ \t]+|section[ \t]+|§[ \t]*)?"
    r"(\d+(?:\.\d+)+)\.?(?:[ \t]+(.*))?$"
)
_SECTION_TITLE_RE = re.compile(
    r"(?i)^specification[ \t]+section[ \t]+(\d+(?:\.\d+)*)\b[ \t]*(.*)$"
)
_SELF_SPEC_RE = re.compile(r"(?i)\bspecification\s+section\b")
_POSITIVE_NONSPEC_RE = re.compile(
    r"(?i)(?:\bdrawings?\b|\bdwg\b|\breports?\b|\bdesign\s+notes?\b)"
)
_STORM_WATER_RE = re.compile(r"(?i)\bstorm\s*water\b")
# "cast against soil" and "in contact with soil" are one condition.
_COVER_CONDITIONS = (
    ("concrete cast against or in contact with soil", re.compile(
        r"(?i)\b(?:concrete\s+)?(?:"
        r"cast\s+against(?:\s+or\s+in\s+contact\s+with)?"
        r"|in\s+contact\s+with"
        r")\s+soil\b"
    )),
    ("the bottom of footings", re.compile(r"(?i)\bbottom\s+of\s+footings?\b")),
    ("cast against blinding", re.compile(r"(?i)\bcast\s+against\s+blinding\b")),
)
_CLAUSE_RE = re.compile(
    r"(?i)(?:§|\bsection\b|\bclause\b)\s*(\d+(?:\.\d+)*)"
)


@dataclass(frozen=True)
class _Hit:
    figure: str
    source: str
    is_class: bool
    condition: str = ""
    clause: str = ""
    calls_itself_spec: bool = False


@dataclass(frozen=True)
class _Bound:
    """Figures that answer different conditions. State each; do not ask."""

    hits: tuple[_Hit, ...]


def first_line_hard_rule_enabled() -> bool:
    return (os.getenv("FIRST_LINE_HARD_RULE", "1") or "").strip().lower() not in (
        "0", "false", "no", "off",
    )


def apply_first_line_hard_rule(
    text: str,
    rag_sys_msg: dict | None,
    messages: list | None,
) -> str:
    """Prepend a first line that carries the figure and its document.

    Returns ``text`` unchanged when the question is outside this rule, the
    excerpts do not hold the figure, the first line already complies, or
    the kill-switch is off.
    """
    if not first_line_hard_rule_enabled():
        return text or ""
    try:
        return _apply(text or "", rag_sys_msg, messages)
    except Exception:
        _LOG.warning(
            "first-line hard rule failed; answer passed through", exc_info=True,
        )
        return text or ""


def _apply(text: str, rag_sys_msg: dict | None, messages: list | None) -> str:
    ask = _operator_ask(messages)
    if not ask:
        return text
    if _WHICH_CONTRACT_RE.search(ask):
        return _apply_contract(text, rag_sys_msg, messages)
    from app.core.rag.retriever import source_class_named_by

    class_name = source_class_named_by(ask)
    topic = _topic(ask)
    if not class_name or not topic:
        return text
    hits = _figure_hits(topic, class_name, _retrieval_records(rag_sys_msg, messages))
    if not hits:
        return text
    chosen = _select(hits, text, topic, ask)
    if isinstance(chosen, _Bound):
        if _states_bound(_first(text), chosen.hits):
            return text
        return _prepend(text, _bound_line(topic, chosen.hits, class_name))
    if isinstance(chosen, list):
        line = _ask_which_figure(topic, chosen)
        if _asks_which_figures(text, chosen):
            return text
        return _prepend(text, line)
    if _line_states(_first(text), chosen, class_name):
        return text
    return _prepend(text, _state_line(topic, chosen, class_name))


def _apply_contract(text: str, rag_sys_msg: dict | None, messages: list | None) -> str:
    contracts = _contract_labels(_retrieval_records(rag_sys_msg, messages))
    if not contracts:
        return text
    if _contract_line_ok(_first(text), contracts):
        return text
    if len(contracts) == 1:
        line = f"The contract in the retrieved context is {contracts[0][1]}."
    else:
        names = " and ".join(name for _key, name in contracts)
        line = (
            f"Retrieved context names more than one contract: {names}. "
            "Which contract is meant?"
        )
    return _prepend(text, line)


def _topic(ask: str) -> str:
    if _COVER_ASK_RE.search(ask or ""):
        return "cover"
    if _COMPACTION_ASK_RE.search(ask or ""):
        return "compaction"
    return ""


def _operator_ask(messages: list | None) -> str:
    from app.agents.runtime import _latest_operator_ask

    return _latest_operator_ask(messages) or ""


def _retrieval_records(rag_sys_msg: dict | None, messages: list | None):
    from app.agents.citation_provenance import build_evidence

    return [
        record for record in build_evidence(rag_sys_msg, messages).records
        if record.kind == "retrieval" and (record.text or record.source_name)
    ]


def _figure_hits(topic: str, class_name: str, records) -> list[_Hit]:
    from app.core.rag.retriever import filename_is_source_class

    hits: list[_Hit] = []
    seen: set[tuple[str, str]] = set()
    for record in records:
        source = (record.source_name or "").strip()
        if not source:
            continue
        raw = record.text or ""
        body = raw if topic == "cover" else _compaction_body(raw)
        numbers = (
            _cover_numbers(body) if topic == "cover" else _mdd_numbers(body)
        )
        is_class = filename_is_source_class(source, class_name)
        for number, start, end in numbers:
            figure = f"{number} mm" if topic == "cover" else f"{number}%"
            key = (figure, source)
            if key in seen:
                continue
            seen.add(key)
            condition, clause, calls_spec = _bind_figure(
                topic, body, start, end, source,
            )
            hits.append(_Hit(
                figure=figure,
                source=source,
                is_class=is_class,
                condition=condition,
                clause=clause,
                calls_itself_spec=calls_spec,
            ))
    return hits


def _select(
    hits: list[_Hit], answer: str, topic: str, ask: str,
) -> _Hit | list[_Hit] | _Bound:
    """One figure to state, the bound set, or the conflicting hits to ask.

    Same figure, any number of copies: one hit, so the line cites one copy.
    Ask which document only when two or more figures match the condition
    the question names and they come from different documents, or when the
    question names no condition and the figures cannot be separated. One
    document that still holds two figures for one condition does not ask.
    Exactly one match is stated, with its clause. Other figures are stated
    with their own condition, or omitted when they are unbound. An unbound
    figure from a different system does not force the question.
    """
    class_hits = [hit for hit in hits if hit.is_class]
    pool = class_hits or hits
    collapsed: list[_Hit] = []
    seen_figures: set[str] = set()
    for hit in pool:
        if hit.figure in seen_figures:
            continue
        seen_figures.add(hit.figure)
        collapsed.append(hit)
    if len(collapsed) == 1:
        return collapsed[0]
    asked = _labels_in(topic, ask or "")
    if asked:
        matched = [
            hit for hit in collapsed
            if _condition_matches(topic, hit.condition, asked)
        ]
        matched_sources = {hit.source for hit in matched}
        if len(matched) >= 2 and len(matched_sources) >= 2:
            return matched
        if len(matched) == 1:
            lead = matched[0]
            others = [
                hit for hit in collapsed
                if hit.figure != lead.figure
                and hit.condition
                and not _condition_matches(topic, hit.condition, asked)
            ]
            if others:
                return _Bound(tuple(_lead_first([lead, *others], topic, asked)))
            return lead
        if len(matched) >= 2:
            return matched[0]
    if _conditions_distinguish(collapsed):
        return _Bound(tuple(_lead_first(collapsed, topic, asked)))
    stated = [hit for hit in collapsed if _figure_in(answer, hit.figure)]
    if len(stated) == 1:
        return stated[0]
    sources = {hit.source for hit in collapsed}
    if len(sources) < 2:
        return collapsed[0]
    return collapsed


def _contract_labels(records) -> list[tuple[str, str]]:
    from app.core.rag.retriever import extract_contract_doc_ids

    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for record in records:
        name = (record.source_name or "").strip()
        if not name or _TEMPLATE_RE.search(name):
            continue
        if not _CONTRACT_WORD_RE.search(name):
            continue
        ids = extract_contract_doc_ids(name)
        key = ids[0] if ids else name.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append((key, name))
    return out


def _plan_dimension(text: str, match: re.Match) -> bool:
    """True when this millimetre is one side of an N x N size."""
    before = text[max(0, match.start() - 24): match.start()]
    after = text[match.end(): match.end() + 16]
    return bool(
        _PLAN_SIZE_BEFORE_RE.search(before) or _PLAN_SIZE_AFTER_RE.search(after)
    )


def _cover_numbers(text: str) -> list[tuple[str, int, int]]:
    found: list[tuple[str, int, int]] = []
    seen: set[str] = set()
    body = text or ""
    for match in _MM_RE.finditer(body):
        if _plan_dimension(body, match):
            continue
        window = body[max(0, match.start() - 100): min(len(body), match.end() + 100)]
        if not _CONCRETE_COVER_NEAR_RE.search(window):
            continue
        number = _trim_num(match.group(1))
        if number in seen:
            continue
        seen.add(number)
        found.append((number, match.start(), match.end()))
    return found


def _compaction_body(text: str) -> str:
    # Drop the optional-higher span before scanning, so 100% in "could be
    # compacted to … 100% of maximum dry density" is not a second figure.
    return _OPTIONAL_HIGHER_COMPACTION_RE.sub(" ", text or "")


def _mdd_numbers(body: str) -> list[tuple[str, int, int]]:
    found: list[tuple[str, int, int]] = []
    seen: set[str] = set()
    for pattern in _MDD_RES:
        for match in pattern.finditer(body or ""):
            number = _trim_num(match.group(1))
            if number in seen:
                continue
            seen.add(number)
            found.append((number, match.start(), match.end()))
    return found


def _clause_window(text: str, start: int, end: int) -> str:
    """The sentence holding the figure, plus the sentence before it."""
    prev = text.rfind(".", 0, start)
    prev2 = text.rfind(".", 0, prev) if prev > 0 else -1
    begin = 0 if prev2 < 0 else prev2 + 1
    nxt = text.find(".", end)
    stop = len(text) if nxt < 0 else nxt + 1
    return text[begin:stop]


def _labels_in(topic: str, text: str) -> str:
    """Material and location named in ``text``, or ""."""
    patterns = _COVER_CONDITIONS if topic == "cover" else _COMPACTION_MATERIALS
    label = ""
    for name, rx in patterns:
        if rx.search(text or ""):
            label = name
            break
    if topic != "compaction":
        return label
    for loc, rx in _COMPACTION_LOCATIONS:
        if rx.search(text or ""):
            return f"{label} {loc}".strip() if label else loc
    return label


def _condition_near(topic: str, text: str, start: int, end: int) -> str:
    return _labels_in(topic, _clause_window(text or "", start, end))


def _heading_binding(topic: str, text: str, figure_start: int) -> tuple[str, str]:
    """Condition and clause from the nearest heading above the figure.

    Numbered clause headings ("8.4 Backfill"), section titles
    ("Specification Section 9"), and a short note heading
    ("Bottom of footings") all count. The search walks upward, so a
    heading several lines above the figure, or at the chunk start, still
    binds. A closer heading that names a condition wins over a section
    title further up.
    """
    clause = ""
    for raw_line in reversed((text or "")[:figure_start].splitlines()):
        line = raw_line.strip()
        if not line:
            continue
        numbered = _CLAUSE_HEADING_RE.match(line)
        if numbered:
            number = numbered.group(1)
            title = numbered.group(2) or ""
            if not clause:
                clause = number
            cond = _labels_in(topic, title) or _labels_in(topic, line)
            if cond:
                return cond, number
            continue
        section = _SECTION_TITLE_RE.match(line)
        if section:
            number = section.group(1) or ""
            title = section.group(2) or ""
            if number and not clause:
                clause = number
            cond = _labels_in(topic, title) or _labels_in(topic, line)
            if cond:
                return cond, clause
            continue
        if len(line) <= 80 and "." not in line:
            cond = _labels_in(topic, line)
            if cond:
                return cond, clause
    return "", clause


def _title_condition(topic: str, source: str) -> str:
    """Condition carried only by the document title."""
    name = source or ""
    if topic == "compaction" and _STORM_WATER_RE.search(name):
        return "storm water network"
    return _labels_in(topic, name)


def _bind_figure(
    topic: str, text: str, start: int, end: int, source: str,
) -> tuple[str, str, bool]:
    """Condition, clause, and whether the chunk calls itself a specification.

    Inline words win when they name a condition. Otherwise the nearest
    clause heading, then the section title, then the document title.
    """
    inline = _condition_near(topic, text, start, end)
    heading_cond, heading_clause = _heading_binding(topic, text, start)
    if inline:
        condition = inline
    elif heading_cond:
        condition = heading_cond
    else:
        condition = _title_condition(topic, source)
    clause = heading_clause or _clause_before(text, end)
    return condition, clause, bool(_SELF_SPEC_RE.search(text or ""))


def _material_only(text: str) -> str:
    for name, rx in _COMPACTION_MATERIALS:
        if rx.search(text or ""):
            return name
    return ""


def _location_only(text: str) -> str:
    for name, rx in _COMPACTION_LOCATIONS:
        if rx.search(text or ""):
            return name
    return ""


def _materials_compatible(figure_mat: str, asked_mat: str) -> bool:
    """True when one material name is the other, or a heading's shorter form.

    "backfill" matches "structural backfill". "structural fill" does not.
    """
    if figure_mat == asked_mat:
        return True
    fig_words = set(figure_mat.lower().split())
    ask_words = set(asked_mat.lower().split())
    if not fig_words or not ask_words:
        return False
    return fig_words <= ask_words or ask_words <= fig_words


def _condition_matches(topic: str, figure: str, asked: str) -> bool:
    """True when this figure's condition is the one the question names."""
    if not figure or not asked:
        return False
    if topic == "cover":
        return figure == asked
    fig_m = _material_only(figure)
    ask_m = _material_only(asked)
    fig_l = _location_only(figure)
    ask_l = _location_only(asked)
    if ask_m:
        if not fig_m or not _materials_compatible(fig_m, ask_m):
            return False
    elif not (ask_l and fig_l == ask_l):
        return False
    return not (ask_l and fig_l and fig_l != ask_l)


def _clause_before(text: str, end: int) -> str:
    found = list(_CLAUSE_RE.finditer((text or "")[:end]))
    if not found:
        return ""
    return found[-1].group(1)


def _conditions_distinguish(hits: list[_Hit]) -> bool:
    """True when every figure carries its own non-empty condition."""
    if len(hits) < 2:
        return False
    labels = [hit.condition for hit in hits]
    if any(not label for label in labels):
        return False
    return len(set(labels)) == len(labels)


def _lead_first(hits: list[_Hit], topic: str, asked: str) -> list[_Hit]:
    if not asked:
        return list(hits)
    return sorted(
        hits,
        key=lambda hit: 0 if _condition_matches(topic, hit.condition, asked) else 1,
    )


def _emit_not_the_spec(hit: _Hit, class_name: str) -> bool:
    """True only for a positive non-spec document the chunk does not claim.

    An unrecognised filename is not enough. A chunk that calls itself a
    Specification section is never labelled "not the specification", even
    when the filename says report.
    """
    if not class_name or hit.is_class or hit.calls_itself_spec:
        return False
    if class_name == "specification":
        return bool(_POSITIVE_NONSPEC_RE.search(hit.source or ""))
    return True


def _trim_num(token: str) -> str:
    if "." in token:
        token = token.rstrip("0").rstrip(".")
    return token


def _figure_in(line: str, figure: str) -> bool:
    if figure.endswith("%"):
        number = re.escape(figure[:-1])
        return bool(re.search(rf"(?i)\b{number}\s*(?:%|percent\b)", line or ""))
    if figure.endswith(" mm"):
        number = re.escape(figure[:-3])
        return bool(re.search(rf"(?i)\b{number}\s*mm\b", line or ""))
    return figure.lower() in (line or "").lower()


def _source_in(line: str, source: str) -> bool:
    collapsed = re.sub(r"\s+", " ", line or "").lower()
    return source.lower() in collapsed


def _first(text: str) -> str:
    for line in (text or "").splitlines():
        if line.strip():
            return line.strip()
    return ""


def _line_states(first: str, hit: _Hit, class_name: str) -> bool:
    if not (_figure_in(first, hit.figure) and _source_in(first, hit.source)):
        return False
    missing_denial = f"not the {class_name}" not in first.lower()
    return not (_emit_not_the_spec(hit, class_name) and missing_denial)


def _state_line(topic: str, hit: _Hit, class_name: str) -> str:
    cond = f" for {hit.condition}" if hit.condition else ""
    clause = f" (§{hit.clause})" if hit.clause else ""
    if topic == "cover":
        line = (
            f"{hit.figure} is the concrete-cover figure{cond}{clause} "
            f"in {hit.source}."
        )
    else:
        line = (
            f"{hit.figure} of maximum dry density is the compaction figure"
            f"{cond}{clause} in {hit.source}."
        )
    if _emit_not_the_spec(hit, class_name):
        line += f" That document is not the {class_name}."
    return line


def _bound_line(
    topic: str, hits: tuple[_Hit, ...] | list[_Hit], class_name: str,
) -> str:
    parts: list[str] = []
    for hit in hits:
        cond = f" for {hit.condition}" if hit.condition else ""
        clause = f" (§{hit.clause})" if hit.clause else ""
        if topic == "cover":
            parts.append(
                f"{hit.figure} is the concrete-cover figure{cond}{clause} "
                f"in {hit.source}."
            )
        else:
            parts.append(
                f"{hit.figure} of maximum dry density is the compaction figure"
                f"{cond}{clause} in {hit.source}."
            )
    line = " ".join(parts)
    if (
        class_name
        and hits
        and all(_emit_not_the_spec(hit, class_name) for hit in hits)
    ):
        line += f" These documents are not the {class_name}."
    return line


def _states_bound(first: str, hits: tuple[_Hit, ...] | list[_Hit]) -> bool:
    low = (first or "").lower()
    if "which document" in low:
        return False
    for hit in hits:
        if not (_figure_in(first, hit.figure) and _source_in(first, hit.source)):
            return False
        if hit.condition and hit.condition.lower() not in low:
            return False
        if hit.clause and hit.clause not in (first or ""):
            return False
    return True


def _ask_which_figure(topic: str, hits: list[_Hit]) -> str:
    label = "concrete-cover" if topic == "cover" else "compaction"
    bits = [f"{hit.figure} in {hit.source}" for hit in hits]
    return (
        f"Retrieved context states more than one {label} figure: "
        + "; ".join(bits)
        + ". Which document's figure is meant?"
    )


def _asks_which_figures(text: str, hits: list[_Hit]) -> bool:
    first = _first(text).lower()
    if "which document's figure is meant" not in first:
        return False
    return all(_figure_in(first, hit.figure) and _source_in(first, hit.source) for hit in hits)


def _contract_line_ok(first: str, contracts: list[tuple[str, str]]) -> bool:
    low = (first or "").lower()
    if len(contracts) == 1:
        key, name = contracts[0]
        return key.lower() in low or name.lower() in low
    if "which contract is meant" not in low:
        return False
    return all(key.lower() in low or name.lower() in low for key, name in contracts)


def _prepend(text: str, line: str) -> str:
    body = (text or "").strip()
    if not body:
        return line
    return f"{line}\n\n{body}"
