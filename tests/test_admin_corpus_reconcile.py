"""Tests for the corpus reconciliation endpoint.

Detects chunks whose ``project_id`` disagrees with their parent document's
``project_id`` — the root cause of the training_material scoping anomaly after
the drive_archive migration.
"""
from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.core.db import SessionLocal, engine
from app.core.models import Document, Project, RagChunk, User
from app.dependencies import require_api_key


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


def _ensure_schema():
    from app.core.projects import init_db as init_projects_db
    init_projects_db()
    RagChunk.__table__.create(bind=engine, checkfirst=True)
    with SessionLocal() as session:
        if session.get(User, "test-admin") is None:
            session.add(User(
                id="test-admin",
                email="test-admin@example.com",
                password_hash="",
                salt="",
                display_name="Test Admin",
                role="admin",
                created_at=datetime.now(timezone.utc).isoformat(),
            ))
            session.commit()


def _wipe(*pids: str):
    with SessionLocal() as session:
        for pid in pids:
            session.query(RagChunk).filter(RagChunk.project_id == pid).delete()
            session.query(Document).filter(Document.project_id == pid).delete()
            session.query(Project).filter(Project.id == pid).delete()
        session.commit()


def _wipe_all():
    """Delete every RagChunk/Document/Project row for test isolation.

    The reconcile endpoint reports mismatches across the whole corpus, so
    leftovers from earlier tests make exact-count assertions flaky. Wipe
    everything before seeding the reconcile fixture.
    """
    _ensure_schema()
    with SessionLocal() as session:
        session.query(RagChunk).delete()
        session.query(Document).delete()
        session.query(Project).delete()
        session.commit()


def _seed_misplaced_chunks():
    """Create two projects where one chunk is under the wrong project_id."""
    _wipe_all()
    now = datetime.now(timezone.utc).isoformat()
    vec = np.zeros(256, dtype=np.float32)
    with SessionLocal() as session:
        for pid in ("reconcile_a", "reconcile_b"):
            session.add(Project(
                id=pid, name=pid, user_id="test-admin",
                status="active", created_at=now,
            ))
        session.flush()
        session.add(Document(
            id="doc_a", project_id="reconcile_a",
            original_name="a.pdf", doc_type="document",
            doc_role="other", size=0, uploaded_at=now,
        ))
        session.add(Document(
            id="doc_b", project_id="reconcile_b",
            original_name="b.pdf", doc_type="document",
            doc_role="other", size=0, uploaded_at=now,
        ))
        # doc_a's chunk is wrongly stored under reconcile_b.
        session.add(RagChunk(
            chunk_id="misplaced_1", project_id="reconcile_b",
            doc_id="doc_a", chunk_index=0, text="content",
            embedding=vec, created_at=now,
        ))
        # doc_b's chunk is correctly stored under reconcile_b.
        session.add(RagChunk(
            chunk_id="correct_1", project_id="reconcile_b",
            doc_id="doc_b", chunk_index=0, text="content",
            embedding=vec, created_at=now,
        ))
        # A dangling chunk with no parent document.
        session.add(RagChunk(
            chunk_id="dangling_1", project_id="reconcile_a",
            doc_id="doc_missing", chunk_index=0, text="content",
            embedding=vec, created_at=now,
        ))
        session.commit()


def test_reconcile_detects_misplaced_chunks(client):
    _seed_misplaced_chunks()
    resp = client.post("/v1/admin/corpus/reconcile")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["dry_run"] is True
    assert body["execute"] is False
    assert body["mismatches_total"] == 1
    assert body["dangling_total"] == 1
    sample = body["mismatches_sample"]
    assert any(m["chunk_id"] == "misplaced_1" and
               m["current_project_id"] == "reconcile_b" and
               m["correct_project_id"] == "reconcile_a"
               for m in sample)
    _wipe("reconcile_a", "reconcile_b")


def test_reconcile_repairs_misplaced_chunks(client):
    _seed_misplaced_chunks()
    resp = client.post("/v1/admin/corpus/reconcile?execute=true")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["dry_run"] is False
    assert body["execute"] is True
    assert body["repaired"] == 1
    assert body["mismatches_total"] == 1

    # After repair, the chunk is under the correct project.
    with SessionLocal() as session:
        chunk = session.get(RagChunk, "misplaced_1")
        assert chunk is not None
        assert chunk.project_id == "reconcile_a"
        assert chunk.doc_id == "doc_a"
    _wipe("reconcile_a", "reconcile_b")


def test_reconcile_non_admin_blocked(client):
    from app.dependencies import require_api_key as real_key
    app.dependency_overrides[real_key] = lambda: {
        "user_id": "test-user", "role": "user",
    }
    try:
        resp = client.post("/v1/admin/corpus/reconcile")
    finally:
        app.dependency_overrides[real_key] = lambda: {
            "user_id": "test-admin", "role": "admin",
        }
    assert resp.status_code == 403


def test_reconcile_reports_no_mismatches_when_clean(client):
    _wipe_all()
    now = datetime.now(timezone.utc).isoformat()
    vec = np.zeros(256, dtype=np.float32)
    with SessionLocal() as session:
        session.add(Project(
            id="reconcile_clean", name="reconcile_clean",
            user_id="test-admin", status="active", created_at=now,
        ))
        session.flush()
        session.add(Document(
            id="doc_clean", project_id="reconcile_clean",
            original_name="clean.pdf", doc_type="document",
            doc_role="other", size=0, uploaded_at=now,
        ))
        session.add(RagChunk(
            chunk_id="clean_chunk", project_id="reconcile_clean",
            doc_id="doc_clean", chunk_index=0, text="content",
            embedding=vec, created_at=now,
        ))
        session.commit()

    resp = client.post("/v1/admin/corpus/reconcile")
    assert resp.status_code == 200
    body = resp.json()
    assert body["mismatches_total"] == 0
    assert body["dangling_total"] == 0
    assert body["repaired"] == 0
    _wipe("reconcile_clean")
