"""The RAG takes text formats only, at every door (docs/INGEST_EXCLUSION_RULE.md).

docs/RAG_GAPS_REVIEW_2026-09-12.md section E: CAD, images, Google Earth,
video, GIS internals and fonts are excluded BY DESIGN. Live 2026-10-04 a
Drive ingest downloaded and registered 8 videos, hundreds of drawings and
photos anyway. These tests pin the rule where files enter:
  * one declaration (ingest_status.TEXT_BEARING_EXTS) the indexer derives from;
  * archive members that are not text are never read;
  * a project upload / Drive import of an excluded format is refused (415)
    and writes nothing;
  * a chat photo is still accepted as question context and creates no
    document and no chunks.
The Drive ingest discovery filter is covered in test_p1b_ingest_accounting.
"""
from __future__ import annotations

import io
import zipfile

import pytest
from fastapi.testclient import TestClient

from app.core import ingest_status as ist
from app.main import app

H = {"Authorization": "Bearer cb_dev_key"}


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def _new_project(client, name="Text Only"):
    r = client.post("/v1/projects", json={"name": name, "client": "ACME"}, headers=H)
    assert r.status_code == 201, r.text
    return r.json()


def _doc_count(client, pid):
    r = client.get(f"/v1/projects/{pid}/documents", headers=H)
    assert r.status_code == 200, r.text
    body = r.json()
    return len(body.get("documents", body) if isinstance(body, dict) else body)


# ── one declaration ────────────────────────────────────────────────────────


def test_the_indexer_derives_its_formats_from_the_single_declaration():
    from app.core import doc_index
    from app.routers import projects

    # Equality, not identity: other tests reload these modules.
    assert set(doc_index._SUPPORTED_EXTS) == set(ist.TEXT_BEARING_EXTS)
    assert set(projects.ALLOWED_DOC_EXTENSIONS) == set(ist.TEXT_BEARING_EXTS)


@pytest.mark.parametrize("name", ["drone.mp4", "plan.dwg", "site.jpg", "earth.kmz", "font.ttf"])
def test_excluded_formats_are_not_ingestible(name):
    assert not ist.is_ingestible(name)


@pytest.mark.parametrize("name", ["contract.pdf", "boq.xlsx", "notes.txt", "pack.zip", "Spec.DOCX"])
def test_text_formats_are_ingestible(name):
    assert ist.is_ingestible(name)


# ── archives ───────────────────────────────────────────────────────────────


def test_zip_with_mixed_members_reads_only_the_text_members(tmp_path, monkeypatch):
    from app.core import doc_index, file_crypto

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("spec.txt", "Retaining wall drainage clause seven.")
        z.writestr("drawing.dwg", b"AC1032" + b"\x00" * 64)
        z.writestr("photo.jpg", b"\xff\xd8\xff" + b"\x00" * 64)
        z.writestr("clip.mp4", b"\x00\x00\x00\x18ftypmp42")
    path = str(tmp_path / "pack.zip")
    file_crypto.write_document(path, buf.getvalue())

    reads: list[str] = []
    real_read = zipfile.ZipFile.read

    def _spy(self, name, *a, **k):
        reads.append(name)
        return real_read(self, name, *a, **k)

    monkeypatch.setattr(zipfile.ZipFile, "read", _spy)
    text = doc_index._extract_archive(path, "pack.zip", doc_index._zip_opener)

    assert "Retaining wall drainage clause seven." in text
    assert reads == ["spec.txt"], reads


# ── upload doors ───────────────────────────────────────────────────────────


@pytest.mark.parametrize("name,mime", [
    ("drone.mp4", "video/mp4"),
    ("plan.dwg", "application/acad"),
    ("site.jpg", "image/jpeg"),
])
def test_project_upload_of_an_excluded_format_is_refused_and_writes_nothing(client, name, mime):
    proj = _new_project(client, f"Refuse {name}")
    before = _doc_count(client, proj["id"])
    r = client.post(
        f"/v1/projects/{proj['id']}/documents",
        files={"file": (name, b"\x00" * 128, mime)}, headers=H,
    )
    assert r.status_code == 415, r.text
    assert "text formats only" in r.json()["detail"]
    assert _doc_count(client, proj["id"]) == before


def test_upload_with_a_project_refuses_an_excluded_format(client):
    proj = _new_project(client, "Refuse via /upload")
    before = _doc_count(client, proj["id"])
    r = client.post(
        "/v1/upload",
        files={"file": ("drone.mp4", b"\x00" * 128, "video/mp4")},
        data={"project_id": proj["id"]}, headers=H,
    )
    assert r.status_code == 415, r.text
    assert "text formats only" in r.json()["detail"]
    assert _doc_count(client, proj["id"]) == before


def test_a_text_upload_to_a_project_still_lands(client):
    proj = _new_project(client, "Text lands")
    r = client.post(
        f"/v1/projects/{proj['id']}/documents",
        files={"file": ("notes.txt", b"Concrete cover 50 mm to footings.", "text/plain")},
        headers=H,
    )
    assert r.status_code in (200, 201), r.text


# ── chat photos stay question context ──────────────────────────────────────


def test_chat_photo_is_accepted_as_context_and_creates_no_document(client, monkeypatch):
    from app import dependencies

    class _Image:
        async def execute(self, inputs, params):
            return {"result": {"safety_qaqc": [], "summary_by_class": {}}}

    monkeypatch.setattr(dependencies, "get_block_instance", lambda name: _Image())

    from app.core.db import SessionLocal
    from app.core.models import Document

    with SessionLocal() as s:
        docs_before = s.query(Document).count()
    r = client.post(
        "/v1/chat/analyze-photo",
        files={"file": ("site.jpg", b"\xff\xd8\xff" + b"\x00" * 256, "image/jpeg")},
        headers=H,
    )
    assert r.status_code == 200, r.text
    with SessionLocal() as s:
        assert s.query(Document).count() == docs_before


# ── deleting a document removes everything it left behind ──────────────────


def test_purge_document_removes_the_stored_file_the_row_and_the_index_entry(tmp_path, monkeypatch):
    import importlib

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.delenv("DATA_ENCRYPTION_KEY", raising=False)
    from app.core import projects as projects_mod

    monkeypatch.setattr(projects_mod, "_initialized", False)
    projects_mod.init_db()
    from app.core import doc_index, file_crypto

    importlib.reload(doc_index)
    pid = projects_mod.create_project("Purge")["id"]
    path = str(tmp_path / "notes.txt")
    file_crypto.write_document(path, b"Concrete cover 50 mm to footings. " * 20)
    doc = projects_mod.add_document(pid, "notes.txt", file_path=path, size=600)
    doc_index._upsert_index_entry(pid, doc["id"], "document",
                                  {"document_id": doc["id"], "filename": "notes.txt", "chunk_count": 1})

    out = projects_mod.purge_document(doc["id"])

    assert out == {"deleted": True, "file_removed": True, "index_pruned": True}
    assert not (tmp_path / "notes.txt").exists()
    assert projects_mod.get_document(doc["id"]) is None
    assert (doc_index._load_index(pid) or {}).get("documents", []) == []
