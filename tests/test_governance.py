"""Tests for data governance — audit, deletion, retention. Roadmap V2 · Epic 6."""

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.core import audit

H = {"Authorization": "Bearer cb_dev_key"}


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def _project_with_doc(client):
    pid = client.post("/v1/projects", json={"name": "Gov Test"}, headers=H).json()["id"]
    files = {"file": ("site_drawing.pdf", b"%PDF-1.4 data", "application/pdf")}
    doc = client.post(f"/v1/projects/{pid}/documents", files=files, headers=H).json()
    return pid, doc["document"]["id"]


# ── audit log ───────────────────────────────────────────────────────────────

def test_audit_record_and_read():
    e = audit.record("test.event", project_id="pXYZ", note="hello")
    assert e["event"] == "test.event" and "ts" in e
    entries = audit.read_audit(project_id="pXYZ")
    assert any(x["event"] == "test.event" for x in entries)


def test_actions_are_audited(client):
    pid, doc_id = _project_with_doc(client)
    entries = client.get(f"/v1/projects/{pid}/audit", headers=H).json()["entries"]
    events = {e["event"] for e in entries}
    assert "project.created" in events
    assert "document.added" in events


# ── deletion ────────────────────────────────────────────────────────────────

def test_delete_single_document(client):
    pid, doc_id = _project_with_doc(client)
    r = client.delete(f"/v1/projects/{pid}/documents/{doc_id}", headers=H)
    assert r.status_code == 200
    assert r.json()["file_removed"] is True
    docs = client.get(f"/v1/projects/{pid}", headers=H).json()["documents"]
    assert doc_id not in [d["id"] for d in docs]


def test_delete_project_purges_files(client):
    pid, doc_id = _project_with_doc(client)
    r = client.delete(f"/v1/projects/{pid}", headers=H)
    assert r.status_code == 200
    assert r.json()["files_purged"] >= 1
    assert client.get(f"/v1/projects/{pid}", headers=H).status_code == 404


def test_delete_missing_document_404(client):
    pid, _ = _project_with_doc(client)
    assert client.delete(
        f"/v1/projects/{pid}/documents/nope999", headers=H
    ).status_code == 404


# ── governance status / purge ───────────────────────────────────────────────

def test_governance_status(client):
    r = client.get("/v1/governance", headers=H)
    assert r.status_code == 200
    body = r.json()
    assert body["audit_logging"] is True
    assert body["delete_on_request"] is True
    assert "data_directory" in body


def test_purge_is_noop_without_retention(client):
    # DATA_RETENTION_DAYS not set in the test environment
    r = client.post("/v1/governance/purge", headers=H)
    assert r.status_code == 200
    assert r.json()["status"] == "skipped"
