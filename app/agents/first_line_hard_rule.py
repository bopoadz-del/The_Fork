"""First-line figure + source, enforced on the answer the operator sees.

The Hard rule in the project-assistant and heavy-reasoning prompts already
tells the model to open with the figure and the document that carries it.
Live SET5 close-out on 209bc83 still scored the first line, and the model
still opened with a narrative, a bare figure, or "properly compacted".

This guard verifies the first line. It does not rewrite it. When the
model's first line already carries a figure and a source, the answer
passes through unchanged. Otherwise it may prepend only the figure the
body commits to — the first figure the body states as the answer — and
it never asks once that commitment exists. 98% backfill, 90% storm-water
bedding, and 95% structural fill are different subjects. A question is
prepended only when the body commits to no figure and the remaining
figures share one subject. The filename class is a hint: when the body
names the class ("Specification Section 9.1"), that naming governs, and
the server does not emit "That document is not the specification".
An optional higher compaction degree in the same clause ("could be
compacted to … under the approval of the engineer") is not a second
figure. A millimetre counts as concrete cover only when it is tied to
that quantity, not to a panel, tile, or paint band. A condition the
excerpt negates ("not in contact with soil", "other than structural
fill") is not that condition, and the first line does not print it on
the figure. "Which contract governs this project?" names the one
contract in the excerpts, or asks which when more than one non-template
contract is visible.

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
# The answer body naming the class overrides the filename hint.
_BODY_NAMES_SPEC_RE = re.compile(r"(?i)\bspecifications?\b")
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
# Negation that governs the condition phrase, not a later "not" in the
# same paragraph. "non-" is the prefix form ("non-structural").
_NEGATION_RE = re.compile(
    r"(?i)(?:\b(?:not|no|except|without)\b|\bother\s+than\b|\bnon-)"
)
# "faces not exposed to soil" does not match the soil-contact pattern.
# It is still a negation of that condition.
_NEGATED_EXPOSURE_RE = re.compile(
    r"(?i)(?:"
    r"\b(?:not|no|except|without)\b|\bother\s+than\b|\bnon-"
    r")"
    r"[^.\n]{0,40}?"
    r"\bexposed\s+to\s+soil\b"
)
# "Cl." / "Fig." are not sentence ends. "4.2" is handled separately.
_ABBREV_DOT_RE = re.compile(
    r"(?i)(?:^|[^A-Za-z])(?:"
    r"cl|fig|figs|no|nos|dr|mr|mrs|ms|vs|etc|eg|ie|st|vol|sec|para|"
    r"approx|eq|ref|cf"
    r")\.$"
)


@dataclass(frozen=True)
class _Hit:
    figure: str
    source: str
    is_class: bool
    condition: str = ""
    clause: str = ""
    calls_itself_spec: bool = False
    negated: bool = False


def first_line_hard_rule_enabled() -> bool:
    return (os.getenv("FIRST_LINE_HARD_RULE", "1") or "").strip().lower() not in (
        "0", "false", "no", "off",
    )


def apply_first_line_hard_rule(
    text: str,
    rag_sys_msg: dict | None,
    messages: list | None,
) -> str:
    """Verify the first line. Annotate a missing figure, or leave the answer.

    Returns ``text`` unchanged when the kill-switch is off, the question
    is outside this rule, the first line already carries a figure and a
    source, or the body commits to no figure and the hits are not one
    subject. A committed figure is prepended. A question is prepended
    only when the body itself does not commit and the figures share a
    subject.
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
    hits = _figure_hits(
        topic, class_name, _retrieval_records(rag_sys_msg, messages), text,
    )
    if not hits:
        return text
    first = _first(text)
    # (A) A first line that already names a figure and a source stands.
    if _first_carries_figure_and_source(first, hits):
        return text
    # (B) A figure the body states. Prefer the condition the question
    # asks. A negated or mismatched condition is not promoted.
    committed = _committed_hit(text, hits, topic, ask)
    if committed is not None:
        return _prepend(text, _state_line(topic, committed, class_name))
    # (C) A question only when nothing was committed and the subject is one.
    conflict = _same_subject_conflict(topic, hits)
    if conflict is None:
        return text
    if _asks_which_figures(text, conflict):
        return text
    return _prepend(text, _ask_which_figure(topic, conflict))


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


def _body_names_class(answer: str, class_name: str) -> bool:
    """True when the answer itself names the governing class.

    The filename test is only a hint. "Specification Section 9.1" in the
    body means the document is being treated as a specification.
    """
    if class_name != "specification":
        return False
    return bool(_BODY_NAMES_SPEC_RE.search(answer or ""))


def _figure_hits(
    topic: str, class_name: str, records, answer: str = "",
) -> list[_Hit]:
    from app.core.rag.retriever import filename_is_source_class

    body_names = _body_names_class(answer, class_name)
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
        # Filename is a hint. The body naming the class overrides it.
        is_class = filename_is_source_class(source, class_name) or body_names
        for number, start, end in numbers:
            figure = f"{number} mm" if topic == "cover" else f"{number}%"
            key = (figure, source)
            if key in seen:
                continue
            seen.add(key)
            condition, clause, calls_spec, negated = _bind_figure(
                topic, body, start, end, source,
            )
            if negated:
                condition = ""
            hits.append(_Hit(
                figure=figure,
                source=source,
                is_class=is_class,
                condition=condition,
                clause=clause,
                calls_itself_spec=calls_spec or body_names,
                negated=negated,
            ))
    return hits


def _first_carries_figure_and_source(first: str, hits: list[_Hit]) -> bool:
    """(A) The model's opening line already names a figure and a source."""
    if not first:
        return False
    has_figure = any(_figure_in(first, hit.figure) for hit in hits)
    has_source = any(_source_in(first, hit.source) for hit in hits)
    return has_figure and has_source


def _figure_at(text: str, figure: str) -> int:
    if figure.endswith("%"):
        number = re.escape(figure[:-1])
        match = re.search(rf"(?i)\b{number}\s*(?:%|percent\b)", text or "")
    elif figure.endswith(" mm"):
        number = re.escape(figure[:-3])
        match = re.search(rf"(?i)\b{number}\s*mm\b", text or "")
    else:
        match = re.search(re.escape(figure), text or "", re.IGNORECASE)
    return match.start() if match else -1


def _committed_hit(
    text: str, hits: list[_Hit], topic: str, ask: str,
) -> _Hit | None:
    """A figure the body states, or None so the caller can take path (C).

    A negated figure is never promoted. When the question names a
    condition and a stated figure carries it, that figure wins over an
    earlier figure for a different condition. When nothing stated matches,
    the earliest figure the body actually states still stands.
    """
    asked = _labels_in(topic, ask or "")
    found: list[tuple[int, _Hit]] = []
    for hit in hits:
        if hit.negated:
            continue
        pos = _figure_at(text, hit.figure)
        if pos >= 0:
            found.append((pos, hit))
    if not found:
        return None
    found.sort(key=lambda item: item[0])
    matches: list[tuple[int, _Hit]] = []
    if asked:
        matches = [
            (pos, hit) for pos, hit in found
            if hit.condition and _condition_matches(topic, hit.condition, asked)
        ]
    pool = matches or found
    pos, hit = pool[0]
    candidates = [
        item for item in hits
        if item.figure == hit.figure and not item.negated
        and (
            not matches
            or (
                item.condition
                and _condition_matches(topic, item.condition, asked)
            )
        )
    ]
    if not candidates:
        candidates = [hit]
    window = (text or "")[max(0, pos - 180): pos + 420]
    named = [item for item in candidates if _source_in(window, item.source)]
    return named[0] if named else candidates[0]


def _collapse_figures(hits: list[_Hit]) -> list[_Hit]:
    collapsed: list[_Hit] = []
    seen: set[str] = set()
    for hit in hits:
        if hit.figure in seen:
            continue
        seen.add(hit.figure)
        collapsed.append(hit)
    return collapsed


def _same_subject_conflict(topic: str, hits: list[_Hit]) -> list[_Hit] | None:
    """Figures that share one subject, from more than one document.

    Returns None when the subject cannot be shown to be the same, so the
    caller does not invent a question. Backfill, storm-water bedding, and
    structural fill are different subjects.
    """
    collapsed = _collapse_figures([hit for hit in hits if not hit.negated])
    if len(collapsed) < 2:
        return None
    if len({hit.source for hit in collapsed}) < 2:
        return None
    if any(not hit.condition for hit in collapsed):
        return None
    head = collapsed[0]
    for other in collapsed[1:]:
        if other.condition == head.condition:
            continue
        if not _condition_matches(topic, other.condition, head.condition):
            return None
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


def _negated_at(text: str, match_start: int) -> bool:
    """True when a negation in the 40 characters before the match governs it.

    A figure digit between the negation and the match means the negation
    applies to the figure ("shall not be less than 75 mm for … soil"),
    not to the condition ("not in contact with soil").
    """
    before = (text or "")[max(0, match_start - 40): match_start]
    for found in _NEGATION_RE.finditer(before):
        if not re.search(r"\d", before[found.end():]):
            return True
    return False


def _first_positive(text: str, patterns) -> tuple[str, bool]:
    """First pattern that is not negated, and whether a negated match was seen."""
    saw_negated = False
    body = text or ""
    for name, rx in patterns:
        for match in rx.finditer(body):
            if _negated_at(body, match.start()):
                saw_negated = True
                continue
            return name, False
    return "", saw_negated


def _condition_binding(topic: str, text: str) -> tuple[str, bool]:
    """Condition label in ``text``, and whether the only match was negated."""
    patterns = _COVER_CONDITIONS if topic == "cover" else _COMPACTION_MATERIALS
    label, saw_neg = _first_positive(text, patterns)
    if topic == "compaction":
        loc, loc_neg = _first_positive(text, _COMPACTION_LOCATIONS)
        if label and loc:
            label = f"{label} {loc}"
        elif loc and not saw_neg:
            label = loc
        elif loc_neg and not label:
            saw_neg = True
    if label:
        return label, False
    if topic == "cover" and _NEGATED_EXPOSURE_RE.search(text or ""):
        return "", True
    return "", saw_neg


def _dot_is_sentence_end(text: str, index: int) -> bool:
    """False for a '.' inside a number or an abbreviation."""
    if index < 0 or index >= len(text) or text[index] != ".":
        return False
    prev = text[index - 1] if index else ""
    nxt = text[index + 1] if index + 1 < len(text) else ""
    if prev.isdigit() and nxt.isdigit():
        return False
    if _ABBREV_DOT_RE.search(text[max(0, index - 8): index + 1]):
        return False
    return True


def _clause_window(text: str, start: int, end: int) -> str:
    """The sentence that holds the figure. A neighbouring sentence does not."""
    body = text or ""
    begin = 0
    for i in range(min(start, len(body)) - 1, -1, -1):
        if _dot_is_sentence_end(body, i):
            begin = i + 1
            break
    stop = len(body)
    for i in range(max(end, 0), len(body)):
        if _dot_is_sentence_end(body, i):
            stop = i + 1
            break
    return body[begin:stop]


def _labels_in(topic: str, text: str) -> str:
    """Material and location named in ``text``, or "" when negated or absent."""
    label, _negated = _condition_binding(topic, text)
    return label


def _heading_binding(topic: str, text: str, figure_start: int) -> tuple[str, str, bool]:
    """Condition, clause, and whether the nearest heading negates it.

    Numbered clause headings ("8.4 Backfill"), section titles
    ("Specification Section 9"), and a short note heading
    ("Bottom of footings") all count. The search walks upward, so a
    heading several lines above the figure, or at the chunk start, still
    binds. A closer heading that names a condition wins over a section
    title further up. A closer heading that negates the condition stops
    the walk.
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
            cond, neg = _condition_binding(topic, title or line)
            if not cond and title:
                cond, neg = _condition_binding(topic, line)
            if neg and not cond:
                return "", number, True
            if cond:
                return cond, number, False
            continue
        section = _SECTION_TITLE_RE.match(line)
        if section:
            number = section.group(1) or ""
            title = section.group(2) or ""
            if number and not clause:
                clause = number
            cond, neg = _condition_binding(topic, title or line)
            if not cond and title:
                cond, neg = _condition_binding(topic, line)
            if neg and not cond:
                return "", clause, True
            if cond:
                return cond, clause, False
            continue
        if len(line) <= 80 and "." not in line:
            cond, neg = _condition_binding(topic, line)
            if neg and not cond:
                return "", clause, True
            if cond:
                return cond, clause, False
    return "", clause, False


def _title_condition(topic: str, source: str) -> str:
    """Condition carried only by the document title."""
    name = source or ""
    if topic == "compaction" and _STORM_WATER_RE.search(name):
        return "storm water network"
    return _labels_in(topic, name)


def _bind_figure(
    topic: str, text: str, start: int, end: int, source: str,
) -> tuple[str, str, bool, bool]:
    """Condition, clause, self-spec, and whether the figure's condition is negated.

    The figure's own sentence wins. A negation there does not inherit a
    heading or a title. Otherwise the nearest clause heading, then the
    section title, then the document title.
    """
    window = _clause_window(text or "", start, end)
    inline, inline_neg = _condition_binding(topic, window)
    heading_cond, heading_clause, heading_neg = _heading_binding(topic, text, start)
    calls = bool(_SELF_SPEC_RE.search(text or ""))
    clause = heading_clause or _clause_before(text, end)
    if inline:
        return inline, clause, calls, False
    if inline_neg or (heading_neg and not heading_cond):
        return "", clause, calls, True
    if heading_cond:
        return heading_cond, clause, calls, False
    return _title_condition(topic, source), clause, calls, False


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


def _emit_not_the_spec(hit: _Hit, class_name: str) -> bool:
    """True only for a positive non-spec document the body does not claim.

    The filename is a hint. A body or chunk that calls the document a
    specification is never labelled "not the specification".
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


def _state_line(topic: str, hit: _Hit, class_name: str) -> str:
    # The printed condition is the figure's own. A negated figure has none.
    condition = "" if hit.negated else (hit.condition or "")
    cond = f" for {condition}" if condition else ""
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
