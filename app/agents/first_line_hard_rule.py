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
named source. "Which contract governs this project?" names the one contract
in the excerpts, or asks which when more than one non-template contract is
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
_COVER_WORD_RE = re.compile(r"(?i)\bcovers?\b")
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


@dataclass(frozen=True)
class _Hit:
    figure: str
    source: str
    is_class: bool


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
    chosen = _select(hits, text)
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
        numbers = (
            _cover_numbers(record.text or "")
            if topic == "cover"
            else _compaction_numbers(record.text or "")
        )
        is_class = filename_is_source_class(source, class_name)
        for number in numbers:
            figure = f"{number} mm" if topic == "cover" else f"{number}%"
            key = (figure, source)
            if key in seen:
                continue
            seen.add(key)
            hits.append(_Hit(figure=figure, source=source, is_class=is_class))
    return hits


def _select(hits: list[_Hit], answer: str) -> _Hit | list[_Hit]:
    """One figure to state, or every remaining hit when the line must ask."""
    class_hits = [hit for hit in hits if hit.is_class]
    pool = class_hits or hits
    figures: list[str] = []
    for hit in pool:
        if hit.figure not in figures:
            figures.append(hit.figure)
    if len(figures) == 1:
        sources: list[str] = []
        for hit in pool:
            if hit.source not in sources:
                sources.append(hit.source)
        if len(sources) == 1:
            return pool[0]
        return _one_per_source(pool)
    stated = [figure for figure in figures if _figure_in(answer, figure)]
    if len(stated) == 1:
        for hit in pool:
            if hit.figure == stated[0]:
                return hit
    return _one_per_source(pool)


def _one_per_source(hits: list[_Hit]) -> list[_Hit]:
    out: list[_Hit] = []
    seen: set[tuple[str, str]] = set()
    for hit in hits:
        key = (hit.figure, hit.source)
        if key in seen:
            continue
        seen.add(key)
        out.append(hit)
    return out


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


def _cover_numbers(text: str) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    for match in _MM_RE.finditer(text or ""):
        window = text[max(0, match.start() - 100): min(len(text), match.end() + 100)]
        if not _COVER_WORD_RE.search(window):
            continue
        number = _trim_num(match.group(1))
        if number in seen:
            continue
        seen.add(number)
        found.append(number)
    return found


def _compaction_numbers(text: str) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    for pattern in _MDD_RES:
        for match in pattern.finditer(text or ""):
            number = _trim_num(match.group(1))
            if number in seen:
                continue
            seen.add(number)
            found.append(number)
    return found


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
