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
    """Unsupported extension returns empty string, never raises.

    Stream F: images (.jpg/.png/...) are now SUPPORTED via OCR, so this uses a
    genuinely unsupported extension (.dwg) instead.
    """
    monkeypatch.delenv("DATA_ENCRYPTION_KEY", raising=False)
    from app.core.doc_index import extract_document_text

    doc_path = str(tmp_path / "drawing.dwg")
    file_crypto.write_document(doc_path, b"AutoCAD DWG binary data")

    result = extract_document_text(doc_path, "drawing.dwg")
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
    from app.core.rag import vector_store as _vs
    _vs.reset_store_cache()

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

    # Persisted index
    saved = doc_index._load_index(pid)
    assert saved is not None
    assert saved["project_id"] == pid
    assert len(saved["documents"]) == 1
    assert "subsidence" in saved["documents"][0]["chunks"][0]


def test_index_project_skips_unsupported_type(fresh_db, tmp_path, monkeypatch):
    """A genuinely unsupported document lands in 'skipped', not 'documents'.

    Stream F: images are now OCR-able and SUPPORTED, so this uses .dwg — a
    type that remains unsupported.
    """
    monkeypatch.delenv("DATA_ENCRYPTION_KEY", raising=False)
    from app.core import doc_index
    importlib.reload(doc_index)
    from app.core.rag import vector_store as _vs
    _vs.reset_store_cache()

    proj = projects_mod.create_project("Beta Project")
    pid = proj["id"]

    img_path = str(tmp_path / "model.dwg")
    file_crypto.write_document(img_path, b"AutoCAD DWG")
    projects_mod.add_document(pid, "model.dwg", file_path=img_path, size=6)

    result = doc_index.index_project(pid)

    assert result["indexed"] == 0
    assert result["skipped_unsupported"] == 1

    import json
    saved = doc_index._load_index(pid)
    assert saved["documents"] == []
    assert len(saved["skipped"]) == 1
    assert saved["skipped"][0]["reason"] == "unsupported_type"


def test_index_document_wires_boq_total_into_rag(fresh_db, tmp_path, monkeypatch):
    """A priced BOQ's total + line items become retrievable chunks so the
    platform can answer 'what is the total package value?' from the corpus.
    Wires app/blocks/boq_processor into index_document. The summed total
    (105000) is NOT present in the raw CSV text — only the BOQ wiring emits it.
    """
    monkeypatch.delenv("DATA_ENCRYPTION_KEY", raising=False)
    monkeypatch.setenv("RAG_EMBEDDING_MODEL", "fake")
    from app.core import doc_index
    importlib.reload(doc_index)
    from app.core.rag import vector_store as _vs
    _vs.reset_store_cache()

    proj = projects_mod.create_project("BOQ Project")
    pid = proj["id"]

    csv = (
        b"Description,Quantity,Rate,Amount\n"
        b"Excavation works,100,50,5000\n"
        b"Concrete grade 40,200,300,60000\n"
        b"Reinforcement steel,50,800,40000\n"
    )
    doc_path = _write_txt_doc(tmp_path, "Priced BOQ.csv", csv)
    doc = projects_mod.add_document(pid, "Priced BOQ.csv", file_path=doc_path, size=len(csv))

    doc_index.index_document(pid, doc["id"])

    saved = doc_index._load_index(pid)
    chunks = saved["documents"][0]["chunks"]
    blob = "\n".join(chunks).lower()
    assert "boq total" in blob, f"no BOQ summary chunk; chunks={chunks}"
    # 5000 + 60000 + 40000 = 105000 — appears only via the BOQ wiring.
    assert "105000" in blob or "105,000" in blob, f"total not wired; chunks={chunks}"


def test_index_document_boq_total_hedged_when_pages_skipped(fresh_db, tmp_path, monkeypatch):
    """Accuracy guard: if the BOQ parse skipped pages, the chunk must say the
    total is PARTIAL, never a confident number (no-assumptions rule)."""
    monkeypatch.delenv("DATA_ENCRYPTION_KEY", raising=False)
    monkeypatch.setenv("RAG_EMBEDDING_MODEL", "fake")
    from app.core import doc_index
    importlib.reload(doc_index)
    from app.core.rag import vector_store as _vs
    _vs.reset_store_cache()

    # Force the BOQ processor to report a skipped page.
    import app.blocks.boq_processor as boq
    async def _fake_process(self, input_data, params=None):
        return {
            "status": "success", "item_count": 2, "total_cost": 105000.0,
            "currency": "USD", "line_items": [
                {"description": "Excavation", "total_cost": 5000.0, "section": "Civil"},
                {"description": "Concrete", "total_cost": 100000.0, "section": "Civil"},
            ],
            "cost_breakdown": {"Civil": {"total": 105000.0, "percentage": 100.0}},
            "pages_skipped": 2,
        }
    monkeypatch.setattr(boq.BOQProcessorBlock, "process", _fake_process)

    proj = projects_mod.create_project("Partial BOQ")
    pid = proj["id"]
    csv = b"Description,Amount\nExcavation,5000\n"
    doc_path = _write_txt_doc(tmp_path, "scanned BOQ.pdf", csv)  # .pdf -> boq path
    doc = projects_mod.add_document(pid, "scanned BOQ.pdf", file_path=doc_path, size=len(csv))

    doc_index.index_document(pid, doc["id"])
    saved = doc_index._load_index(pid)
    blob = "\n".join(saved["documents"][0]["chunks"]).lower()
    assert "partial" in blob, f"pages_skipped>0 must hedge; chunks missing 'partial': {blob[:300]}"


def test_index_project_empty_project(fresh_db, monkeypatch):
    """Empty project produces a valid index file with no documents."""
    monkeypatch.delenv("DATA_ENCRYPTION_KEY", raising=False)
    from app.core import doc_index
    importlib.reload(doc_index)
    from app.core.rag import vector_store as _vs
    _vs.reset_store_cache()

    proj = projects_mod.create_project("Empty Project")
    pid = proj["id"]

    result = doc_index.index_project(pid)

    assert result["indexed"] == 0
    assert result["total_chunks"] == 0

    import json
    saved = doc_index._load_index(pid)
    assert saved["documents"] == []
    assert saved["skipped"] == []


def test_index_document_incremental(fresh_db, tmp_path, monkeypatch):
    """index_document adds to an existing index without discarding prior docs."""
    monkeypatch.delenv("DATA_ENCRYPTION_KEY", raising=False)
    from app.core import doc_index
    importlib.reload(doc_index)
    from app.core.rag import vector_store as _vs
    _vs.reset_store_cache()

    proj = projects_mod.create_project("Gamma Project")
    pid = proj["id"]

    # Doc A — indexed via index_project first
    content_a = b"Foundation works are complete as per the structural report."
    doc_path_a = _write_txt_doc(tmp_path, "baseline.txt", content_a)
    doc_a = projects_mod.add_document(pid, "baseline.txt", file_path=doc_path_a, size=len(content_a))
    doc_index.index_project(pid)  # establishes doc A in the index

    # Doc B — added afterwards, indexed incrementally
    content_b = b"Concrete pour completed on schedule per daily report."
    doc_path_b = _write_txt_doc(tmp_path, "daily.txt", content_b)
    doc_b = projects_mod.add_document(pid, "daily.txt", file_path=doc_path_b, size=len(content_b))
    did_b = doc_b["id"]

    result = doc_index.index_document(pid, did_b)

    assert result["indexed"] == 1

    import json
    saved = doc_index._load_index(pid)
    ids = [d["document_id"] for d in saved["documents"]]
    # Both docs must be present — proves load-modify-write preserved doc A
    assert doc_a["id"] in ids
    assert did_b in ids


def test_invalidate_project_removes_index(fresh_db, tmp_path, monkeypatch):
    """invalidate_project deletes the index file."""
    monkeypatch.delenv("DATA_ENCRYPTION_KEY", raising=False)
    from app.core import doc_index
    importlib.reload(doc_index)
    from app.core.rag import vector_store as _vs
    _vs.reset_store_cache()

    proj = projects_mod.create_project("Delta Project")
    pid = proj["id"]
    doc_index.index_project(pid)  # creates the file

    assert doc_index._load_index(pid) is not None
    doc_index.invalidate_project(pid)
    assert doc_index._load_index(pid) is None


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
    from app.core.rag import vector_store as _vs
    _vs.reset_store_cache()

    proj = projects_mod.create_project("Fingerprint Project")
    pid = proj["id"]
    content = b"Fingerprint test document content here."
    doc_path = _write_txt_doc(tmp_path, "fp.txt", content)
    doc = projects_mod.add_document(pid, "fp.txt", file_path=doc_path, size=len(content))

    doc_index.index_project(pid)

    import json
    saved = doc_index._load_index(pid)
    entry = saved["documents"][0]
    expected_fp = f"{doc['uploaded_at']}:{doc['size']}"
    assert entry["fingerprint"] == expected_fp


# ────────────────────────────────────────────────────────────────────────────
# WAVE 4 — Phase C2: search_project_documents
# ────────────────────────────────────────────────────────────────────────────

CONCRETE_TEXT = (
    b"Concrete pour and curing schedule: the contractor must ensure the mix "
    b"reaches the specified compressive strength before formwork is removed. "
    b"The curing time for standard Portland cement concrete is 28 days. "
    b"Water-cement ratio must be controlled. Slump tests are performed on site. "
    b"The concrete curing process requires consistent moisture and temperature."
)

ELECTRICAL_TEXT = (
    b"Electrical wiring and conduit inspection: all cable runs shall be "
    b"installed in rigid conduit per the approved shop drawings. The inspector "
    b"must verify continuity, grounding, and insulation resistance. "
    b"Circuit breaker panels must be clearly labelled. "
    b"Junction boxes require accessible covers for future maintenance."
)

LANDSCAPING_TEXT = (
    b"Landscaping and irrigation layout: the planting plan shows trees, "
    b"shrubs, and ground cover arranged along the building perimeter. "
    b"Drip irrigation lines are routed to each planted zone. "
    b"Soil preparation includes aeration and compost amendment. "
    b"Irrigation controllers are programmed for seasonal watering schedules."
)


@pytest.fixture
def search_project(tmp_path, monkeypatch):
    """Shared fixture: isolated DATA_DIR, fresh DB, project with 3 disjoint docs."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.delenv("DATA_ENCRYPTION_KEY", raising=False)
    monkeypatch.setattr(projects_mod, "_initialized", False)
    projects_mod.init_db()

    from app.core import doc_index
    importlib.reload(doc_index)

    # PR #94: search_project_documents now uses the hybrid retriever which
    # caches its VectorStore per (db_url, dim) at module scope. Tests swap
    # DATA_DIR per-test, so the cache must be reset to repoint the store
    # at the new SQLite file, otherwise queries hit the previous test's DB.
    from app.core.rag import vector_store as _vs
    _vs.reset_store_cache()

    proj = projects_mod.create_project("Search Test Project")
    pid = proj["id"]

    def _add(filename, content):
        p = str(tmp_path / filename)
        file_crypto.write_document(p, content)
        return projects_mod.add_document(pid, filename, file_path=p, size=len(content))

    doc_concrete = _add("concrete.txt", CONCRETE_TEXT)
    doc_elec = _add("electrical.txt", ELECTRICAL_TEXT)
    doc_landscape = _add("landscaping.txt", LANDSCAPING_TEXT)

    return {
        "pid": pid,
        "doc_concrete": doc_concrete,
        "doc_elec": doc_elec,
        "doc_landscape": doc_landscape,
        "tmp_path": tmp_path,
        "doc_index": doc_index,
    }


@pytest.mark.asyncio
async def test_search_ranks_relevant_doc_first(search_project):
    """Query 'concrete curing time' should rank the concrete document first."""
    doc_index = search_project["doc_index"]
    pid = search_project["pid"]
    concrete_id = search_project["doc_concrete"]["id"]

    results = await doc_index.search_project_documents(pid, "concrete curing time")

    assert len(results) >= 1
    assert results[0]["document_id"] == concrete_id


@pytest.mark.asyncio
async def test_search_returns_shape(search_project):
    """Each result has document_id, filename, snippet, score; score is a float."""
    doc_index = search_project["doc_index"]
    pid = search_project["pid"]

    results = await doc_index.search_project_documents(pid, "concrete curing time")

    assert len(results) >= 1
    for r in results:
        assert "document_id" in r
        assert "filename" in r
        assert "snippet" in r
        assert "score" in r
        assert isinstance(r["score"], float)


@pytest.mark.asyncio
async def test_search_top_k(search_project):
    """top_k=1 returns exactly 1 result."""
    doc_index = search_project["doc_index"]
    pid = search_project["pid"]

    results = await doc_index.search_project_documents(pid, "concrete curing time", top_k=1)

    assert len(results) == 1


@pytest.mark.asyncio
async def test_search_empty_project(tmp_path, monkeypatch):
    """A project with no documents returns [] without raising."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.delenv("DATA_ENCRYPTION_KEY", raising=False)
    monkeypatch.setattr(projects_mod, "_initialized", False)
    projects_mod.init_db()

    from app.core import doc_index
    importlib.reload(doc_index)
    from app.core.rag import vector_store as _vs
    _vs.reset_store_cache()

    proj = projects_mod.create_project("Empty Search Project")
    pid = proj["id"]

    results = await doc_index.search_project_documents(pid, "anything")
    assert results == []


@pytest.mark.asyncio
async def test_search_builds_index_lazily(tmp_path, monkeypatch):
    """Index file must not exist before search; must exist afterwards."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.delenv("DATA_ENCRYPTION_KEY", raising=False)
    monkeypatch.setattr(projects_mod, "_initialized", False)
    projects_mod.init_db()

    from app.core import doc_index
    importlib.reload(doc_index)
    from app.core.rag import vector_store as _vs
    _vs.reset_store_cache()

    proj = projects_mod.create_project("Lazy Build Project")
    pid = proj["id"]

    p = str(tmp_path / "lazy.txt")
    file_crypto.write_document(p, b"Lazy build test content about concrete pouring.")
    projects_mod.add_document(pid, "lazy.txt", file_path=p, size=47)

    # Index must not yet exist
    assert doc_index._load_index(pid) is None

    results = await doc_index.search_project_documents(pid, "concrete")

    # After search, index exists and results are returned
    assert doc_index._load_index(pid) is not None
    assert isinstance(results, list)


@pytest.mark.asyncio
async def test_search_uses_hybrid_retriever(search_project):
    """PR #94: search_project_documents must query the same chunks table
    the RAG injection layer queries. Verify by seeding the chunks table
    directly and confirming the function returns results without going
    through the legacy JSON index path.
    """
    doc_index = search_project["doc_index"]
    pid = search_project["pid"]
    concrete_id = search_project["doc_concrete"]["id"]

    # Force a search — this triggers the bootstrap which writes to the
    # chunks table. Then delete the legacy JSON index file to prove the
    # next call goes through the hybrid retriever (which reads the
    # chunks table, not the JSON blob).
    results1 = await doc_index.search_project_documents(pid, "concrete curing")
    assert len(results1) >= 1
    assert results1[0]["document_id"] == concrete_id

    # Wipe the legacy JSON index row but leave the chunks table populated.
    from app.core.db import SessionLocal
    from app.core.models import DocIndex
    from sqlalchemy import delete as _sql_delete
    with SessionLocal() as s:
        s.execute(_sql_delete(DocIndex).where(DocIndex.project_id == pid))
        s.commit()
    assert doc_index._load_index(pid) is None

    # Second call: bootstrap would re-fire (since JSON is gone), but the
    # retriever should still find the chunks already in the chunks
    # table. The contract here: results come from the chunks table,
    # not the JSON blob.
    results2 = await doc_index.search_project_documents(pid, "concrete curing")
    assert len(results2) >= 1
    assert results2[0]["document_id"] == concrete_id


@pytest.mark.asyncio
async def test_search_excludes_deleted_document(tmp_path, monkeypatch):
    """After deleting a document, its content must not appear in search results."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.delenv("DATA_ENCRYPTION_KEY", raising=False)
    monkeypatch.setattr(projects_mod, "_initialized", False)
    projects_mod.init_db()

    from app.core import doc_index
    importlib.reload(doc_index)
    from app.core.rag import vector_store as _vs
    _vs.reset_store_cache()

    proj = projects_mod.create_project("Delete Test Project")
    pid = proj["id"]

    p1 = str(tmp_path / "concrete2.txt")
    file_crypto.write_document(p1, CONCRETE_TEXT)
    doc_keep = projects_mod.add_document(pid, "concrete2.txt", file_path=p1, size=len(CONCRETE_TEXT))

    p2 = str(tmp_path / "electrical2.txt")
    file_crypto.write_document(p2, ELECTRICAL_TEXT)
    doc_del = projects_mod.add_document(pid, "electrical2.txt", file_path=p2, size=len(ELECTRICAL_TEXT))

    # Build the index by searching once
    await doc_index.search_project_documents(pid, "concrete")

    # Delete the electrical document from the DB
    projects_mod.delete_document(doc_del["id"])

    # Search again — deleted doc must not appear in results
    results = await doc_index.search_project_documents(pid, "electrical wiring conduit")
    returned_ids = [r["document_id"] for r in results]
    assert doc_del["id"] not in returned_ids
