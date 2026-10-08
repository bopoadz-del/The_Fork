"""A Documents-panel upload is chunked, embedded and retrievable.

The panel posts the file to ``POST /v1/projects/{id}/documents`` as the
signed-in user. Preview reads the stored bytes on its own, so a file can
show its text and still sit at ``chunk_count == 0`` — the badge the panel
renders as "Not indexed" — when that route never hands the file to the
indexer. The formats come from the extractor registry
(``ingest_status.TEXT_BEARING_EXTS``), the same list the route accepts.
"""
from __future__ import annotations

from typing import Any, Dict

import pytest
from fastapi.testclient import TestClient

from app.core import ingest_status as ist
from app.core import projects as store
from app.dependencies import require_user
from app.main import app
from tests.test_ingest_text_only_rule import _sample

USER = {"user_id": "upload-index-user", "role": "user"}
NO_KEY = {"Authorization": "Bearer unused-dependency-is-overridden"}


@pytest.fixture(scope="module")
def client():
    mp = pytest.MonkeyPatch()
    mp.setenv("WARM_MODELS_BLOCKING", "1")
    try:
        with TestClient(app) as c:
            yield c
    finally:
        mp.undo()


@pytest.fixture
def as_user():
    app.dependency_overrides[require_user] = lambda: dict(USER)
    yield
    app.dependency_overrides.pop(require_user, None)


@pytest.fixture(autouse=True)
def _index_stack(monkeypatch):
    """The same embedder the rest of the suite uses, cache cleared per test."""
    monkeypatch.setenv("RAG_EMBEDDING_MODEL", "fake")
    monkeypatch.setenv("INDEX_ON_UPLOAD", "true")
    monkeypatch.delenv("RAG_RETRIEVAL_PROCESS", raising=False)
    from app.core.rag import embeddings as emb
    from app.core.rag import vector_store as vs

    emb.reset_embedder_cache()
    vs.reset_store_cache()
    yield
    emb.reset_embedder_cache()
    vs.reset_store_cache()


def _project() -> str:
    from app.core import users

    users.ensure_user_exists(USER["user_id"])
    return store.create_project(name="Upload index", user_id=USER["user_id"])["id"]


def _word(text: str) -> str:
    """A retrieval query taken from the text the extractor must keep."""
    for word in text.split():
        cleaned = "".join(ch for ch in word if ch.isalnum())
        if len(cleaned) >= 4:
            return cleaned
    return text.strip()


@pytest.mark.parametrize("ext", sorted(ist.TEXT_BEARING_EXTS))
def test_user_upload_of_every_text_format_is_indexed_and_retrievable(
    client, as_user, ext, tmp_path,
):
    """The panel's route, as role=user, for each format the registry reads."""
    import asyncio

    from app.core import doc_index
    from app.core.rag.embeddings import get_embedder
    from app.core.rag.vector_store import get_store

    path, expected = _sample(ext, tmp_path)
    assert path.exists(), ext
    pid = _project()
    r = client.post(
        f"/v1/projects/{pid}/documents",
        files={"file": (f"notes{ext}", path.read_bytes(), "application/octet-stream")},
        headers=NO_KEY,
    )
    assert r.status_code == 201, r.text
    doc_id = r.json()["document"]["id"]

    # The panel badge is the vector-store count the project detail overwrites
    # onto each document (chunk_count === 0 renders "Not indexed"). Read it
    # the way the panel does, after the upload's background ingest has run.
    detail = client.get(f"/v1/projects/{pid}", headers=NO_KEY)
    assert detail.status_code == 200, detail.text
    listed = next(d for d in detail.json()["documents"] if d["id"] == doc_id)
    assert listed["chunk_count"] > 0, (
        f"{ext} stayed at chunk_count={listed.get('chunk_count')!r} "
        f"ingest_status={listed.get('ingest_status')!r}"
    )
    assert listed.get("ingest_status") in ist.INDEXED_STATUSES, listed.get("ingest_status")

    store_ = get_store(dim=get_embedder().dim)
    texts = store_.doc_chunk_texts(pid, [doc_id]).get(doc_id) or []
    blob = "\n".join(texts).lower()
    assert expected.lower() in blob, (ext, texts[:1])

    query = _word(expected)
    hits = asyncio.run(doc_index.search_project_documents(pid, query))
    assert any(h["document_id"] == doc_id for h in hits), (ext, query, hits)
    cited = next(h for h in hits if h["document_id"] == doc_id)
    assert query.lower() in (cited.get("snippet") or "").lower()
