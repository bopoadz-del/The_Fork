"""Tests for app/core/doc_index.py — document text extraction, chunking,
and per-project index persistence.

TDD order:
  Wave 1 — Task 1: text extraction
  Wave 2 — Task 2: chunking
  Wave 3 — Task 3: index build + JSON persistence
"""

import importlib
import os
import sys
import threading

import pytest
from cryptography.fernet import Fernet

from app.core import file_crypto
from app.core import projects as projects_mod


# ────────────────────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────────────────────

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")
SAMPLE_TXT = os.path.join(FIXTURES_DIR, "sample.txt")


# ────────────────────────────────────────────────────────────────────────────
# WAVE 1 — Task 1: extract_document_text
# ────────────────────────────────────────────────────────────────────────────

def test_extract_txt(tmp_path, monkeypatch):
    """Plain-text fixture round-trips; known phrase appears in result."""
    monkeypatch.delenv("DATA_ENCRYPTION_KEY", raising=False)
    from app.core.doc_index import extract_document_text

    # Copy sample.txt to a tmp location so we have a real writable path
    content = open(SAMPLE_TXT, "rb").read()
    doc_path = str(tmp_path / "sample.txt")
    file_crypto.write_document(doc_path, content)

    text = extract_document_text(doc_path, "sample.txt")
    assert "Kingsbridge Tower" in text
    assert "Sandra Okafor" in text


def test_extract_pdf_mocked(tmp_path, monkeypatch):
    """PDF extraction: monkeypatch fitz.open; assert concatenated page text."""
    monkeypatch.delenv("DATA_ENCRYPTION_KEY", raising=False)

    # Write a dummy file so open_plaintext has something to open
    doc_path = str(tmp_path / "report.pdf")
    file_crypto.write_document(doc_path, b"%PDF-1.4 dummy")

    # Build fake fitz objects
    class FakePage:
        def get_text(self):
            return "Page text alpha. "

    class FakeDoc:
        def __iter__(self):
            return iter([FakePage(), FakePage()])

        def close(self):
            pass

    import fitz as real_fitz
    monkeypatch.setattr(real_fitz, "open", lambda path: FakeDoc())

    from app.core import doc_index
    importlib.reload(doc_index)
    text = doc_index.extract_document_text(doc_path, "report.pdf")
    assert "Page text alpha." in text
    assert text.count("Page text alpha.") == 2


def test_extract_docx_mocked(tmp_path, monkeypatch):
    """DOCX extraction: monkeypatch docx.Document; assert joined paragraph text."""
    monkeypatch.delenv("DATA_ENCRYPTION_KEY", raising=False)

    doc_path = str(tmp_path / "contract.docx")
    file_crypto.write_document(doc_path, b"PK\x03\x04 fake docx bytes")

    class FakePara:
        def __init__(self, txt):
            self.text = txt

    class FakeDocxDoc:
        paragraphs = [FakePara("Clause one text."), FakePara("Clause two text.")]

    import docx as real_docx
    monkeypatch.setattr(real_docx, "Document", lambda path: FakeDocxDoc())

    from app.core import doc_index
    importlib.reload(doc_index)
    text = doc_index.extract_document_text(doc_path, "contract.docx")
    assert "Clause one text." in text
    assert "Clause two text." in text


def test_extract_xlsx_mocked(tmp_path, monkeypatch):
    """XLSX extraction: monkeypatch openpyxl.load_workbook; assert cell text."""
    monkeypatch.delenv("DATA_ENCRYPTION_KEY", raising=False)

    doc_path = str(tmp_path / "budget.xlsx")
    file_crypto.write_document(doc_path, b"PK\x03\x04 fake xlsx bytes")

    class FakeCell:
        def __init__(self, val):
            self.value = val

    class FakeSheet:
        def __iter__(self):
            return iter([
                [FakeCell("Activity"), FakeCell("Cost"), FakeCell(None)],
                [FakeCell("Excavation"), FakeCell(15000), FakeCell("")],
            ])

    class FakeWorkbook:
        sheetnames = ["Sheet1"]

        def __getitem__(self, name):
            return FakeSheet()

    import openpyxl as real_openpyxl
    monkeypatch.setattr(
        real_openpyxl, "load_workbook",
        lambda path, data_only=True: FakeWorkbook()
    )

    from app.core import doc_index
    importlib.reload(doc_index)
    text = doc_index.extract_document_text(doc_path, "budget.xlsx")
    assert "Activity" in text
    assert "Cost" in text
    assert "Excavation" in text


def test_extract_unsupported_returns_empty(tmp_path, monkeypatch):
    """Unsupported extension returns empty string, never raises."""
    monkeypatch.delenv("DATA_ENCRYPTION_KEY", raising=False)
    from app.core.doc_index import extract_document_text

    doc_path = str(tmp_path / "photo.jpg")
    file_crypto.write_document(doc_path, b"\xff\xd8 JPEG data")

    result = extract_document_text(doc_path, "photo.jpg")
    assert result == ""


def test_extract_encrypted_txt(tmp_path, monkeypatch):
    """Encrypted .txt file is transparently decrypted; plaintext is returned."""
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("DATA_ENCRYPTION_KEY", key)

    doc_path = str(tmp_path / "secret.txt")
    plaintext = b"Confidential: the bridge budget is forty million dirhams."
    file_crypto.write_document(doc_path, plaintext)

    # Confirm it is actually encrypted on disk
    on_disk = open(doc_path, "rb").read()
    assert on_disk != plaintext

    from app.core import doc_index
    importlib.reload(doc_index)
    text = doc_index.extract_document_text(doc_path, "secret.txt")
    assert "forty million dirhams" in text


def test_extract_missing_file_returns_empty(monkeypatch):
    """Missing file path returns '' without raising."""
    monkeypatch.delenv("DATA_ENCRYPTION_KEY", raising=False)
    from app.core.doc_index import extract_document_text

    result = extract_document_text("/nonexistent/path/doc.txt", "doc.txt")
    assert result == ""


# ────────────────────────────────────────────────────────────────────────────
# WAVE 2 — Task 2: chunk_text
# ────────────────────────────────────────────────────────────────────────────

def _make_words(n: int) -> str:
    return " ".join(f"word{i}" for i in range(n))


def test_chunk_1200_words_gives_three_chunks():
    from app.core.doc_index import chunk_text
    text = _make_words(1200)
    chunks = chunk_text(text, words_per_chunk=500)
    assert len(chunks) == 3
    for ch in chunks:
        assert len(ch.split()) <= 500


def test_chunk_20_words_gives_one_chunk():
    from app.core.doc_index import chunk_text
    text = _make_words(20)
    chunks = chunk_text(text)
    assert len(chunks) == 1
    assert len(chunks[0].split()) == 20


def test_chunk_empty_string_gives_empty_list():
    from app.core.doc_index import chunk_text
    assert chunk_text("") == []


def test_chunk_whitespace_only_gives_empty_list():
    from app.core.doc_index import chunk_text
    assert chunk_text("   \n\t  ") == []


# ────────────────────────────────────────────────────────────────────────────
# WAVE 3 — Task 3: index build + JSON persistence
# ────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    """Isolated DATA_DIR + fresh projects DB for each test."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setattr(projects_mod, "_initialized", False)
    projects_mod.init_db()
    return tmp_path


def _write_txt_doc(tmp_path, filename, content_bytes):
    """Write a plaintext doc file under tmp_path, return its path."""
    p = str(tmp_path / filename)
    file_crypto.write_document(p, content_bytes)
    return p


def test_index_project_writes_file_and_returns_summary(fresh_db, tmp_path, monkeypatch):
    """index_project indexes supported docs and writes the JSON index file."""
    monkeypatch.delenv("DATA_ENCRYPTION_KEY", raising=False)
    from app.core import doc_index
    importlib.reload(doc_index)

    proj = projects_mod.create_project("Alpha Project")
    pid = proj["id"]

    content = b"The structural survey confirmed no subsidence was detected."
    doc_path = _write_txt_doc(tmp_path, "report.txt", content)
    projects_mod.add_document(pid, "report.txt", file_path=doc_path, size=len(content))

    result = doc_index.index_project(pid)

    assert result["project_id"] == pid
    assert result["indexed"] == 1
    assert result["skipped_unsupported"] == 0
    assert result["total_chunks"] >= 1

    # JSON file on disk
    index_path = doc_index._index_path(pid)
    assert os.path.exists(index_path)

    import json
    with open(index_path) as f:
        saved = json.load(f)

    assert saved["project_id"] == pid
    assert len(saved["documents"]) == 1
    assert "subsidence" in saved["documents"][0]["chunks"][0]


def test_index_project_skips_unsupported_type(fresh_db, tmp_path, monkeypatch):
    """A .jpg document lands in 'skipped', not 'documents'."""
    monkeypatch.delenv("DATA_ENCRYPTION_KEY", raising=False)
    from app.core import doc_index
    importlib.reload(doc_index)

    proj = projects_mod.create_project("Beta Project")
    pid = proj["id"]

    img_path = str(tmp_path / "photo.jpg")
    file_crypto.write_document(img_path, b"\xff\xd8 JPEG")
    projects_mod.add_document(pid, "photo.jpg", file_path=img_path, size=6)

    result = doc_index.index_project(pid)

    assert result["indexed"] == 0
    assert result["skipped_unsupported"] == 1

    import json
    saved = json.load(open(doc_index._index_path(pid)))
    assert saved["documents"] == []
    assert len(saved["skipped"]) == 1
    assert saved["skipped"][0]["reason"] == "unsupported_type"


def test_index_project_empty_project(fresh_db, monkeypatch):
    """Empty project produces a valid index file with no documents."""
    monkeypatch.delenv("DATA_ENCRYPTION_KEY", raising=False)
    from app.core import doc_index
    importlib.reload(doc_index)

    proj = projects_mod.create_project("Empty Project")
    pid = proj["id"]

    result = doc_index.index_project(pid)

    assert result["indexed"] == 0
    assert result["total_chunks"] == 0

    import json
    saved = json.load(open(doc_index._index_path(pid)))
    assert saved["documents"] == []
    assert saved["skipped"] == []


def test_index_document_incremental(fresh_db, tmp_path, monkeypatch):
    """index_document adds one doc to an existing (possibly empty) index."""
    monkeypatch.delenv("DATA_ENCRYPTION_KEY", raising=False)
    from app.core import doc_index
    importlib.reload(doc_index)

    proj = projects_mod.create_project("Gamma Project")
    pid = proj["id"]

    content = b"Concrete pour completed on schedule per daily report."
    doc_path = _write_txt_doc(tmp_path, "daily.txt", content)
    doc = projects_mod.add_document(pid, "daily.txt", file_path=doc_path, size=len(content))
    did = doc["id"]

    result = doc_index.index_document(pid, did)

    assert result["indexed"] == 1

    import json
    saved = json.load(open(doc_index._index_path(pid)))
    ids = [d["document_id"] for d in saved["documents"]]
    assert did in ids


def test_invalidate_project_removes_index(fresh_db, tmp_path, monkeypatch):
    """invalidate_project deletes the index file."""
    monkeypatch.delenv("DATA_ENCRYPTION_KEY", raising=False)
    from app.core import doc_index
    importlib.reload(doc_index)

    proj = projects_mod.create_project("Delta Project")
    pid = proj["id"]
    doc_index.index_project(pid)  # creates the file

    assert os.path.exists(doc_index._index_path(pid))
    doc_index.invalidate_project(pid)
    assert not os.path.exists(doc_index._index_path(pid))


def test_load_index_returns_none_when_absent(fresh_db, monkeypatch):
    """_load_index returns None when no index file exists."""
    monkeypatch.delenv("DATA_ENCRYPTION_KEY", raising=False)
    from app.core import doc_index
    importlib.reload(doc_index)

    result = doc_index._load_index("nonexistent-project-id")
    assert result is None


def test_fingerprint_format(fresh_db, tmp_path, monkeypatch):
    """Each indexed document entry has a fingerprint of the form 'uploaded_at:size'."""
    monkeypatch.delenv("DATA_ENCRYPTION_KEY", raising=False)
    from app.core import doc_index
    importlib.reload(doc_index)

    proj = projects_mod.create_project("Fingerprint Project")
    pid = proj["id"]
    content = b"Fingerprint test document content here."
    doc_path = _write_txt_doc(tmp_path, "fp.txt", content)
    doc = projects_mod.add_document(pid, "fp.txt", file_path=doc_path, size=len(content))

    doc_index.index_project(pid)

    import json
    saved = json.load(open(doc_index._index_path(pid)))
    entry = saved["documents"][0]
    expected_fp = f"{doc['uploaded_at']}:{doc['size']}"
    assert entry["fingerprint"] == expected_fp
