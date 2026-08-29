"""Tests for the inline document-preview endpoint.

Covers the render-friendly JSON shapes returned by
``GET /v1/projects/{pid}/documents/{did}/preview`` — an xlsx workbook renders
as a table, an unpreviewable-but-allowed extension reports 'unsupported', and a
malformed spreadsheet returns 422 (never 500).
"""

import io
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
