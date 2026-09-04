"""One place that decides what an ingested document actually is.

The corpus was re-ingested repeatedly and every run reported success, because
"a documents row exists" and "text came out of it" were the same signal. This
module separates them, and it lives on its own so the rule cannot drift between
the three callers that need it: migration 0016's backfill, the offline stamper,
and the index-time gate in ``doc_index``.

Measured on the live corpus 2026-09-04 (see artifacts INGEST_PROOF / the gap
receipt) -- the numbers below are calibration evidence, not preference:

* 1,873 of 2,691 PDFs in ``client_infra_pack_1`` held exactly ONE chunk while
  being indistinguishable from a fully-extracted specification.
* A chars-per-MB gate was tried and REJECTED: 935 one-chunk documents sit above
  any defensible cut, because chars/MB conflates image-heavy with text-poor.
* OCR is NOT the remedy for those sheets. Rendering them and running Tesseract
  returned 0.6-1.4x the characters the text layer already had, higher DPI was
  worse, and novel-token counts of 1-50 per sheet are consistent with noise on
  line-work. They are vector CAD exports whose content is geometry.
"""
from __future__ import annotations

import os
from typing import NamedTuple

# ── status vocabulary (mirrors the CHECK constraint in migration 0016) ────────

UNVERIFIED = "UNVERIFIED"
INDEXED = "INDEXED"
TEXT_SPARSE = "TEXT_SPARSE"
ZERO_CHUNK = "ZERO_CHUNK"
UNSUPPORTED_TYPE = "UNSUPPORTED_TYPE"
EXTRACT_FAILED = "EXTRACT_FAILED"
TOMBSTONED = "TOMBSTONED"
QUARANTINED = "QUARANTINED"

ALL_STATUSES = frozenset(
    {
        UNVERIFIED,
        INDEXED,
        TEXT_SPARSE,
        ZERO_CHUNK,
        UNSUPPORTED_TYPE,
        EXTRACT_FAILED,
        TOMBSTONED,
        QUARANTINED,
    }
)

#: Statuses that represent OPEN work. Everything else is a settled outcome.
#: ``TEXT_SPARSE`` is deliberately absent -- see ``is_open``.
OPEN_STATUSES = frozenset({UNVERIFIED, ZERO_CHUNK, EXTRACT_FAILED})

# Reason suffixes that qualify TEXT_SPARSE and UNSUPPORTED_TYPE.
TERMINAL = "terminal"        # nothing left to recover, anywhere
RECOVERABLE = "recoverable"  # a richer source for this document exists

# Formats that can yield text at all. Everything else registers as
# UNSUPPORTED_TYPE rather than sitting at zero chunks looking like a failure.
#
# Consulted ONLY for documents that produced nothing -- see ``classify``. The
# geospatial and archive entries are here on measured evidence, not intuition:
# .kmz averages 43 chunks/doc across 311 documents (it is zipped KML, i.e.
# XML), .zip averages 119 across 5, and .ifc is a STEP text format.
TEXT_BEARING_EXTS = frozenset(
    {
        ".pdf", ".txt", ".md", ".csv", ".json", ".xml",
        ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
        ".rtf", ".msg", ".htm", ".html",
        ".kmz", ".kml", ".zip", ".ifc",
    }
)

# Formats whose text lives in a convertible source rather than the file we hold.
# ``.dwg`` carries real TEXT/MTEXT/ATTRIB entities; recovering them is the
# separate F-DWG campaign, so they are marked recoverable, not terminal.
RECOVERABLE_EXTS = frozenset({".dwg", ".dxf"})

# ── density gate ─────────────────────────────────────────────────────────────
# Calibrated on 10 known-sparse drawing sheets vs 10 known-rich text PDFs from
# the live corpus. The measurement overturned the obvious design:
#
#            chars/page                    bytes/page
#   sparse   median 1555, range 132-3594   median 520253, MIN 206016
#   rich     median 2370, range 1767-3042  median  34029, MAX 113462
#
# chars-per-page OVERLAPS almost completely -- a drawing sheet's title block
# carries as much text as a dense page of prose, so it cannot convict on its
# own and is not the primary signal. bytes-per-page separates cleanly, with an
# empty band between 113k and 206k; the threshold sits in that gap.
#
# chars-per-page survives only as a BACKSTOP, set well above both observed
# ranges: a physically large page that really is full of text must never be
# flagged, however heavy the file.
_SPARSE_BYTES_PER_PAGE = int(os.getenv("INGEST_SPARSE_BYTES_PER_PAGE", "150000"))
_SPARSE_CHARS_PER_PAGE = int(os.getenv("INGEST_SPARSE_CHARS_PER_PAGE", "6000"))


class Classification(NamedTuple):
    status: str
    reason: str | None

    @property
    def is_open(self) -> bool:
        return is_open(self.status, self.reason)


def is_open(status: str, reason: str | None = None) -> bool:
    """True when this document still represents work.

    ``TEXT_SPARSE`` is the subtle one. A vector drawing sheet with no
    convertible source is a SETTLED, honest outcome -- the sheet genuinely
    holds ~1.6-2.9k characters and no amount of OCR changes that. Treating it
    as open would build a gate that can never go green, which is the same trap
    as targeting a file count that includes formats which never chunk.
    """
    if status in OPEN_STATUSES:
        return True
    if status in (TEXT_SPARSE, UNSUPPORTED_TYPE):
        return reason is not None and reason.endswith(RECOVERABLE)
    return False


def classify(
    *,
    chunk_count: int,
    extension: str,
    chars: int | None = None,
    page_count: int | None = None,
    size_bytes: int | None = None,
    has_convertible_source: bool = False,
) -> Classification:
    """Decide a document's ingest status from what extraction actually produced.

    ``has_convertible_source`` means a richer original exists in the corpus --
    e.g. a ``.dwg`` sharing this sheet's name. It is what splits a sparse sheet
    into work (RECOVERABLE) versus a closed outcome (TERMINAL).
    """
    ext = (extension or "").lower()
    if not ext.startswith(".") and ext:
        ext = "." + ext

    # EVIDENCE BEATS POLICY. A document that produced chunks yielded text, and
    # no extension list may overrule that. Ordering this the other way round
    # was caught in a dry run about to demote 172 documents holding 14,000
    # real chunks -- 311 .kmz files averaging 43 chunks each (zipped KML is
    # XML), and .zip archives averaging 119. The list below was simply wrong,
    # which is the argument for consulting it last and only when there is no
    # evidence to consult instead.
    if chunk_count <= 0:
        if ext in RECOVERABLE_EXTS:
            return Classification(UNSUPPORTED_TYPE, f"{ext.lstrip('.')}:{RECOVERABLE}")
        if ext and ext not in TEXT_BEARING_EXTS:
            return Classification(UNSUPPORTED_TYPE, f"{ext.lstrip('.')}:{TERMINAL}")
        # A zero-byte file is a settled outcome, not a failed extraction: the
        # first TIER-1 smoke upload was a 0-byte Drive placeholder that the
        # pipeline correctly reported as "0 chunks, error". 65 of the 231
        # reachable "missing" files were empties. Only trusted together with
        # zero chunks -- the bulk backfill routes recorded size=0 on 2,507
        # documents that hold real chunks, so size alone convicts nothing.
        if size_bytes == 0:
            return Classification(UNSUPPORTED_TYPE, f"empty_file:{TERMINAL}")
        return Classification(ZERO_CHUNK, None)

    qualifier = RECOVERABLE if has_convertible_source else TERMINAL

    # A single chunk means the whole extracted text fit one ~500-word window.
    # This needs no tuned threshold and is why the backfill can run in SQL.
    if chunk_count == 1:
        return Classification(TEXT_SPARSE, f"single_window:{qualifier}")

    # Multi-chunk documents can still be sparse for their physical extent --
    # a 40-sheet drawing set produces one title-block chunk per sheet.
    if _is_sparse_for_extent(chars, page_count, size_bytes):
        return Classification(TEXT_SPARSE, f"low_density:{qualifier}")

    return Classification(INDEXED, None)


def _is_sparse_for_extent(
    chars: int | None, page_count: int | None, size_bytes: int | None
) -> bool:
    """Bytes-per-page convicts; chars-per-page can only acquit.

    Missing data never convicts. Callers that cannot supply page counts (the
    SQL backfill) do not reach this test -- they stop at ``chunk_count == 1``.
    """
    if not page_count or page_count <= 0:
        return False
    if size_bytes is None:
        return False
    if size_bytes / page_count <= _SPARSE_BYTES_PER_PAGE:
        return False
    # Backstop: a heavy page that is nonetheless full of text is a real
    # document (a large-format scan OCR'd successfully), not a drawing.
    return not (chars is not None and chars / page_count >= _SPARSE_CHARS_PER_PAGE)
