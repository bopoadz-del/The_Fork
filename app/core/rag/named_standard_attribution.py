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


def _honest_absence(
    missing: list[NamedStandard],
    excerpts: list[Excerpt],
    answer: str,
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
    chosen = matched if matched else others[:2]
    notes: list[str] = []
    for excerpt, qty in chosen[:4]:
        label = excerpt.source_name or "a project document"
        notes.append(
            f'The project document "{label}" states {qty.display}. '
            f"That figure is project-only and is not {whose}."
        )
    return "\n\n".join([absence, *notes])


def absence_note(query: str, chunks) -> str:
    """Steering line for the RAG system message. Empty when it does not apply."""
    if not gate_enabled():
        return ""
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
    if not _needs_relabel(answer or "", missing, excerpts):
        return answer
    return _honest_absence(missing, excerpts, answer or "")
