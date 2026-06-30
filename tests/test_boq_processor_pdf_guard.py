"""Guard: an oversized BOQ PDF must be refused, never OOM the worker.

``boq_processor._parse_pdf`` runs ``pdfplumber.extract_tables()`` across every
page, which loads the whole PDF into memory. On the 2 GB box a large priced
BOQ PDF OOM-killed the worker and 502'd every concurrent user. The size guard
refuses above ``BOQ_PDF_MAX_MB`` with a clear pointer to the xlsx/csv path
instead of crashing. These tests pin that the guard fires above the cap and
stays out of the way below it.
"""
import asyncio
import os
import tempfile

from app.blocks.boq_processor import BOQProcessorBlock


def _make_pdf(size_mb: float) -> str:
    """Write a throwaway .pdf of approximately ``size_mb`` megabytes.

    Content is junk — the size guard checks os.path.getsize BEFORE pdfplumber
    ever opens the file, so the bytes never need to be a valid PDF.
    """
    fd, path = tempfile.mkstemp(suffix=".pdf")
    with os.fdopen(fd, "wb") as fh:
        fh.write(b"%PDF-1.4\n")
        fh.write(b"0" * int(size_mb * 1024 * 1024))
    return path


def test_oversize_pdf_refused_without_parsing(monkeypatch):
    monkeypatch.setenv("BOQ_PDF_MAX_MB", "20")
    path = _make_pdf(21)
    try:
        result = asyncio.run(BOQProcessorBlock().process({"file_path": path}))
    finally:
        os.remove(path)
    assert result["status"] == "error"
    assert result.get("boq_pdf_too_large") is True
    # The message must steer the operator to the safe path.
    assert "xlsx" in result["error"].lower()


def test_small_pdf_not_blocked_by_size_guard(monkeypatch):
    monkeypatch.setenv("BOQ_PDF_MAX_MB", "20")
    path = _make_pdf(0.001)  # ~1 KB — well under the cap
    try:
        result = asyncio.run(BOQProcessorBlock().process({"file_path": path}))
    finally:
        os.remove(path)
    # The guard must NOT trip. (Parsing junk bytes still fails downstream — a
    # "no tables" / parse error — but that must NOT be the size refusal.)
    assert not result.get("boq_pdf_too_large")
