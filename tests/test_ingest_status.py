"""The ledger must not be able to call an unusable document INDEXED.

Every prior ingest of this corpus reported success. It could, because "a
documents row exists" and "usable text came out" were the same signal. These
tests pin the separation, and the numbers in them are measurements against the
live corpus (2026-09-04), not invented fixtures.
"""
from __future__ import annotations

import importlib

import pytest

from app.core import ingest_status as ist


# Calibration measured on 10 known-sparse drawing sheets vs 10 known-rich text
# PDFs. The gap between these two numbers is empty in the sample; the threshold
# lives inside it. If either bound moves, the gate's justification moves too.
RICH_MAX_BYTES_PER_PAGE = 113_462
SPARSE_MIN_BYTES_PER_PAGE = 206_016


def test_one_chunk_is_never_indexed():
    """The defect this whole ledger exists for.

    1,873 of 2,691 PDFs in one project held exactly one chunk and were
    indistinguishable from a fully-extracted specification.
    """
    c = ist.classify(chunk_count=1, extension=".pdf")
    assert c.status == ist.TEXT_SPARSE
    assert c.status != ist.INDEXED


def test_multi_chunk_text_document_is_indexed():
    assert ist.classify(chunk_count=42, extension=".pdf").status == ist.INDEXED


def test_zero_chunks_is_zero_chunk_not_sparse():
    assert ist.classify(chunk_count=0, extension=".pdf").status == ist.ZERO_CHUNK


@pytest.mark.parametrize("ext", [".dwg", ".dxf"])
def test_cad_sources_are_unsupported_but_recoverable(ext):
    """.dwg carries real TEXT/MTEXT entities, so it is work, not a dead end."""
    c = ist.classify(chunk_count=0, extension=ext)
    assert c.status == ist.UNSUPPORTED_TYPE
    assert c.is_open, "a convertible CAD source must stay visible as open work"


@pytest.mark.parametrize("ext", [".jpg", ".ttf", ".gdbtable", ".shx"])
def test_formats_that_can_never_yield_text_are_closed(ext):
    c = ist.classify(chunk_count=0, extension=ext)
    assert c.status == ist.UNSUPPORTED_TYPE
    assert not c.is_open, "a format with no text cannot be a permanent red gate"


@pytest.mark.parametrize(
    "ext,chunks",
    [(".kmz", 43), (".zip", 119), (".jpg", 1), (".ifc", 1), (".png", 1)],
)
def test_evidence_beats_the_extension_list(ext, chunks):
    """A document that produced chunks yielded text, whatever its extension.

    Checking policy before evidence was caught in a dry run about to demote
    172 documents holding ~14,000 real chunks to UNSUPPORTED_TYPE -- 311 .kmz
    averaging 43 chunks each, .zip averaging 119. The extension list was
    wrong, which is exactly why it must be consulted last.
    """
    c = ist.classify(chunk_count=chunks, extension=ext)
    assert c.status != ist.UNSUPPORTED_TYPE
    assert c.status == (ist.TEXT_SPARSE if chunks == 1 else ist.INDEXED)


def test_empty_file_with_no_chunks_is_settled_not_open():
    """First TIER-1 smoke upload: a 0-byte Drive placeholder, correctly
    reported by the pipeline as 0 chunks. That is not work."""
    c = ist.classify(chunk_count=0, extension=".pdf", size_bytes=0)
    assert c.status == ist.UNSUPPORTED_TYPE
    assert c.reason == "empty_file:terminal"
    assert not c.is_open


def test_size_zero_alone_convicts_nothing():
    """The bulk backfill routes wrote size=0 on 2,507 documents that hold real
    chunks. Evidence beats the size column too."""
    assert ist.classify(chunk_count=40, extension=".pdf", size_bytes=0).status == ist.INDEXED
    assert ist.classify(chunk_count=1, extension=".pdf", size_bytes=0).status == ist.TEXT_SPARSE


def test_extension_policy_still_applies_when_nothing_was_extracted():
    """The list is not dead -- it is just subordinate to evidence."""
    assert ist.classify(chunk_count=0, extension=".ttf").status == ist.UNSUPPORTED_TYPE


def test_terminal_sparse_is_settled_and_recoverable_sparse_is_work():
    """The distinction that stops the gate being permanently red.

    1,747 sparse sheets have no source to recover from; 126 have a .dwg.
    """
    terminal = ist.classify(chunk_count=1, extension=".pdf")
    recoverable = ist.classify(
        chunk_count=1, extension=".pdf", has_convertible_source=True
    )
    assert not terminal.is_open
    assert recoverable.is_open
    assert terminal.status == recoverable.status == ist.TEXT_SPARSE


# ── density gate ─────────────────────────────────────────────────────────────


def _dense(**kw):
    base = dict(chunk_count=40, extension=".pdf", page_count=40)
    base.update(kw)
    return ist.classify(**base)


def test_gate_does_not_flag_the_richest_measured_text_pdf():
    """False positives are worse than misses here: wrongly demoting a real
    specification hides it from the very report meant to surface gaps."""
    c = _dense(size_bytes=RICH_MAX_BYTES_PER_PAGE * 40, chars=2370 * 40)
    assert c.status == ist.INDEXED


def test_gate_flags_the_lightest_measured_drawing_sheet():
    c = _dense(size_bytes=SPARSE_MIN_BYTES_PER_PAGE * 40, chars=1555 * 40)
    assert c.status == ist.TEXT_SPARSE
    assert c.reason.startswith("low_density")


def test_chars_per_page_can_acquit_but_never_convict():
    """chars/page OVERLAPS between the two populations (sparse 132-3594, rich
    1767-3042), so it is a backstop only. A heavy page full of text is a real
    document -- a large-format scan that OCR'd well."""
    heavy_but_wordy = _dense(
        size_bytes=SPARSE_MIN_BYTES_PER_PAGE * 40, chars=20_000 * 40
    )
    assert heavy_but_wordy.status == ist.INDEXED


def test_missing_extent_data_never_convicts():
    """The SQL backfill supplies no page counts. It must fall through to the
    chunk_count rule rather than guessing."""
    assert _dense(page_count=None, size_bytes=None, chars=None).status == ist.INDEXED
    assert _dense(size_bytes=None, chars=None).status == ist.INDEXED
    assert _dense(page_count=0, size_bytes=10**9, chars=0).status == ist.INDEXED


def test_thresholds_are_env_tunable_without_a_deploy():
    monkey = pytest.MonkeyPatch()
    try:
        monkey.setenv("INGEST_SPARSE_BYTES_PER_PAGE", "999999999")
        importlib.reload(ist)
        # With the bar above anything real, nothing is convicted by density.
        assert (
            ist.classify(
                chunk_count=40, extension=".pdf", page_count=1,
                size_bytes=10_000_000, chars=10,
            ).status
            == ist.INDEXED
        )
    finally:
        monkey.undo()
        importlib.reload(ist)


def test_status_vocabulary_matches_the_migration_check_constraint():
    """0016 pins these in SQL. A status added here and not there is written to
    production as a constraint violation, at ingest time, on a live run."""
    from pathlib import Path

    src = Path("alembic/versions/0016_document_ingest_status.py").read_text(
        encoding="utf-8"
    )
    for status in ist.ALL_STATUSES:
        assert f'"{status}"' in src, f"{status} missing from migration 0016"


def test_migration_backfill_only_ever_touches_unverified_rows():
    """0016 was applied to production once out of band and stamped back, so
    the entrypoint re-runs it on deploy. Every UPDATE it makes must be scoped
    to UNVERIFIED rows, or a release silently regresses the stamper's
    refinements -- caught in review, would have shipped."""
    import re
    from pathlib import Path

    src = Path("alembic/versions/0016_document_ingest_status.py").read_text(
        encoding="utf-8"
    )
    updates = re.findall(r"UPDATE documents[^;]*?\"\s*\)\)", src, flags=re.S)
    assert updates, "expected backfill UPDATE statements in migration 0016"
    for stmt in updates:
        assert "ingest_status = 'UNVERIFIED'" in stmt, (
            "backfill statement not scoped to UNVERIFIED rows:\n" + stmt
        )


def test_mutation_probe_treating_any_chunk_as_success_reopens_the_defect():
    """MUTATION PROBE.

    The historical rule was "chunks > 0 means indexed". Expressed directly so
    the probe fails loudly if anyone restores it: under that rule the 1,873
    one-chunk sheets are INDEXED again and the ledger resumes lying.
    """
    def old_rule(chunk_count: int) -> str:
        return ist.INDEXED if chunk_count > 0 else ist.ZERO_CHUNK

    assert old_rule(1) == ist.INDEXED, "the old rule was already safe -- then " \
        "there was no defect to fix"
    assert ist.classify(chunk_count=1, extension=".pdf").status != old_rule(1)
    # ...and the two rules must still agree where the old one was right.
    assert ist.classify(chunk_count=40, extension=".pdf").status == old_rule(40)
