"""Tests for the inline document-preview endpoint.

Covers the render-friendly JSON shapes returned by
``GET /v1/projects/{pid}/documents/{did}/preview`` — an xlsx workbook renders
as a table, an unpreviewable-but-allowed extension reports 'unsupported', and a
malformed spreadsheet returns 422 (never 500).
"""

import io
import json
import os

import openpyxl
import pytest
from fastapi.testclient import TestClient

from app.main import app

H = {"Authorization": "Bearer cb_dev_key"}


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def _new_project(client, name="Preview Project"):
    r = client.post("/v1/projects", json={"name": name}, headers=H)
    assert r.status_code == 201, r.text
    return r.json()


def _upload(client, pid, filename, content, content_type):
    files = {"file": (filename, content, content_type)}
    r = client.post(f"/v1/projects/{pid}/documents", files=files, headers=H)
    assert r.status_code == 201, r.text
    return r.json()["document"]


def _xlsx_bytes() -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "BOQ"
    ws.append(["Item", "Qty", "Rate"])
    ws.append(["Concrete", 100, 350])
    ws.append(["Rebar", 50, 4200])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_xlsx_previews_as_table(client):
    proj = _new_project(client)
    doc = _upload(
        client, proj["id"], "boq.xlsx", _xlsx_bytes(),
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    r = client.get(
        f"/v1/projects/{proj['id']}/documents/{doc['id']}/preview", headers=H
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["kind"] == "table"
    assert len(body["sheets"]) == 1
    sheet = body["sheets"][0]
    assert sheet["name"] == "BOQ"
    assert sheet["rows"][0] == ["Item", "Qty", "Rate"]
    assert sheet["rows"][1] == ["Concrete", "100", "350"]


def test_unsupported_extension_reports_unsupported(client):
    proj = _new_project(client)
    # .ifc is accepted by the upload route but has no preview renderer.
    doc = _upload(
        client, proj["id"], "model.ifc", b"ISO-10303-21;\nHEADER;",
        "application/octet-stream",
    )
    r = client.get(
        f"/v1/projects/{proj['id']}/documents/{doc['id']}/preview", headers=H
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["kind"] == "unsupported"
    assert body["ext"] == ".ifc"


def test_malformed_spreadsheet_returns_422(client):
    proj = _new_project(client)
    # A .xlsx extension over non-workbook bytes must 422, not 500.
    doc = _upload(
        client, proj["id"], "broken.xlsx", b"not a real spreadsheet",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    r = client.get(
        f"/v1/projects/{proj['id']}/documents/{doc['id']}/preview", headers=H
    )
    assert r.status_code == 422, r.text


def test_txt_previews_as_text(client):
    proj = _new_project(client)
    doc = _upload(
        client, proj["id"], "notes.txt", b"Line one\nLine two", "text/plain",
    )
    r = client.get(
        f"/v1/projects/{proj['id']}/documents/{doc['id']}/preview", headers=H
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["kind"] == "text"
    assert "Line one" in body["text"]


def test_preview_missing_document_404(client):
    proj = _new_project(client)
    r = client.get(
        f"/v1/projects/{proj['id']}/documents/nope1234/preview", headers=H
    )
    assert r.status_code == 404


def test_preview_cited_corpus_doc_from_workspace(client, monkeypatch):
    """Opening a Sources cite fetches preview via the *workspace* project
    id plus the cited doc id. That doc often lives on Master Corpus / GK,
    not on the workspace — the fetch must still succeed."""
    workspace = _new_project(client, "Preview Workspace")
    corpus = _new_project(client, "Citeable Corpus")
    doc = _upload(
        client, corpus["id"], "cited_spec.txt",
        b"Clause 8.7 Delay Damages are 0.1 percent per day.",
        "text/plain",
    )
    monkeypatch.setattr(
        "app.routers.projects._preview_citeable_owner_ids",
        lambda pid: {pid, corpus["id"]},
    )
    r = client.get(
        f"/v1/projects/{workspace['id']}/documents/{doc['id']}/preview",
        headers=H,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["kind"] == "text"
    assert "Delay Damages" in body["text"]


def test_preview_foreign_private_doc_still_404(client):
    """A document the workspace cannot cite stays 404 — no cross-project leak."""
    workspace = _new_project(client, "Preview Workspace")
    other = _new_project(client, "Private Other")
    doc = _upload(
        client, other["id"], "secret.txt", b"not a cited source", "text/plain",
    )
    r = client.get(
        f"/v1/projects/{workspace['id']}/documents/{doc['id']}/preview",
        headers=H,
    )
    assert r.status_code == 404


def test_preview_r2_backed_corpus_doc_missing_local_file(
    client, monkeypatch, tmp_path,
):
    """Citeable Master Corpus / GK docs are archived to R2 then the local
    file is deleted (P1B). Preview must follow ``r2_object_key``, not 404
    on a stale ``file_path`` / size 0."""
    from app.core import projects as store

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    workspace = _new_project(client, "Preview Workspace")
    corpus = _new_project(client, "Citeable Corpus")
    pdf = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"
    doc = store.add_document(
        project_id=corpus["id"],
        original_name="DD-2023-118 - Infrastructure Package 1- vol 1-Executed.pdf",
        file_path=str(tmp_path / "gone-after-r2-archive.pdf"),
        size=0,
        metadata={"r2_object_key": "projects/corpus/drive/abc/deadbeef.pdf"},
    )
    assert doc["size"] == 0
    assert not os.path.exists(doc["file_path"] or "")

    monkeypatch.setattr(
        "app.routers.projects._preview_citeable_owner_ids",
        lambda pid: {pid, corpus["id"]},
    )
    r2_calls: list[str] = []

    def _fake_fetch(key: str):
        r2_calls.append(key)
        return pdf if key.endswith("deadbeef.pdf") else None

    monkeypatch.setattr("app.core.r2_storage.fetch_object_bytes", _fake_fetch)

    r = client.get(
        f"/v1/projects/{workspace['id']}/documents/{doc['id']}/preview",
        headers=H,
    )
    assert r.status_code == 200, r.text
    assert r.json()["kind"] == "pdf"
    assert r2_calls

    raw = client.get(
        f"/v1/projects/{workspace['id']}/documents/{doc['id']}/preview/raw",
        headers=H,
    )
    assert raw.status_code == 200, raw.text
    assert raw.content == pdf

    refreshed = store.get_document(doc["id"])
    assert refreshed is not None
    assert refreshed["size"] == len(pdf)
    assert r.json()["has_file"] is True
    assert r.json()["size"] == len(pdf)


def test_preview_foreign_private_doc_does_not_fetch_r2(
    client, monkeypatch, tmp_path,
):
    """Ownership fails closed before any remote hydrate — no blob leak."""
    from app.core import projects as store

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    workspace = _new_project(client, "Preview Workspace")
    other = _new_project(client, "Private Other")
    doc = store.add_document(
        project_id=other["id"],
        original_name="secret.pdf",
        file_path=str(tmp_path / "secret-missing.pdf"),
        size=0,
        metadata={"r2_object_key": "projects/other/drive/zzz/secret.pdf"},
    )
    called: list[str] = []
    monkeypatch.setattr(
        "app.core.r2_storage.fetch_object_bytes",
        lambda key: called.append(key) or b"%PDF-1.4 secret",
    )
    r = client.get(
        f"/v1/projects/{workspace['id']}/documents/{doc['id']}/preview",
        headers=H,
    )
    assert r.status_code == 404
    assert called == []


def test_preview_zero_byte_file_clear_404(client, tmp_path):
    """A real 0-byte file must not 500 — surface an empty-file state."""
    from app.core import projects as store

    proj = _new_project(client)
    empty = tmp_path / "empty.txt"
    empty.write_bytes(b"")
    doc = store.add_document(
        project_id=proj["id"],
        original_name="empty.txt",
        file_path=str(empty),
        size=0,
    )
    r = client.get(
        f"/v1/projects/{proj['id']}/documents/{doc['id']}/preview", headers=H
    )
    assert r.status_code == 404
    assert r.status_code != 500
    detail = r.json()["detail"]
    assert "empty" in detail.lower() or "0 bytes" in detail.lower()


def test_preview_missing_blob_clear_404(client, tmp_path):
    """No local file and no R2/Drive pointer — honest unavailable, not 500."""
    from app.core import projects as store

    proj = _new_project(client)
    doc = store.add_document(
        project_id=proj["id"],
        original_name="vanished.txt",
        file_path=str(tmp_path / "never-written.txt"),
        size=0,
    )
    r = client.get(
        f"/v1/projects/{proj['id']}/documents/{doc['id']}/preview", headers=H
    )
    assert r.status_code == 404
    assert r.status_code != 500
    assert "not available" in r.json()["detail"].lower()


def test_preview_drive_fallback_when_r2_missing(
    client, monkeypatch, tmp_path,
):
    """P1B deletes the local copy even when R2 archive failed — Drive id
    is the remaining source."""
    from app.core import projects as store

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    workspace = _new_project(client, "Preview Workspace")
    corpus = _new_project(client, "Citeable Corpus")
    doc = store.add_document(
        project_id=corpus["id"],
        original_name="cited_from_drive.txt",
        file_path=str(tmp_path / "deleted-after-index.txt"),
        size=0,
        metadata={"drive_file_id": "drive-file-123"},
    )
    monkeypatch.setattr(
        "app.routers.projects._preview_citeable_owner_ids",
        lambda pid: {pid, corpus["id"]},
    )
    monkeypatch.setattr(
        "app.core.gdrive_service.download_file_bytes",
        lambda fid: (
            (b"Clause from Drive original", None)
            if fid == "drive-file-123"
            else (None, "nope")
        ),
    )
    r = client.get(
        f"/v1/projects/{workspace['id']}/documents/{doc['id']}/preview",
        headers=H,
    )
    assert r.status_code == 200, r.text
    assert r.json()["kind"] == "text"
    assert "Drive original" in r.json()["text"]


def _p1b_meta(**extra):
    """Exact keys ``scripts/p1b_ingest_drive_server.py`` writes to metadata."""
    meta = {
        "drive_file_id": "11oD5bJW8tdTtwqyf4fYAVxYhbYwYATiI",
        "drive_path": (
            "Master Folder/the client project/Contract Docs/Contractor/"
            "Contract docs SIGNED/DD-2023-118 - Infrastructure Package 1- vol 1-Executed.pdf"
        ),
        "source": "p1b_server_drive_reingestion",
        "ingestion_run_id": "run-test",
        "mimeType": "application/pdf",
        "content_sha256": "abc123",
        "r2_object_key": "projects/corpus/drive/11oD5bJW8tdTtwqyf4fYAVxYhbYwYATiI/deadbeef.pdf",
        "r2_bucket": "theshovel-raw-docs",
        "r2_endpoint": "https://example.r2.cloudflarestorage.com",
        "r2_account_id": "acct",
    }
    meta.update(extra)
    return meta


def test_extract_p1b_keys_including_string_and_aliases():
    """#445 assumed a dict with r2_object_key. Live audit already parses
    string JSON and driveFileId — preview must do the same."""
    from app.core.projects import extract_document_source_pointers

    p1b = extract_document_source_pointers({"metadata": _p1b_meta()})
    assert p1b["r2_object_key"].endswith("deadbeef.pdf")
    assert p1b["drive_file_id"] == "11oD5bJW8tdTtwqyf4fYAVxYhbYwYATiI"
    assert p1b["r2_bucket"] == "theshovel-raw-docs"

    as_string = extract_document_source_pointers(
        {"metadata": json.dumps(_p1b_meta())},
    )
    assert as_string["r2_object_key"] == p1b["r2_object_key"]
    assert as_string["drive_file_id"] == p1b["drive_file_id"]

    camel = extract_document_source_pointers(
        {"metadata": {"driveFileId": "drive-camel", "r2ObjectKey": "proj/k.pdf"}},
    )
    assert camel["drive_file_id"] == "drive-camel"
    assert camel["r2_object_key"] == "proj/k.pdf"

    nested = extract_document_source_pointers(
        {"metadata": {"r2_archive": {"r2_object_key": "nested/key.pdf"}}},
    )
    assert nested["r2_object_key"] == "nested/key.pdf"


def test_preview_p1b_string_metadata_r2_hydrate(
    client, monkeypatch, tmp_path,
):
    """JSONB-as-string metadata (the audit script already special-cases this)
    must still find r2_object_key and return 200."""
    from app.core import projects as store
    from app.core.db import SessionLocal
    from app.core.models import Document

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    workspace = _new_project(client, "Preview Workspace")
    corpus = _new_project(client, "Citeable Corpus")
    pdf = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"
    doc = store.add_document(
        project_id=corpus["id"],
        original_name="DD-2023-118 - Infrastructure Package 1- vol 1-Executed.pdf",
        file_path=str(tmp_path / "gone-after-r2-archive.pdf"),
        size=0,
        metadata=_p1b_meta(),
    )
    with SessionLocal() as session:
        row = session.get(Document, doc["id"])
        assert row is not None
        row.metadata_ = json.dumps(_p1b_meta())
        session.commit()

    monkeypatch.setattr(
        "app.routers.projects._preview_citeable_owner_ids",
        lambda pid: {pid, corpus["id"]},
    )
    monkeypatch.setattr(
        "app.core.r2_storage.fetch_object_bytes",
        lambda key, bucket=None: pdf if key.endswith("deadbeef.pdf") else None,
    )

    r = client.get(
        f"/v1/projects/{workspace['id']}/documents/{doc['id']}/preview",
        headers=H,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["kind"] == "pdf"
    assert body["has_file"] is True
    assert body["size"] == len(pdf)

    listing = client.get(
        f"/v1/projects/{corpus['id']}/documents", headers=H,
    )
    assert listing.status_code == 200, listing.text
    listed = next(d for d in listing.json()["documents"] if d["id"] == doc["id"])
    assert listed["has_file"] is True
    assert listed["has_remote_source"] is True
    assert listed["size"] == len(pdf)


def test_preview_reconstructed_p1b_r2_key_when_metadata_omits_it(
    client, monkeypatch, tmp_path,
):
    """rag_render bulk ingest stores drive_file_id + size=0 and no
    r2_object_key. Reconstruct the deterministic P1B key before Drive."""
    from app.core import projects as store
    from app.core import r2_storage

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    workspace = _new_project(client, "Preview Workspace")
    corpus = _new_project(client, "Citeable Corpus")
    drive_id = "11oD5bJW8tdTtwqyf4fYAVxYhbYwYATiI"
    name = "DD-2023-118 - Infrastructure Package 1- vol 1-Executed.pdf"
    expected = r2_storage.object_key_for(corpus["id"], drive_id, name)
    pdf = b"%PDF-1.4 reconstructed\n"
    doc = store.add_document(
        project_id=corpus["id"],
        original_name=name,
        file_path=str(tmp_path / "stale-windows-path.pdf"),
        size=0,
        metadata={
            "drive_file_id": drive_id,
            "source_path": "G:\\\\My Drive\\\\Master Folder\\\\" + name,
            "source": "rag_backfill_client_clean_all",
        },
    )
    monkeypatch.setattr(
        "app.routers.projects._preview_citeable_owner_ids",
        lambda pid: {pid, corpus["id"]},
    )
    seen: list[str] = []

    def _fake_fetch(key: str, bucket=None):
        seen.append(key)
        return pdf if key == expected else None

    monkeypatch.setattr("app.core.r2_storage.fetch_object_bytes", _fake_fetch)

    r = client.get(
        f"/v1/projects/{workspace['id']}/documents/{doc['id']}/preview",
        headers=H,
    )
    assert r.status_code == 200, r.text
    assert expected in seen
    assert r.json()["kind"] == "pdf"


def test_preview_r2_not_configured_clear_404(
    client, monkeypatch, tmp_path,
):
    """R2 env missing must not look like a missing document."""
    from app.core import projects as store

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    proj = _new_project(client)
    doc = store.add_document(
        project_id=proj["id"],
        original_name="DD-2023-118 - Infrastructure Package 1- vol 1-Executed.pdf",
        file_path=str(tmp_path / "deleted-local.pdf"),
        size=0,
        metadata=_p1b_meta(),
    )
    monkeypatch.setattr("app.core.r2_storage.fetch_object_bytes", lambda key, bucket=None: None)
    monkeypatch.setattr(
        "app.core.r2_storage.fetch_failure_reason",
        lambda key, bucket=None: "R2 is not configured on this service",
    )
    monkeypatch.setattr(
        "app.core.gdrive_service.download_file_bytes",
        lambda fid: (None, "service account unavailable"),
    )
    r = client.get(
        f"/v1/projects/{proj['id']}/documents/{doc['id']}/preview", headers=H,
    )
    assert r.status_code == 404
    assert r.status_code != 500
    detail = r.json()["detail"].lower()
    assert "not available" in detail
    assert "r2 is not configured" in detail
    assert "service account unavailable" in detail


def test_preview_r2_fetch_failed_clear_404(
    client, monkeypatch, tmp_path,
):
    """Key present, GET failed — say so, do not hide behind generic missing."""
    from app.core import projects as store

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    proj = _new_project(client)
    doc = store.add_document(
        project_id=proj["id"],
        original_name="cited.pdf",
        file_path=str(tmp_path / "gone.pdf"),
        size=0,
        metadata=_p1b_meta(),
    )
    monkeypatch.setattr("app.core.r2_storage.fetch_object_bytes", lambda key, bucket=None: None)
    monkeypatch.setattr(
        "app.core.r2_storage.fetch_failure_reason",
        lambda key, bucket=None: "R2 object missing or fetch failed",
    )
    monkeypatch.setattr(
        "app.core.gdrive_service.download_file_bytes",
        lambda fid: (None, "Drive download returned 404"),
    )
    r = client.get(
        f"/v1/projects/{proj['id']}/documents/{doc['id']}/preview", headers=H,
    )
    assert r.status_code == 404
    detail = r.json()["detail"]
    assert "not available" in detail.lower()
    assert "R2 object missing" in detail or "fetch failed" in detail.lower()


def test_list_documents_has_file_for_remote_pointer(client, tmp_path):
    """Nav must not treat size=0 + R2/Drive pointer as 'no file' forever."""
    from app.core import projects as store

    proj = _new_project(client)
    doc = store.add_document(
        project_id=proj["id"],
        original_name="DD-2023-118 - Infrastructure Package 1- vol 1-Executed.pdf",
        file_path=str(tmp_path / "stale.pdf"),
        size=0,
        metadata=_p1b_meta(),
    )
    r = client.get(f"/v1/projects/{proj['id']}/documents", headers=H)
    assert r.status_code == 200, r.text
    listed = next(d for d in r.json()["documents"] if d["id"] == doc["id"])
    assert listed["size"] == 0
    assert listed["has_remote_source"] is True
    assert listed["has_file"] is True


def test_preview_rag_backfill_stub_resolves_drive_by_filename(
    client, monkeypatch, tmp_path,
):
    """Live ocr1exec shape: size=0, G:\\ path, source=rag_backfill_client_clean_all,
    drive_file_id null, no r2_object_key. Filename lookup + download must
    200 and persist the Drive id."""
    from app.core import projects as store

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    workspace = _new_project(client, "Preview Workspace")
    corpus = _new_project(client, "Citeable Corpus")
    name = "DD-2023-118 - Infrastructure Package 1- vol 1-Executed.pdf"
    drive_id = "11oD5bJW8tdTtwqyf4fYAVxYhbYwYATiI"
    pdf = b"%PDF-1.4 from Drive filename resolve\n"
    doc = store.add_document(
        project_id=corpus["id"],
        original_name=name,
        file_path=(
            r"G:\My Drive\Master Folder\the client project\Contract Docs"
            r"\Contractor\Contract docs SIGNED\\" + name
        ),
        size=0,
        metadata={
            "source": "rag_backfill_client_clean_all",
            "drive_file_id": None,
            "source_path": (
                r"G:\My Drive\Master Folder\the client project\Contract Docs"
                r"\Contractor\Contract docs SIGNED\\" + name
            ),
            "ext": ".pdf",
        },
    )
    assert doc.get("has_remote_source") is False
    monkeypatch.setattr(
        "app.routers.projects._preview_citeable_owner_ids",
        lambda pid: {pid, corpus["id"]},
    )
    lookups: list[str] = []

    def _lookup(filename: str):
        lookups.append(filename)
        return (drive_id, None) if filename == name else (None, "nope")

    monkeypatch.setattr(
        "app.core.gdrive_service.find_file_id_by_exact_name", _lookup,
    )
    monkeypatch.setattr(
        "app.core.gdrive_service.download_file_bytes",
        lambda fid: (pdf, None) if fid == drive_id else (None, "wrong id"),
    )

    r = client.get(
        f"/v1/projects/{workspace['id']}/documents/{doc['id']}/preview",
        headers=H,
    )
    assert r.status_code == 200, r.text
    assert r.json()["kind"] == "pdf"
    assert lookups == [name]

    refreshed = store.get_document(doc["id"])
    assert refreshed is not None
    assert (refreshed.get("metadata") or {}).get("drive_file_id") == drive_id
    assert refreshed["size"] == len(pdf)


def test_preview_rag_backfill_stub_unresolved_clear_404(
    client, monkeypatch, tmp_path,
):
    """Unlinkable stub stays an honest 404 — no invented bytes."""
    from app.core import projects as store

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    proj = _new_project(client)
    name = "DD-2023-118 - Infrastructure Package 1- vol 1-Executed.pdf"
    doc = store.add_document(
        project_id=proj["id"],
        original_name=name,
        file_path=r"G:\My Drive\Master Folder\\" + name,
        size=0,
        metadata={
            "source": "rag_backfill_client_clean_all",
            "drive_file_id": None,
        },
    )
    monkeypatch.setattr(
        "app.core.gdrive_service.find_file_id_by_exact_name",
        lambda filename: (None, f"no Drive file named {filename}"),
    )
    r = client.get(
        f"/v1/projects/{proj['id']}/documents/{doc['id']}/preview", headers=H,
    )
    assert r.status_code == 404
    assert r.status_code != 500
    detail = r.json()["detail"]
    assert "not available" in detail.lower()
    assert "no R2 object key or Drive file id" in detail
    assert name in detail


def test_preview_user_upload_local_path_still_200(client):
    """A real on-disk upload must not go through Drive filename resolve."""
    proj = _new_project(client)
    doc = _upload(client, proj["id"], "site-note.txt", b"hello from disk", "text/plain")
    r = client.get(
        f"/v1/projects/{proj['id']}/documents/{doc['id']}/preview", headers=H,
    )
    assert r.status_code == 200, r.text
    assert r.json()["kind"] == "text"
    assert "hello from disk" in r.json()["text"]
