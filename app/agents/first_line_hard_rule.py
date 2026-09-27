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
document only when different figures answer the same material and condition,
or when no named condition separates them. Figures that each carry a
different material or condition are stated together, each tied to that
condition and to the document, clause, or drawing that contains it. An
optional higher compaction degree in the same clause ("could be compacted
to … under the approval of the engineer") is not a second figure. A
millimetre counts as concrete cover only when it is tied to that quantity,
not to a panel, tile, or paint band. "Which contract governs this
project?" names the one contract in the excerpts, or asks which when more
than one non-template contract is visible.

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
_COMPACTION_LOCATIONS = (
    ("under foundations", re.compile(r"(?i)\bunder\s+foundations?\b")),
    ("under road pavement", re.compile(
        r"(?i)\bunder\s+(?:the\s+)?road\s+pavement\b"
    )),
)
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
            hits.append(_Hit(
                figure=figure,
                source=source,
                is_class=is_class,
                condition=_condition_near(topic, body, start, end),
                clause=_clause_before(body, end),
            ))
    return hits


def _select(
    hits: list[_Hit], answer: str, topic: str, ask: str,
) -> _Hit | list[_Hit] | _Bound:
    """One figure to state, the bound set, or the conflicting hits to ask.

    Same figure, any number of copies: one hit, so the line cites one copy.
    Different figures ask which document only when they answer the same
    material and condition, or when no named condition separates them, and
    they come from different documents. One document that still holds two
    figures for one condition does not ask. Figures that each name a
    different condition are returned bound, not as a question.
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
    if _conditions_distinguish(collapsed):
        asked = _condition_near(topic, ask or "", 0, len(ask or ""))
        return _Bound(tuple(_lead_first(collapsed, asked)))
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


def _condition_near(topic: str, text: str, start: int, end: int) -> str:
    window = _clause_window(text or "", start, end)
    patterns = _COVER_CONDITIONS if topic == "cover" else _COMPACTION_MATERIALS
    label = ""
    for name, rx in patterns:
        if rx.search(window):
            label = name
            break
    if topic == "compaction" and label:
        for loc, rx in _COMPACTION_LOCATIONS:
            if rx.search(window):
                label = f"{label} {loc}"
                break
    return label


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


def _lead_first(hits: list[_Hit], asked: str) -> list[_Hit]:
    if not asked:
        return list(hits)
    return sorted(hits, key=lambda hit: 0 if hit.condition == asked else 1)


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
    if not hit.is_class and class_name:
        if f"not the {class_name}" not in first.lower():
            return False
    return True


def _state_line(topic: str, hit: _Hit, class_name: str) -> str:
    if topic == "cover":
        line = f"{hit.figure} is the concrete-cover figure in {hit.source}."
    else:
        line = (
            f"{hit.figure} of maximum dry density is the compaction figure "
            f"in {hit.source}."
        )
    if not hit.is_class and class_name:
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
    if class_name and hits and not any(hit.is_class for hit in hits):
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
