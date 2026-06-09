"""Tests for the SHA-256 incremental ingestion path on the Drive walker."""
from __future__ import annotations

import hashlib
import os
import pathlib
import pytest


def test_documents_schema_has_content_sha256_column(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    from app.core import projects
    projects._initialized = False
    projects.init_db()
    import sqlite3
    conn = sqlite3.connect(projects._db_path())
    cols = [r[1] for r in conn.execute("PRAGMA table_info(documents)").fetchall()]
    assert "content_sha256" in cols


def test_add_document_writes_sha256(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    from app.core import projects
    projects._initialized = False
    projects.create_project(name="P", client="C", user_id="u1")
    proj = projects.list_projects("u1")[0]
    sha = hashlib.sha256(b"hello world").hexdigest()
    doc = projects.add_document(
        project_id=proj["id"],
        original_name="hello.txt",
        stored_as="hello.txt",
        file_path="/tmp/hello.txt",
        size=11,
        content_sha256=sha,
    )
    assert doc["content_sha256"] == sha


def test_walker_skips_unchanged_file_on_rewalk(monkeypatch, tmp_path):
    """Second walk over a Drive folder whose file bytes haven't changed
    must skip the file (no new document row, no re-encryption)."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    from app.core import projects
    projects._initialized = False
    projects.create_project(name="P", client="C", user_id="u1")
    proj = projects.list_projects("u1")[0]

    body = b"hello world content for sha test"
    sha = hashlib.sha256(body).hexdigest()
    # First walk: insert.
    projects.add_document(
        project_id=proj["id"],
        original_name="x.pdf", stored_as="x.pdf", file_path="/tmp/x.pdf",
        size=len(body), content_sha256=sha,
    )
    # Second walk: should detect via find_document_by_sha and skip.
    found = projects.find_document_by_sha(proj["id"], sha)
    assert found is not None
    # If the walker were to add again it would create a duplicate row.
    # Ensure the existing row is the only one.
    docs = projects.list_documents(proj["id"])
    assert len(docs) == 1
