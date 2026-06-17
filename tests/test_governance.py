"""Tests for data governance — audit, deletion, retention. Roadmap V2 · Epic 6."""

import importlib
import os

import pytest
from cryptography.fernet import Fernet
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


def test_project_cascade_audits_each_document(client):
    """Regression guard for the 'BOQ disappeared with no explanation'
    failure mode. When a project delete cascades through its documents,
    each document removal must leave its OWN audit row — not just a
    single ``project.deleted`` summary. Without per-doc rows there's
    no forensic trail of which specific docs the cascade swept."""
    pid, doc_id = _project_with_doc(client)
    r = client.delete(f"/v1/projects/{pid}", headers=H)
    assert r.status_code == 200
    entries = audit.read_audit(project_id=pid)
    cascade_events = [
        e for e in entries
        if e.get("event") == "document.deleted"
        and e.get("document_id") == doc_id
        and e.get("reason") == "project_cascade"
    ]
    assert cascade_events, (
        f"expected a 'document.deleted' audit row with reason='project_cascade' "
        f"for doc {doc_id}; got events={[e.get('event') for e in entries]}"
    )


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


def test_governance_purge_audits_each_document(client, monkeypatch):
    """Counterpart guard for the bulk-purge path. The summary
    ``governance.purge`` row records only a count — for forensics each
    purged document must also get its own ``document.deleted`` row with
    reason='governance_purge'."""
    # Make the retention window so short that the doc-just-uploaded is
    # already past it: 0 days, i.e. purge-everything.
    pid, doc_id = _project_with_doc(client)
    monkeypatch.setenv("DATA_RETENTION_DAYS", "0")
    # Re-set to a positive value below the document's age in microseconds —
    # effectively zero. The endpoint reads the env var at call-time.
    # We need a value > 0 to enter the purge branch; the test
    # documents.uploaded_at is "now" so we backdate via the env var.
    # Simpler: set retention_days to a tiny positive integer and override
    # the cutoff comparison by directly calling the store with days=0
    # bypassing the endpoint guard. Below we go through the HTTP path with
    # a 1-day window but stamp the doc's uploaded_at as 2 days ago.
    from app.core import projects as store
    from datetime import datetime, timedelta, timezone
    from sqlalchemy import update
    backdate = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    with store.SessionLocal() as session:
        session.execute(
            update(store.Document)
            .where(store.Document.id == doc_id)
            .values(uploaded_at=backdate)
        )
        session.commit()
    monkeypatch.setenv("DATA_RETENTION_DAYS", "1")

    r = client.post("/v1/governance/purge", headers=H)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "purged"
    assert body["documents_purged"] >= 1

    entries = audit.read_audit(project_id=pid)
    purge_events = [
        e for e in entries
        if e.get("event") == "document.deleted"
        and e.get("document_id") == doc_id
        and e.get("reason") == "governance_purge"
    ]
    assert purge_events, (
        f"expected a 'document.deleted' audit row with reason='governance_purge' "
        f"for doc {doc_id}; got events={[e.get('event') for e in entries]}"
    )


# ── encryption at rest (Roadmap V2 · Epic 6 follow-up) ──────────────────────

def _upload_doc(client, content: bytes):
    """Create a project, attach a document, return (project, document) records."""
    pid = client.post("/v1/projects", json={"name": "Crypto Test"},
                       headers=H).json()["id"]
    files = {"file": ("confidential.pdf", content, "application/pdf")}
    doc = client.post(f"/v1/projects/{pid}/documents",
                      files=files, headers=H).json()
    return pid, doc["document"]


def test_uploaded_document_is_encrypted_on_disk_with_key(monkeypatch):
    """With DATA_ENCRYPTION_KEY set, the stored file is ciphertext on disk but
    still decrypts back to the original content."""
    monkeypatch.setenv("DATA_ENCRYPTION_KEY", Fernet.generate_key().decode())
    from app.core import file_crypto
    importlib.reload(file_crypto)
    # Re-import the routers so they pick up the reloaded file_crypto module.
    from app.routers import projects as projects_router
    importlib.reload(projects_router)

    content = b"%PDF-1.4 highly confidential client document"
    with TestClient(app) as c:
        pid, doc = _upload_doc(c, content)
    file_path = doc["file_path"]

    on_disk = open(file_path, "rb").read()
    assert on_disk != content, "file should be ciphertext on disk"
    assert file_crypto.looks_encrypted(on_disk)
    # ...but reads back transparently to the original plaintext.
    assert file_crypto.read_document(file_path) == content
    # recorded size is the original plaintext size, not the ciphertext size
    assert doc["size"] == len(content)

    importlib.reload(file_crypto)
    importlib.reload(projects_router)


def test_uploaded_document_stays_plaintext_without_key(monkeypatch):
    """With no key, the stored file is plaintext on disk — unchanged behaviour."""
    monkeypatch.delenv("DATA_ENCRYPTION_KEY", raising=False)
    from app.core import file_crypto
    importlib.reload(file_crypto)
    from app.routers import projects as projects_router
    importlib.reload(projects_router)

    content = b"%PDF-1.4 plaintext document"
    with TestClient(app) as c:
        pid, doc = _upload_doc(c, content)
    file_path = doc["file_path"]

    assert open(file_path, "rb").read() == content
    assert not file_crypto.looks_encrypted(open(file_path, "rb").read())
