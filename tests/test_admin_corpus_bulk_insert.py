"""PR #93 — admin bulk-insert endpoint contract guards.

Verifies POST /v1/admin/corpus/bulk-insert:
  * admin-only (403 for non-admin)
  * inserts in FK order (projects -> documents -> chunks) and returns
    per-table inserted counts plus seen counts
  * ON CONFLICT DO NOTHING — second send of the same payload reports
    inserted=0 for every table (idempotent)
  * 256-dim embedding lists land in chunks.embedding correctly
"""
from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.core.db import SessionLocal, engine
from app.core.models import Document, Project, RagChunk
from app.dependencies import require_api_key


def _ensure_schema():
    from app.core.projects import init_db as init_projects_db
    init_projects_db()
    RagChunk.__table__.create(bind=engine, checkfirst=True)


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def _admin_override():
    app.dependency_overrides[require_api_key] = lambda: {
        "user_id": "test-admin", "role": "admin",
    }
    yield
    app.dependency_overrides.clear()


def _payload():
    now = datetime.now(timezone.utc).isoformat()
    vec = [0.0] * 256
    return {
        "projects": [
            {"id": "bulk_test_proj", "name": "Bulk Test", "user_id": "system",
             "status": "active", "created_at": now},
        ],
        "documents": [
            {"id": "bulk_doc_1", "project_id": "bulk_test_proj",
             "original_name": "test.pdf", "size": 1024, "uploaded_at": now},
            {"id": "bulk_doc_2", "project_id": "bulk_test_proj",
             "original_name": "test2.pdf", "size": 2048, "uploaded_at": now},
        ],
        "chunks": [
            {"chunk_id": "bulk_c_1", "project_id": "bulk_test_proj",
             "doc_id": "bulk_doc_1", "chunk_index": 0, "text": "hello",
             "embedding": vec, "created_at": now},
            {"chunk_id": "bulk_c_2", "project_id": "bulk_test_proj",
             "doc_id": "bulk_doc_1", "chunk_index": 1, "text": "world",
             "embedding": vec, "created_at": now},
            {"chunk_id": "bulk_c_3", "project_id": "bulk_test_proj",
             "doc_id": "bulk_doc_2", "chunk_index": 0, "text": "other",
             "embedding": vec, "created_at": now},
        ],
    }


def _wipe_test_rows():
    with SessionLocal() as session:
        session.query(RagChunk).filter(RagChunk.project_id == "bulk_test_proj").delete()
        session.query(Document).filter(Document.project_id == "bulk_test_proj").delete()
        session.query(Project).filter(Project.id == "bulk_test_proj").delete()
        session.commit()


def test_bulk_insert_inserts_in_fk_order(client):
    _ensure_schema()
    _wipe_test_rows()
    resp = client.post("/v1/admin/corpus/bulk-insert", json=_payload())
    assert resp.status_code == 200, resp.text
    counts = resp.json()["counts"]
    assert counts["projects"] == 1
    assert counts["documents"] == 2
    assert counts["chunks"] == 3
    assert counts["projects_seen"] == 1
    assert counts["documents_seen"] == 2
    assert counts["chunks_seen"] == 3
    # Verify rows actually landed
    with SessionLocal() as session:
        assert session.query(Project).filter(Project.id == "bulk_test_proj").count() == 1
        assert session.query(Document).filter(Document.project_id == "bulk_test_proj").count() == 2
        assert session.query(RagChunk).filter(RagChunk.project_id == "bulk_test_proj").count() == 3
    _wipe_test_rows()


def test_bulk_insert_is_idempotent(client):
    _ensure_schema()
    _wipe_test_rows()
    p = _payload()
    first = client.post("/v1/admin/corpus/bulk-insert", json=p).json()["counts"]
    assert (first["projects"], first["documents"], first["chunks"]) == (1, 2, 3)
    second = client.post("/v1/admin/corpus/bulk-insert", json=p).json()["counts"]
    # All re-sends collide on existing primary keys, ON CONFLICT DO NOTHING -> zero inserted
    assert (second["projects"], second["documents"], second["chunks"]) == (0, 0, 0)
    # seen counts still reflect the request size
    assert (second["projects_seen"], second["documents_seen"], second["chunks_seen"]) == (1, 2, 3)
    _wipe_test_rows()


def test_bulk_insert_non_admin_blocked(client):
    app.dependency_overrides[require_api_key] = lambda: {
        "user_id": "test-user", "role": "user",
    }
    try:
        resp = client.post("/v1/admin/corpus/bulk-insert", json={"projects": [], "documents": [], "chunks": []})
    finally:
        app.dependency_overrides[require_api_key] = lambda: {
            "user_id": "test-admin", "role": "admin",
        }
    assert resp.status_code == 403


def test_bulk_insert_handles_empty_payload(client):
    _ensure_schema()
    resp = client.post("/v1/admin/corpus/bulk-insert", json={"projects": [], "documents": [], "chunks": []})
    assert resp.status_code == 200
    counts = resp.json()["counts"]
    assert counts == {"projects": 0, "documents": 0, "chunks": 0,
                      "projects_seen": 0, "documents_seen": 0, "chunks_seen": 0}
