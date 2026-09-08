"""R1: retrieval_visible hides a superseded extract on BOTH hybrid legs.

D1: top-5 must contain the corrected id and not the stale one.
Reversible by flipping the flag. Hard delete is never used.
"""
from __future__ import annotations

import os
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(_HERE)
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

from app.core.rag.embeddings import get_embedder, reset_embedder_cache
from app.core.rag.vector_store import VectorStore, reset_store_cache


PROJECT_ID = "p_vis"
STALE_ID = "b5033ec2"
LIVE_ID = "93982d45"
QUERY = "D1LETTER signatory token"
STALE_TEXT = "D1LETTER signatory token stale extract yours sincerely"
LIVE_TEXT = "D1LETTER signatory token corrected extract yours sincerely"


def _reload(monkeypatch, tmp_path):
    import importlib

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("RAG_EMBEDDING_MODEL", "fake")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    import app.core.db as db_mod
    import app.core.users as users_mod
    from app.core import projects

    importlib.reload(db_mod)
    importlib.reload(users_mod)
    users_mod._initialized = False
    projects._initialized = False
    reset_embedder_cache()
    reset_store_cache()
    return projects, db_mod, users_mod


def _seed_pair(projects, users):
    users.ensure_user_exists("u1")
    projects.create_project(name="Vis", client="C", user_id="u1")
    proj = projects.list_projects("u1")[0]
    pid = proj["id"]
    from app.core.db import SessionLocal
    from app.core.models import Document

    with SessionLocal() as session:
        for did, name in ((STALE_ID, "stale.docx"), (LIVE_ID, "live.docx")):
            session.add(
                Document(
                    id=did,
                    project_id=pid,
                    original_name=name,
                    stored_as=name,
                    file_path=None,
                    doc_type="document",
                    doc_role="other",
                    size=139671,
                    uploaded_at="2026-01-01T00:00:00+00:00",
                    content_sha256="ab" * 32,
                    retrieval_visible=True,
                )
            )
        session.commit()
    return pid


def _index_pair(pid, embedder):
    from app.core.db import get_database_url

    url = get_database_url()
    path = url.replace("sqlite:///", "") if url.startswith("sqlite") else url
    store = VectorStore(db_path=path, dim=embedder.dim)
    store._visibility_ready = None
    for doc_id, text in ((STALE_ID, STALE_TEXT), (LIVE_ID, LIVE_TEXT)):
        vecs = embedder.encode([text])
        store.upsert_chunks(pid, doc_id, [text], vecs)
    store._visibility_ready = None
    return store


@pytest.fixture
def vis_store(monkeypatch, tmp_path):
    projects, _db, users = _reload(monkeypatch, tmp_path)
    projects.init_db()
    pid = _seed_pair(projects, users)
    embedder = get_embedder()
    store = _index_pair(pid, embedder)
    projects.supersede_document(STALE_ID, LIVE_ID)
    store._visibility_ready = None
    yield store, embedder, pid, projects
    store.close()


def _ids(chunks):
    return [c.doc_id for c in chunks]


def test_seed_hides_stale_without_deleting(vis_store):
    store, _emb, pid, projects = vis_store
    stale = projects.get_document(STALE_ID)
    live = projects.get_document(LIVE_ID)
    assert stale is not None
    assert live is not None
    assert stale["superseded_by"] == LIVE_ID
    assert stale["retrieval_visible"] is False
    assert live["retrieval_visible"] is True
    assert projects.get_document(STALE_ID)["id"] == STALE_ID


def test_vector_leg_drops_hidden_doc(vis_store):
    store, embedder, pid, _p = vis_store
    q = embedder.encode([QUERY])[0]
    hits = store._semantic_search(pid, q, k=5)
    ids = _ids(hits)
    assert LIVE_ID in ids
    assert STALE_ID not in ids


def test_bm25_leg_drops_hidden_doc(vis_store):
    store, _emb, pid, _p = vis_store
    hits = store.bm25_search(pid, QUERY, k=5)
    ids = _ids(hits)
    assert LIVE_ID in ids
    assert STALE_ID not in ids


def test_identifier_search_drops_hidden_doc(vis_store):
    store, _emb, pid, _p = vis_store
    hits = store.identifier_search(pid, ["D1LETTER"], k=5)
    ids = _ids(hits)
    assert LIVE_ID in ids
    assert STALE_ID not in ids


def test_d1_hybrid_top5_contains_live_not_stale(vis_store, monkeypatch):
    store, embedder, pid, _p = vis_store
    monkeypatch.setenv("RAG_HYBRID_SEARCH", "true")
    q = embedder.encode([QUERY])[0]
    hits = store.search(pid, q, k=5, query_text=QUERY)
    ids = _ids(hits)
    assert LIVE_ID in ids
    assert STALE_ID not in ids


def test_flag_flip_restores_stale_in_both_legs(vis_store):
    store, embedder, pid, projects = vis_store
    from app.core.db import SessionLocal
    from app.core.models import Document

    with SessionLocal() as session:
        row = session.get(Document, STALE_ID)
        row.retrieval_visible = True
        session.commit()
    store._visibility_ready = None

    q = embedder.encode([QUERY])[0]
    vec_ids = _ids(store._semantic_search(pid, q, k=5))
    bm25_ids = _ids(store.bm25_search(pid, QUERY, k=5))
    assert STALE_ID in vec_ids
    assert STALE_ID in bm25_ids
