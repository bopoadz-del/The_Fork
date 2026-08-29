"""Tests for the inline document-preview endpoint.

Covers the render-friendly JSON shapes returned by
``GET /v1/projects/{pid}/documents/{did}/preview`` — an xlsx workbook renders
as a table, an unpreviewable-but-allowed extension reports 'unsupported', and a
malformed spreadsheet returns 422 (never 500).
"""

import io

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
