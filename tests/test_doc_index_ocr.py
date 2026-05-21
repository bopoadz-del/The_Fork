"""Tests for Stream F — OCR of scanned image documents and image-only PDFs
into the per-project search index.

Tesseract is not guaranteed in the test environment, so every test here
mocks ``OCRBlock.process`` with a canned async result.

Covered:
  * .png / image → extract_document_text runs OCR and returns its text
  * image-only .pdf (empty fitz text) → falls back to OCR
  * an image document is indexed and becomes searchable (headline test —
    exercises the running-event-loop branch of the _run_sync bridge via the
    lazy search → index_project path)
  * low-quality OCR flag propagates to the index entry and to search results
  * OCR failure is graceful — returns "", never raises, indexing still works
"""

import importlib
import json
import os

import pytest

from app.core import file_crypto
from app.core import projects as projects_mod


# ────────────────────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────────────────────

def _make_ocr_stub(result):
    """Build an async stand-in for OCRBlock.process returning ``result``."""
    async def _fake_process(self, input_data, params=None):
        return result
    return _fake_process


def _make_ocr_raiser():
    async def _fake_process(self, input_data, params=None):
        raise RuntimeError("tesseract exploded")
    return _fake_process


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    """Isolated DATA_DIR + fresh projects DB for each test."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.delenv("DATA_ENCRYPTION_KEY", raising=False)
    monkeypatch.setattr(projects_mod, "_initialized", False)
    projects_mod.init_db()
    return tmp_path


# ────────────────────────────────────────────────────────────────────────────
# Task 1 — extract_document_text runs OCR for images
# ────────────────────────────────────────────────────────────────────────────

def test_extract_image_runs_ocr(tmp_path, monkeypatch):
    """A .png document is OCR'd; extract_document_text returns the OCR text."""
    monkeypatch.delenv("DATA_ENCRYPTION_KEY", raising=False)
    from app.blocks.ocr import OCRBlock
    from app.core import doc_index
    importlib.reload(doc_index)

    monkeypatch.setattr(OCRBlock, "process", _make_ocr_stub({
        "status": "success",
        "text": "CONCRETE POUR SCHEDULE rev B",
        "confidence": 0.9,
        "quality": {"low_quality": False},
    }))

    img_path = str(tmp_path / "drawing.png")
    file_crypto.write_document(img_path, b"\x89PNG\r\n\x1a\n fake image bytes")

    text = doc_index.extract_document_text(img_path, "drawing.png")
    assert text == "CONCRETE POUR SCHEDULE rev B"


def test_extract_scanned_pdf_falls_back_to_ocr(tmp_path, monkeypatch):
    """An image-only PDF (empty fitz text) falls back to OCR."""
    monkeypatch.delenv("DATA_ENCRYPTION_KEY", raising=False)
    from app.blocks.ocr import OCRBlock
    from app.core import doc_index
    importlib.reload(doc_index)

    # fitz yields effectively empty text → triggers OCR fallback
    class FakePage:
        def get_text(self):
            return "   \n  "

    class FakeDoc:
        def __iter__(self):
            return iter([FakePage()])

        def close(self):
            pass

    import fitz as real_fitz
    monkeypatch.setattr(real_fitz, "open", lambda path: FakeDoc())

    monkeypatch.setattr(OCRBlock, "process", _make_ocr_stub({
        "status": "success",
        "text": "SCANNED SITE PLAN extracted via OCR fallback",
        "quality": {"low_quality": False},
    }))

    pdf_path = str(tmp_path / "scan.pdf")
    file_crypto.write_document(pdf_path, b"%PDF-1.4 image-only")

    text = doc_index.extract_document_text(pdf_path, "scan.pdf")
    assert "SCANNED SITE PLAN" in text


def test_extract_text_pdf_does_not_ocr(tmp_path, monkeypatch):
    """A PDF with a real text layer keeps the fitz text — no OCR fallback."""
    monkeypatch.delenv("DATA_ENCRYPTION_KEY", raising=False)
    from app.blocks.ocr import OCRBlock
    from app.core import doc_index
    importlib.reload(doc_index)

    class FakePage:
        def get_text(self):
            return "This PDF has a genuine, sufficiently long text layer. "

    class FakeDoc:
        def __iter__(self):
            return iter([FakePage(), FakePage()])

        def close(self):
            pass

    import fitz as real_fitz
    monkeypatch.setattr(real_fitz, "open", lambda path: FakeDoc())

    # If OCR were (wrongly) invoked, it would return this sentinel.
    monkeypatch.setattr(OCRBlock, "process", _make_ocr_stub({
        "status": "success", "text": "OCR-SENTINEL-SHOULD-NOT-APPEAR",
        "quality": {"low_quality": False},
    }))

    pdf_path = str(tmp_path / "textlayer.pdf")
    file_crypto.write_document(pdf_path, b"%PDF-1.4 text layer")

    text = doc_index.extract_document_text(pdf_path, "textlayer.pdf")
    assert "genuine, sufficiently long text layer" in text
    assert "OCR-SENTINEL" not in text


# ────────────────────────────────────────────────────────────────────────────
# Task 3 — image documents are indexed and searchable
# ────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_image_document_is_indexed_and_searchable(fresh_db, tmp_path, monkeypatch):
    """Headline test: a scanned .png becomes searchable.

    Search lazily triggers index_project → extract_document_text → OCR,
    so this also exercises the running-event-loop branch of _run_sync.
    """
    from app.blocks.ocr import OCRBlock
    from app.core import doc_index
    importlib.reload(doc_index)

    monkeypatch.setattr(OCRBlock, "process", _make_ocr_stub({
        "status": "success",
        "text": ("Concrete pour and curing schedule for the foundation slab. "
                 "The contractor must verify compressive strength before "
                 "formwork removal. Curing time is 28 days."),
        "quality": {"low_quality": False},
    }))

    proj = projects_mod.create_project("OCR Search Project")
    pid = proj["id"]

    img_path = str(tmp_path / "scanned_drawing.png")
    file_crypto.write_document(img_path, b"\x89PNG\r\n\x1a\n scanned drawing")
    doc = projects_mod.add_document(pid, "scanned_drawing.png",
                                    file_path=img_path, size=20)

    # No index yet — search must lazily build it (running event loop active).
    assert not os.path.exists(doc_index._index_path(pid))

    results = await doc_index.search_project_documents(pid, "concrete curing schedule")

    returned_ids = [r["document_id"] for r in results]
    assert doc["id"] in returned_ids


def test_image_document_indexed_not_skipped(fresh_db, tmp_path, monkeypatch):
    """index_project treats images as supported — they go to documents, not skipped."""
    from app.blocks.ocr import OCRBlock
    from app.core import doc_index
    importlib.reload(doc_index)

    monkeypatch.setattr(OCRBlock, "process", _make_ocr_stub({
        "status": "success",
        "text": "REBAR LAYOUT DETAIL sheet S-201",
        "quality": {"low_quality": False},
    }))

    proj = projects_mod.create_project("Image Index Project")
    pid = proj["id"]

    img_path = str(tmp_path / "rebar.jpg")
    file_crypto.write_document(img_path, b"\xff\xd8 jpeg")
    doc = projects_mod.add_document(pid, "rebar.jpg", file_path=img_path, size=8)

    result = doc_index.index_project(pid)

    assert result["indexed"] == 1
    assert result["skipped_unsupported"] == 0

    saved = json.load(open(doc_index._index_path(pid)))
    assert [d["document_id"] for d in saved["documents"]] == [doc["id"]]
    assert saved["skipped"] == []
    assert any("REBAR LAYOUT" in c for c in saved["documents"][0]["chunks"])


# ────────────────────────────────────────────────────────────────────────────
# Task 4 — low-quality OCR flag propagates
# ────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_low_quality_ocr_flag_propagates(fresh_db, tmp_path, monkeypatch):
    """A low-quality OCR result flags the index entry and the search hit."""
    from app.blocks.ocr import OCRBlock
    from app.core import doc_index
    importlib.reload(doc_index)

    monkeypatch.setattr(OCRBlock, "process", _make_ocr_stub({
        "status": "success",
        "text": "blurry steel beam connection detail drawing scan",
        "quality": {"low_quality": True, "caveat": "Low OCR confidence"},
    }))

    proj = projects_mod.create_project("Low Quality OCR Project")
    pid = proj["id"]

    img_path = str(tmp_path / "blurry.png")
    file_crypto.write_document(img_path, b"\x89PNG\r\n\x1a\n blurry")
    doc = projects_mod.add_document(pid, "blurry.png", file_path=img_path, size=8)

    doc_index.index_project(pid)

    saved = json.load(open(doc_index._index_path(pid)))
    entry = saved["documents"][0]
    assert entry["ocr_low_quality"] is True

    results = await doc_index.search_project_documents(pid, "steel beam connection")
    hit = next(r for r in results if r["document_id"] == doc["id"])
    assert hit["ocr_low_quality"] is True


@pytest.mark.asyncio
async def test_good_quality_ocr_has_no_low_quality_flag(fresh_db, tmp_path, monkeypatch):
    """A good OCR result does NOT carry ocr_low_quality: true."""
    from app.blocks.ocr import OCRBlock
    from app.core import doc_index
    importlib.reload(doc_index)

    monkeypatch.setattr(OCRBlock, "process", _make_ocr_stub({
        "status": "success",
        "text": "crisp clear architectural elevation drawing east facade",
        "quality": {"low_quality": False},
    }))

    proj = projects_mod.create_project("Good Quality OCR Project")
    pid = proj["id"]

    img_path = str(tmp_path / "crisp.png")
    file_crypto.write_document(img_path, b"\x89PNG\r\n\x1a\n crisp")
    doc = projects_mod.add_document(pid, "crisp.png", file_path=img_path, size=8)

    doc_index.index_project(pid)
    saved = json.load(open(doc_index._index_path(pid)))
    assert not saved["documents"][0].get("ocr_low_quality", False)

    results = await doc_index.search_project_documents(pid, "architectural elevation east facade")
    hit = next(r for r in results if r["document_id"] == doc["id"])
    assert hit.get("ocr_low_quality", False) is False


# ────────────────────────────────────────────────────────────────────────────
# Task 1/2 — OCR failure is graceful
# ────────────────────────────────────────────────────────────────────────────

def test_ocr_failure_is_graceful(fresh_db, tmp_path, monkeypatch):
    """OCR raising → extract_document_text returns '', no exception."""
    from app.blocks.ocr import OCRBlock
    from app.core import doc_index
    importlib.reload(doc_index)

    monkeypatch.setattr(OCRBlock, "process", _make_ocr_raiser())

    img_path = str(tmp_path / "bad.png")
    file_crypto.write_document(img_path, b"\x89PNG\r\n\x1a\n bad")

    text = doc_index.extract_document_text(img_path, "bad.png")
    assert text == ""


def test_ocr_error_status_is_graceful(fresh_db, tmp_path, monkeypatch):
    """OCR returning status=error → extract returns '', indexing still succeeds."""
    from app.blocks.ocr import OCRBlock
    from app.core import doc_index
    importlib.reload(doc_index)

    monkeypatch.setattr(OCRBlock, "process", _make_ocr_stub({
        "status": "error", "text": "", "confidence": 0,
        "error": "Tesseract not installed",
    }))

    proj = projects_mod.create_project("OCR Error Project")
    pid = proj["id"]

    img_path = str(tmp_path / "fail.png")
    file_crypto.write_document(img_path, b"\x89PNG\r\n\x1a\n fail")
    doc = projects_mod.add_document(pid, "fail.png", file_path=img_path, size=8)

    text = doc_index.extract_document_text(img_path, "fail.png")
    assert text == ""

    # Indexing still succeeds — the doc indexes with zero chunks, not skipped.
    result = doc_index.index_project(pid)
    assert result["skipped_unsupported"] == 0
    saved = json.load(open(doc_index._index_path(pid)))
    assert [d["document_id"] for d in saved["documents"]] == [doc["id"]]
    assert saved["documents"][0]["chunks"] == []


# ────────────────────────────────────────────────────────────────────────────
# Bridge — the sync (no running loop) path
# ────────────────────────────────────────────────────────────────────────────

def test_run_sync_works_with_no_running_loop(tmp_path, monkeypatch):
    """extract_document_text called from plain sync context (no event loop)
    successfully runs the async OCR block."""
    monkeypatch.delenv("DATA_ENCRYPTION_KEY", raising=False)
    from app.blocks.ocr import OCRBlock
    from app.core import doc_index
    importlib.reload(doc_index)

    monkeypatch.setattr(OCRBlock, "process", _make_ocr_stub({
        "status": "success", "text": "sync-context OCR text",
        "quality": {"low_quality": False},
    }))

    img_path = str(tmp_path / "sync.png")
    file_crypto.write_document(img_path, b"\x89PNG\r\n\x1a\n sync")

    # Plain sync call — no event loop running.
    text = doc_index.extract_document_text(img_path, "sync.png")
    assert text == "sync-context OCR text"
