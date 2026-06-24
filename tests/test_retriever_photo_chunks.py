"""Test the photo_chunks BM25 leg of vector_store + retriever (Plan Task 2.7)."""
from __future__ import annotations

import json
import os
import tempfile

import pytest
from sqlalchemy import create_engine, text

from app.core.rag.vector_store import Chunk, VectorStore


@pytest.fixture
def sqlite_url(monkeypatch):
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    url = f"sqlite:///{path}"
    monkeypatch.setenv("DATABASE_URL", url)
    yield url
    try:
        os.unlink(path)
    except OSError:
        pass


@pytest.fixture
def store_with_photos(sqlite_url):
    """SQLite VectorStore with photo_chunks table populated for BM25 testing."""
    engine = create_engine(sqlite_url)
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE photo_chunks (
                chunk_id TEXT PRIMARY KEY,
                project_id TEXT,
                sha256 TEXT NOT NULL,
                caption TEXT NOT NULL,
                photo_metadata TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                UNIQUE(sha256)
            )
        """))
        rows = [
            ("a" * 64, None, "Site photo showing 1 safety issue(s): no_hardhat.",
             json.dumps({"safety_qaqc": [{"class": "no_hardhat", "confidence": 0.9}]})),
            ("b" * 64, None, "Site photo showing 1 QA/QC issue(s): concrete_crack.",
             json.dumps({"safety_qaqc": [{"class": "concrete_crack", "confidence": 0.8}]})),
            ("c" * 64, "proj1", "Site photo showing 1 QA/QC issue(s): concrete_honeycomb.",
             json.dumps({"safety_qaqc": [{"class": "concrete_honeycomb", "confidence": 0.95}]})),
        ]
        for sha, pid, cap, meta in rows:
            conn.execute(
                text("INSERT INTO photo_chunks (chunk_id, project_id, sha256, caption, photo_metadata) "
                     "VALUES (:c, :p, :s, :cap, :m)"),
                {"c": sha, "p": pid, "s": sha, "cap": cap, "m": meta},
            )

    store = VectorStore(db_path=sqlite_url.replace("sqlite:///", ""), dim=256)
    return store


def test_bm25_search_photos_finds_class_name(store_with_photos):
    results = store_with_photos.bm25_search_photos("no hardhat", k=5)
    assert len(results) >= 1
    assert results[0].kind == "photo"
    assert "no_hardhat" in results[0].text


def test_bm25_search_photos_returns_photo_url(store_with_photos):
    results = store_with_photos.bm25_search_photos("concrete crack", k=5)
    assert len(results) >= 1
    sha = "b" * 64
    matched = [r for r in results if r.sha256 == sha]
    assert matched
    assert matched[0].photo_url == f"/v1/photos/{sha}"


def test_bm25_search_photos_project_scope(store_with_photos):
    """When project_id is provided, results include NULL-project photos
    plus the matching-project ones; not other projects' photos."""
    results = store_with_photos.bm25_search_photos("concrete honeycomb", k=5, project_id="proj1")
    found_shas = {r.sha256 for r in results}
    assert ("c" * 64) in found_shas  # proj1's photo

    # Different project shouldn't see proj1's photo, but should still see NULL-project ones
    results_other = store_with_photos.bm25_search_photos("concrete honeycomb", k=5, project_id="proj_other")
    found_shas_other = {r.sha256 for r in results_other}
    assert ("c" * 64) not in found_shas_other


def test_bm25_search_photos_empty_query_returns_empty(store_with_photos):
    assert store_with_photos.bm25_search_photos("", k=5) == []
    assert store_with_photos.bm25_search_photos("   ", k=5) == []


def test_photo_chunk_serializes_with_kind(store_with_photos):
    results = store_with_photos.bm25_search_photos("no hardhat", k=1)
    d = results[0].to_dict()
    assert d["kind"] == "photo"
    assert d["photo_url"] == f"/v1/photos/{'a' * 64}"
    assert "sha256" in d


def test_text_chunk_strips_photo_fields():
    """Plain text chunks default to kind='text' and to_dict drops the
    photo-only fields to keep API payloads small."""
    c = Chunk(chunk_id="x", project_id="p", doc_id="d", chunk_index=0, text="hi", score=0.5)
    d = c.to_dict()
    assert d["kind"] == "text"
    assert "photo_url" not in d
    assert "sha256" not in d
    assert "photo_metadata" not in d
