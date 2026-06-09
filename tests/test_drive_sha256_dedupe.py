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
