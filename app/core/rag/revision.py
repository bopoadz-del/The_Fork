"""Revision currency (audit §5.2) — ALWAYS ON, flag-independent.

A stale-revision answer on a drawing (a dimension, detail, or spec that changed
in a later revision) is a real construction-safety and liability hazard, and it
is exactly the failure this product exists to prevent. This module supplies the
pure, filename-derived signals the retriever uses to:

  1. firmly DOWN-RANK any document whose own filename marks it superseded
     (``superseded|obsolete|archive|old|previous|deprecated``), so a current
     document of comparable relevance always outranks it; and
  2. among retrieved chunks that share a DRAWING NUMBER, prefer the highest
     revision — suppressing a lower revision when a strictly-higher, comparable
     one is also retrieved.

Everything keys off the ORIGINAL FILENAME the uploader gave — the one signal the
retriever already resolves per chunk (``_doc_name_for_id``). So nothing here
depends on ``RAG_LAYERED``, on the layered-authority backfill, or on any
persisted metadata column: revision currency holds even with layered RAG off.

Conservatism is the rule. An unparseable name yields no signal and the chunk is
left exactly where cosine put it; two revisions are only ordered when they are
the same KIND (both numeric or both alphabetic) — guessing that ``Rev 2``
supersedes ``Rev B`` could suppress a current document, the one outcome worse
than showing both.
"""
from __future__ import annotations

import re
from typing import Optional, Tuple

# Keyword set shared verbatim with the layered-RAG 'historical' authority
# classifier (app/core/rag/layers.py::_AUTHORITY_PATTERNS). Kept here as the
# single source of truth so the always-on penalty below and the (flag-gated)
# layered down-weight can never disagree on what "superseded" means.
SUPERSEDED_PATTERN = r"superseded|obsolete|archive|\bold\b|previous|deprecated"
_SUPERSEDED_RE = re.compile(SUPERSEDED_PATTERN, re.IGNORECASE)

# Drawing sheet code, e.g. 'A-101', 'STR2100', 'M_401'. Same shape the
# construction container extracts (_extract_drawing_number): 1-3 letters, an
# optional -/_ separator, then 3-6 digits.
_DRAWING_NUMBER_RE = re.compile(r"[A-Z]{1,3}[-_]?\d{3,6}", re.IGNORECASE)

# Revision token, e.g. 'Rev C', 'R2', 'rev A'. Same regex the construction
# container and the drawing-QTO title-block parser use (_extract_revision) so a
# filename parses to the SAME revision everywhere. Captures a single trailing
# alphanumeric; multi-character revisions ('Rev 10') are a known limit inherited
# from that parser and degrade safely (see revision_rank).
_REVISION_RE = re.compile(r"[Rr][Ee]?[Vv]?\s*([A-Z0-9])")

# Firm demotion applied to a superseded chunk's fused score. Large relative to
# the cosine spread among top-K neighbours (typically ~0.0-0.2) so a current
# document of comparable relevance reliably outranks a superseded one — yet a
# subtraction, NOT a hard drop, so a superseded chunk still survives as a LAST
# resort when nothing current matches (a flagged stale answer beats no answer).
SUPERSEDED_PENALTY = 0.5


def is_superseded(name: Optional[str]) -> bool:
    """True when a filename marks the document superseded/obsolete/archived."""
    return bool(name and _SUPERSEDED_RE.search(name))


def drawing_number(name: Optional[str]) -> str:
    """Uppercased drawing sheet code from a filename, or '' when none matches."""
    if not name:
        return ""
    m = _DRAWING_NUMBER_RE.search(name)
    return m.group(0).upper() if m else ""


def revision_token(name: Optional[str]) -> str:
    """Uppercased single-character revision token from a filename, or ''.

    Matches the construction container's ``_extract_revision`` exactly so a
    given filename yields the same revision here and at ingest time.
    """
    if not name:
        return ""
    m = _REVISION_RE.search(name)
    return m.group(1).upper() if m else ""


def revision_rank(token: str) -> Optional[Tuple[int, int]]:
    """Sortable key for a revision token, or ``None`` when it cannot be ordered.

    Returns ``(kind, value)`` where ``kind`` is ``0`` for a numeric revision
    (``0 < 1 < 2``) and ``1`` for an alphabetic one (``A < B < C``). Callers
    must compare ranks ONLY within the same ``kind``: a numeric revision and a
    letter revision of the same sheet are deliberately left un-ordered so a
    mixed-scheme sheet keeps both rather than risking suppression of the current
    one. An empty/unparseable token returns ``None`` (no ordering, keep as-is).
    """
    if not token:
        return None
    token = token.upper()
    if token.isdigit():
        return (0, int(token))
    if len(token) == 1 and "A" <= token <= "Z":
        return (1, ord(token))
    return None
