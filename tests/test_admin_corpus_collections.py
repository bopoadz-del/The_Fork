"""PR #91 — admin corpus-collections endpoint contract guards.

Verifies the GET /v1/admin/corpus/collections endpoint:
  * gates on admin role (403 for non-admin)
  * returns per-project_id document + chunk counts
  * sorts collections by chunks desc, then documents desc, then project_id
  * emits by_top_folder ONLY for project_ids above the folder_breakdown_min
    threshold (default 50 documents), so ad-hoc projects don't drown the
    response in single-folder tiles
  * tolerates the case where a project_id exists in chunks but not
    documents (legacy import pattern from the Drive corpus seed)

Uses the app's existing SQLite test DB seeded via SQLAlchemy ORM —
avoids reaching into raw sqlite3 to keep the test resilient to schema
drift handled by Alembic / model edits.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.core.db import SessionLocal, engine
from app.core.models import Document, Project, RagChunk


def _ensure_schema():
    """Make sure both `documents` (via projects init) and `chunks`
    (RagChunk) tables exist. Mirrors the bootstrap path the live app
    uses on first request — calls projects.init_db then ensures the
    RagChunk table via the same checkfirst=True pattern
    vector_store.py uses."""
    from app.core.projects import init_db as init_projects_db
    init_projects_db()
    RagChunk.__table__.create(bind=engine, checkfirst=True)


@pytest.fixture(autouse=True)
def _stub_admin(monkeypatch):
    """Force require_api_key to return an admin identity so we can test
    the endpoint's business logic without exercising JWT/users flow."""
    from app.routers import admin as admin_mod
    from app.dependencies import require_api_key as real_require_api_key

    def fake_admin():
        return {"user_id": "test-admin", "role": "admin"}

    def fake_user():
        return {"user_id": "test-user", "role": "user"}

    yield {"admin": fake_admin, "user": fake_user, "real": real_require_api_key}


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def _seed_corpus():
    """Insert a known-shape mini-corpus:
       - "big_corpus": 60 documents (above folder_breakdown_min), 200 chunks
                       across two top-folder prefixes (40 in 'A/', 20 in 'B/')
       - "small_project": 3 documents (below threshold), 10 chunks
       - "chunks_only_legacy": 0 documents, 5 chunks (legacy Drive imports)
    """
    import uuid

    _ensure_schema()

    with SessionLocal() as session:
        # Wipe pre-existing test rows for these ids — keeps the test idempotent
        # against repeated runs in the same SQLite DB.
        for pid in ("big_corpus", "small_project", "chunks_only_legacy"):
            session.query(Document).filter(Document.project_id == pid).delete()
            session.query(RagChunk).filter(RagChunk.project_id == pid).delete()
            session.query(Project).filter(Project.id == pid).delete()
        session.commit()

        # Projects (FK target for documents)
        for pid, name in (
            ("big_corpus", "Big corpus"),
            ("small_project", "Small project"),
        ):
            session.add(Project(
                id=pid, name=name, user_id="system",
                created_at="2026-06-21T00:00:00Z",
                status="active",
            ))
        session.flush()

        # big_corpus: 40 'A/...' + 20 'B/...' = 60 docs
        for i in range(40):
            session.add(Document(
                id=str(uuid.uuid4())[:8], project_id="big_corpus",
                original_name=f"A/folder/file_{i:03d}.pdf",
                doc_type="document", doc_role="other", size=1024,
                uploaded_at="2026-06-21T00:00:00Z",
            ))
        for i in range(20):
            session.add(Document(
                id=str(uuid.uuid4())[:8], project_id="big_corpus",
                original_name=f"B/folder/file_{i:03d}.pdf",
                doc_type="document", doc_role="other", size=1024,
                uploaded_at="2026-06-21T00:00:00Z",
            ))
        # small_project: 3 docs in one folder
        for i in range(3):
            session.add(Document(
                id=str(uuid.uuid4())[:8], project_id="small_project",
                original_name=f"misc/file_{i}.pdf",
                doc_type="document", doc_role="other", size=512,
                uploaded_at="2026-06-21T00:00:00Z",
            ))

        # chunks rows for each project
        import numpy as np
        zero_vec = np.zeros(256, dtype=np.float32)
        for pid, n_chunks in (
            ("big_corpus", 200),
            ("small_project", 10),
            ("chunks_only_legacy", 5),
        ):
            for i in range(n_chunks):
                session.add(RagChunk(
                    chunk_id=f"{pid}-{i}-{uuid.uuid4().hex[:6]}",
                    project_id=pid,
                    doc_id=f"doc-{i // 5}",
                    chunk_index=i % 5,
                    text=f"chunk {i}",
                    embedding=zero_vec,
                    created_at="2026-06-21T00:00:00Z",
                ))
        session.commit()


def test_admin_corpus_collections_returns_per_project_counts(client, _stub_admin):
    _seed_corpus()
    app.dependency_overrides[__import__("app.dependencies", fromlist=["require_api_key"]).require_api_key] = _stub_admin["admin"]
    try:
        resp = client.get("/v1/admin/corpus/collections")
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 200, resp.text
    body = resp.json()
    cols = {c["project_id"]: c for c in body["collections"]}
    assert "big_corpus" in cols
    assert cols["big_corpus"]["documents"] == 60
    assert cols["big_corpus"]["chunks"] == 200
    assert cols["small_project"]["documents"] == 3
    assert cols["small_project"]["chunks"] == 10
    # chunks-only legacy: 0 docs, 5 chunks — must still appear so operator
    # sees that the index has dangling data without parent documents.
    assert cols["chunks_only_legacy"]["documents"] == 0
    assert cols["chunks_only_legacy"]["chunks"] == 5


def test_folder_breakdown_only_above_threshold(client, _stub_admin):
    _seed_corpus()
    app.dependency_overrides[__import__("app.dependencies", fromlist=["require_api_key"]).require_api_key] = _stub_admin["admin"]
    try:
        resp = client.get("/v1/admin/corpus/collections")
    finally:
        app.dependency_overrides.clear()
    cols = {c["project_id"]: c for c in resp.json()["collections"]}

    # big_corpus has 60 docs >= 50 default threshold -> breakdown present
    assert "by_top_folder" in cols["big_corpus"]
    folders = {f["folder"]: f["docs"] for f in cols["big_corpus"]["by_top_folder"]}
    assert folders == {"A": 40, "B": 20}

    # small_project has 3 docs -> no breakdown
    assert "by_top_folder" not in cols["small_project"]
    # chunks_only_legacy has 0 docs -> no breakdown
    assert "by_top_folder" not in cols["chunks_only_legacy"]


def test_sort_order_chunks_desc_then_documents(client, _stub_admin):
    _seed_corpus()
    app.dependency_overrides[__import__("app.dependencies", fromlist=["require_api_key"]).require_api_key] = _stub_admin["admin"]
    try:
        resp = client.get("/v1/admin/corpus/collections")
    finally:
        app.dependency_overrides.clear()
    ids = [c["project_id"] for c in resp.json()["collections"]]
    # big_corpus first (200 chunks), then small_project (10), then chunks_only_legacy (5)
    assert ids[:3] == ["big_corpus", "small_project", "chunks_only_legacy"]


def test_non_admin_gets_403(client, _stub_admin):
    app.dependency_overrides[__import__("app.dependencies", fromlist=["require_api_key"]).require_api_key] = _stub_admin["user"]
    try:
        resp = client.get("/v1/admin/corpus/collections")
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 403


def test_folder_breakdown_disable(client, _stub_admin):
    _seed_corpus()
    app.dependency_overrides[__import__("app.dependencies", fromlist=["require_api_key"]).require_api_key] = _stub_admin["admin"]
    try:
        resp = client.get("/v1/admin/corpus/collections?folder_breakdown=false")
    finally:
        app.dependency_overrides.clear()
    cols = {c["project_id"]: c for c in resp.json()["collections"]}
    # With breakdown disabled, even the big_corpus must NOT have by_top_folder.
    assert "by_top_folder" not in cols["big_corpus"]
