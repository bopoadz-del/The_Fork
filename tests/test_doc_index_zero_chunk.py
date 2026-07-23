"""Fail-loud zero-chunk indexing (Task T4).

* index_document returns an explicit error status for supported files that
  produce no chunks.
* admin_project_reindex surfaces the ZERO_CHUNK condition as a 422 with a
  banner instead of silently returning a green summary.
"""
from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient

from app.dependencies import require_api_key
from app.main import app


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def _admin_auth():
    app.dependency_overrides[require_api_key] = lambda: {
        "user_id": "system",
        "role": "admin",
    }
    yield
    app.dependency_overrides.clear()


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    """Isolated DATA_DIR + fresh projects DB for each test."""
    from app.core import projects as projects_mod

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.delenv("DATA_ENCRYPTION_KEY", raising=False)
    monkeypatch.setattr(projects_mod, "_initialized", False)
    projects_mod.init_db()
    return tmp_path


def _write_txt(tmp_path, filename: str, content: bytes) -> str:
    from app.core import file_crypto

    p = str(tmp_path / filename)
    file_crypto.write_document(p, content)
    return p


def test_index_document_zero_chunk_for_empty_supported_file(
    fresh_db, tmp_path, monkeypatch
):
    """A supported .txt file with no extractable text must fail loud."""
    monkeypatch.setenv("RAG_EMBEDDING_MODEL", "fake")
    from app.core import doc_index, projects as projects_mod

    importlib.reload(doc_index)

    proj = projects_mod.create_project("Zero Doc Project")
    doc_path = _write_txt(tmp_path, "empty.txt", b"")
    doc = projects_mod.add_document(
        proj["id"], "empty.txt", file_path=doc_path, size=0
    )

    result = doc_index.index_document(proj["id"], doc["id"])

    assert result["status"] == "error"
    assert result["error"] == "ZERO_CHUNK"
    assert "0 chunks" in result["banner"]
    assert result["document_id"] == doc["id"]


def test_admin_project_reindex_returns_422_when_all_docs_zero_chunk(
    client, fresh_db, tmp_path, monkeypatch
):
    """The admin reindex endpoint must reject a project whose supported docs
    all produce zero chunks."""
    monkeypatch.setenv("RAG_EMBEDDING_MODEL", "fake")
    from app.core import doc_index, projects as projects_mod

    importlib.reload(doc_index)

    proj = projects_mod.create_project("Zero Project")
    doc_path = _write_txt(tmp_path, "empty.txt", b"")
    projects_mod.add_document(proj["id"], "empty.txt", file_path=doc_path, size=0)

    resp = client.post(
        "/v1/admin/debug/project-reindex",
        params={"project_id": proj["id"]},
    )

    assert resp.status_code == 422, resp.text
    text = resp.text
    assert "ZERO_CHUNK" in text
    assert "0 chunks" in text
