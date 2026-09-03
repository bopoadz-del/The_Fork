"""Contract Data / Appendix-to-Tender / Contract Particulars chunking.

Live Conditions of Contract Q&A missed filled-in figures after a ~159-chunk
reindex because:

  1. "Accepted Contract Amount excluding VAT" grounded on the GC defined-term
     glossary ("X means the amount accepted…") instead of the particulars row.
  2. Default ``chunk_text`` does ``text.split()`` then 500-word windows, so a
     table row is flattened and cut mid-value (digits vs words disagreed;
     delay-damages sat after the cut).

This module re-serialises whatever the document already contains. It does not
embed live client names or live contract figures.
"""
from __future__ import annotations

import re

_CONTRACT_DATA_LABEL = (
    "CONTRACT DATA particulars — filled-in amount / duration / percentage"
)

_CONTRACT_DATA_HEADING_RE = re.compile(
    r"(?im)^[ \t]*(?:particular\s+conditions\s+part\s+a\s*[\-–:.]?\s*)?"
    r"(?:part\s+[a-z0-9ivx]+\s*[\-–.:)]\s*)?"
    r"(?:contract\s+data|appendix\s+to\s+(?:the\s+)?tender|contract\s+particulars)"
    r"\b"
)

_CONTRACT_DATA_END_RE = re.compile(
    r"(?im)^[ \t]*(?:"
    r"particular\s+conditions\s+part\s+b|"
    r"part\s+b\s*[\-–:.]?\s*special\s+provisions|"
    r"special\s+provisions\b|"
    r"general\s+conditions(?:\s+of\s+contract)?\b|"
    r"table\s+of\s+contents\b|"
    r"(?:1\.1\s+)?definitions\b"
    r")"
)

_CONTRACT_DATA_FILENAME_RE = re.compile(
    r"contract\s*data|conditions\s+of\s+contract|particular\s+conditions|"
    r"appendix\s+to\s+(?:the\s+)?tender|contract\s+particulars",
    re.IGNORECASE,
)

_CD_CLAUSE_LINE_RE = re.compile(r"^\s*(\d+(?:\.\d+){1,3})\s+(\S.*)$")
_CD_PIPE_ROW_RE = re.compile(r"^\s*(.+?)\s*\|\s*(.+?)\s*$")
_CD_DOT_LEADER_RE = re.compile(r"^(.+?)\s*\.{3,}\s*(.+)$")
_CD_TWO_COL_RE = re.compile(r"^(.+?)\s{2,}(\S.*)$")
_CD_SEP_ONLY_RE = re.compile(r"^[\s|:\-–—]+$")
_CD_FILLED_VALUE_RE = re.compile(
    r"(?i)(?:\b(?:sar|aed|usd|eur|gbp|qar|bhd|kwd|omr)\b|"
    r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|"
    r"\d[\d,]*\.\d{2}|"
    r"\d+(?:\.\d+)?\s*%|"
    r"\d+\s+(?:calendar\s+|working\s+)?days?|"
    r"\bnot\s+applicable\b|\bn/?a\b)"
)
_CD_HIGH_SIGNAL_KEY_RE = re.compile(
    r"(?i)accepted\s+contract\s+amount|delay\s+damages|liquidated\s+damages|"
    r"time\s+for\s+completion|defects\s+notification|"
    r"performance\s+(?:bond|security|guarantee)|retention"
)


def contract_data_heading_spans(text: str) -> list[tuple[int, int]]:
    """Character spans of Contract Data / Appendix-to-Tender sections."""
    if not text:
        return []
    spans: list[tuple[int, int]] = []
    for match in _CONTRACT_DATA_HEADING_RE.finditer(text):
        start = match.start()
        rest = text[match.end():]
        end_rel = len(rest)
        end_m = _CONTRACT_DATA_END_RE.search(rest)
        if end_m:
            end_rel = end_m.start()
        next_h = _CONTRACT_DATA_HEADING_RE.search(rest)
        if next_h and next_h.start() < end_rel:
            end_rel = next_h.start()
        end = match.end() + end_rel
        if end > start:
            spans.append((start, end))
    return spans


def _dense_particulars_span(text: str) -> tuple[int, int] | None:
    """Filename-hint fallback: a cluster of clause-numbered / pipe KV rows."""
    if not text:
        return None
    lines = text.splitlines(keepends=True)
    kv_flags: list[bool] = []
    offsets: list[int] = []
    pos = 0
    for line in lines:
        offsets.append(pos)
        stripped = line.strip()
        kv_flags.append(
            bool(stripped)
            and not _CD_SEP_ONLY_RE.match(stripped)
            and (
                bool(_CD_CLAUSE_LINE_RE.match(stripped))
                or bool(_CD_PIPE_ROW_RE.match(stripped))
                or bool(_CD_DOT_LEADER_RE.match(stripped))
            )
        )
        pos += len(line)
    best: tuple[int, int] | None = None
    i = 0
    n = len(kv_flags)
    while i < n:
        if not kv_flags[i]:
            i += 1
            continue
        j = i
        hits = 0
        while j < n:
            if kv_flags[j]:
                hits += 1
                j += 1
                continue
            gap = 0
            k = j
            while k < n and not kv_flags[k] and gap < 4:
                if lines[k].strip():
                    gap += 1
                k += 1
            if k < n and kv_flags[k] and gap < 4:
                j = k
                continue
            break
        if hits >= 5:
            start = offsets[i]
            end = offsets[j - 1] + len(lines[j - 1]) if j > 0 else start
            if best is None or (end - start) > (best[1] - best[0]):
                best = (start, end)
        i = max(j, i + 1)
    return best


def contract_data_spans(text: str, filename: str = "") -> list[tuple[int, int]]:
    spans = contract_data_heading_spans(text)
    if spans:
        return spans
    if filename and _CONTRACT_DATA_FILENAME_RE.search(filename):
        dense = _dense_particulars_span(text)
        if dense:
            return [dense]
    return []


def _split_key_value_line(line: str) -> tuple[str, str] | None:
    stripped = (line or "").strip()
    if not stripped or _CD_SEP_ONLY_RE.match(stripped):
        return None
    for rx in (_CD_PIPE_ROW_RE, _CD_DOT_LEADER_RE, _CD_TWO_COL_RE):
        m = rx.match(stripped)
        if m:
            key, val = m.group(1).strip(), m.group(2).strip()
            if key and val:
                return key, val
    m = _CD_CLAUSE_LINE_RE.match(stripped)
    if m:
        clause, rest = m.group(1), m.group(2).strip()
        nested = None
        for rx in (_CD_PIPE_ROW_RE, _CD_DOT_LEADER_RE, _CD_TWO_COL_RE):
            nested = rx.match(rest)
            if nested:
                break
        if nested:
            return f"{clause} {nested.group(1).strip()}", nested.group(2).strip()
        return f"{clause} {rest}", ""
    return None


def _is_cd_continuation(line: str) -> bool:
    raw = line or ""
    s = raw.strip()
    if not s:
        return False
    if _split_key_value_line(s) is not None:
        return False
    if _CD_CLAUSE_LINE_RE.match(s):
        return False
    if raw[:1] in " \t":
        return True
    if s.startswith("(") or s.startswith("["):
        return True
    return bool(_CD_FILLED_VALUE_RE.search(s))


def parse_contract_data_rows(section: str) -> list[tuple[str, str]]:
    """Parse a Contract Data section into intact (key, value) rows.

    Continuation lines (indented amount-in-words, a lone currency figure)
    stay attached to the current key so digits and words of one particulars
    row are never split.
    """
    rows: list[tuple[str, str]] = []
    current_key = ""
    current_val = ""

    def _flush() -> None:
        nonlocal current_key, current_val
        key = current_key.strip()
        val = re.sub(r"\s+", " ", current_val).strip()
        if key:
            rows.append((key, val))
        current_key, current_val = "", ""

    for raw in (section or "").splitlines():
        if not raw.strip():
            continue
        if current_key and _is_cd_continuation(raw):
            current_val = f"{current_val} {raw.strip()}".strip()
            continue
        parsed = _split_key_value_line(raw)
        if parsed is None:
            if current_key:
                current_val = f"{current_val} {raw.strip()}".strip()
            continue
        _flush()
        current_key, current_val = parsed
    _flush()
    return rows


def _pack_contract_data_rows(
    rows: list[tuple[str, str]],
    *,
    max_chars: int = 1400,
    overlap_rows: int = 2,
) -> list[list[tuple[str, str]]]:
    """Group intact rows into windows. Never splits a single key/value row."""
    if not rows:
        return []
    packs: list[list[tuple[str, str]]] = []
    i = 0
    n = len(rows)
    while i < n:
        pack: list[tuple[str, str]] = []
        size = 0
        j = i
        while j < n:
            key, val = rows[j]
            piece = len(key) + len(val) + 4
            if pack and size + piece > max_chars:
                break
            pack.append(rows[j])
            size += piece
            j += 1
        if not pack:
            pack = [rows[i]]
            j = i + 1
        packs.append(pack)
        if j >= n:
            break
        nxt = j - overlap_rows
        i = j if nxt <= i else nxt
    return packs


def _format_cd_chunk(rows: list[tuple[str, str]], filename: str, heading: str) -> str:
    src = f" [{filename}]" if filename else ""
    head = heading.strip() or "Contract Data"
    body = "\n".join(f"{key}: {val}" if val else key for key, val in rows)
    return f"{_CONTRACT_DATA_LABEL}{src}.\n{head}\n{body}".strip()


def _line_packed_chunks(section: str, filename: str, heading: str) -> list[str]:
    lines = [ln for ln in section.splitlines() if ln.strip()]
    if not lines:
        return []
    packed: list[str] = []
    buf: list[str] = []
    size = 0
    for ln in lines:
        if buf and size + len(ln) + 1 > 1400:
            packed.append("\n".join(buf))
            buf = buf[-2:]
            size = sum(len(x) + 1 for x in buf)
        buf.append(ln)
        size += len(ln) + 1
    if buf:
        packed.append("\n".join(buf))
    src = f" [{filename}]" if filename else ""
    return [
        f"{_CONTRACT_DATA_LABEL}{src}.\n{heading}\n{body}".strip()
        for body in packed
    ]


_CD_ALNUM_RE = re.compile(r"[A-Za-z0-9]")


def particulars_chunk_states_a_value(chunk: str) -> bool:
    """True when a rendered particulars chunk carries at least one filled row.

    The counterpart to :func:`contract_data_particulars_chunks`: retrieval
    needs to tell a window that states a particular from one that is a list
    of unfilled keys, and only this module knows the rendered format.

    ``_format_cd_chunk`` writes a filled row as ``key: value`` and an empty
    one as a bare ``key``, so a colon with content after it IS the filled
    marker. Chunks from the raw-line fallback keep the document's own
    separator, so those are re-parsed with the same splitter the section
    parser uses.

    A value does not have to be a figure. ``1.3.1 (b) Engineer: <firm>`` and
    ``Schedule 10: Not Used`` are both filled in, and a numeric-only test
    cannot see either.
    """
    lines = (chunk or "").splitlines()
    # Line 0 is the label this module prepends; every candidate carries it.
    for line in lines[1:]:
        stripped = line.strip()
        if not stripped:
            continue
        key, sep, val = stripped.partition(":")
        if sep and key.strip() and _CD_ALNUM_RE.search(val):
            return True
        parsed = _split_key_value_line(stripped)
        if parsed and parsed[1]:
            return True
    return False


def contract_data_particulars_chunks(text: str, filename: str = "") -> list[str]:
    """Emit retrieval-ready Contract Data chunks, or ``[]`` when none found.

    Grouped windows never cut a key/value row. High-signal filled rows
    (Accepted Contract Amount, delay damages, Time for Completion, …) also
    get a dedicated one-row chunk so a truncated table cannot hide them.
    """
    if not text or not text.strip():
        return []
    spans = contract_data_spans(text, filename)
    if not spans:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for start, end in spans:
        section = text[start:end]
        heading_m = _CONTRACT_DATA_HEADING_RE.search(section)
        heading = heading_m.group(0).strip() if heading_m else "Contract Data"
        rows = parse_contract_data_rows(section)
        if not rows:
            for chunk in _line_packed_chunks(section, filename, heading):
                if chunk not in seen:
                    seen.add(chunk)
                    out.append(chunk)
            continue
        for pack in _pack_contract_data_rows(rows):
            chunk = _format_cd_chunk(pack, filename, heading)
            if chunk not in seen:
                seen.add(chunk)
                out.append(chunk)
        for key, val in rows:
            if not val or not _CD_HIGH_SIGNAL_KEY_RE.search(key):
                continue
            if not _CD_FILLED_VALUE_RE.search(val):
                continue
            chunk = _format_cd_chunk([(key, val)], filename, heading)
            if chunk not in seen:
                seen.add(chunk)
                out.append(chunk)
    return out
