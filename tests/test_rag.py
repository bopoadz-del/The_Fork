"""Tests for the RAG layer (PR 2 — persistent retrieval).

Strategy:
- The fake embedder (deterministic hash → 384-dim) is the workhorse —
  the suite does NOT depend on sentence-transformers being installed.
- The fast (sqlite-vec) and slow (numpy) search paths produce identical
  ordering on toy data; we only assert behavior, not which path was taken,
  so the suite passes whether the C extension is loadable or not.
- DATA_DIR is per-test via the isolated_data_dir fixture (same pattern as
  test_hydration.py and test_router_ml.py). Module-level caches are
  cleared in the fixture so swapping DATA_DIR actually takes effect.
"""

from __future__ import annotations

import os
import time

import numpy as np
import pytest


# ── Fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture
def isolated_data_dir(tmp_path, monkeypatch):
    """Fresh DATA_DIR + reset every RAG cache so a clean store is built."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("RAG_EMBEDDING_MODEL", "fake")
    from app.core.rag import embeddings as _emb, vector_store as _vs
    _emb.reset_embedder_cache()
    _vs.reset_store_cache()
    # Also reset agent_memory / projects so the doc_index path can run
    from app.core import agent_memory as _am, projects as _proj
    if hasattr(_am, "_initialized"):
        _am._initialized = False
    if hasattr(_proj, "_initialized"):
        _proj._initialized = False
    yield tmp_path
    _emb.reset_embedder_cache()
    _vs.reset_store_cache()


# ── Embedder ──────────────────────────────────────────────────────────────


def test_fake_embedder_shape_and_normalization(isolated_data_dir):
    from app.core.rag.embeddings import Embedder, EMBEDDING_DIM

    e = Embedder(model_name="fake")
    vecs = e.encode(["alpha", "beta", "gamma"])
    assert vecs.shape == (3, EMBEDDING_DIM)
    # L2-normalized so cosine = dot product downstream
    norms = np.linalg.norm(vecs, axis=1)
    assert np.allclose(norms, 1.0, atol=1e-5)


def test_fake_embedder_is_deterministic(isolated_data_dir):
    """Same text in → same vector out. Necessary so test assertions are
    stable across runs."""
    from app.core.rag.embeddings import Embedder

    e = Embedder(model_name="fake")
    a = e.encode(["construction"])[0]
    b = e.encode(["construction"])[0]
    assert np.allclose(a, b)


def test_fake_embedder_distinguishes_different_inputs(isolated_data_dir):
    from app.core.rag.embeddings import Embedder

    e = Embedder(model_name="fake")
    v = e.encode(["alpha", "beta"])
    # Hash-based vectors should be very distinct (close to orthogonal)
    cos_sim = float(v[0] @ v[1])
    assert abs(cos_sim) < 0.2, f"Hash vectors too similar: {cos_sim}"


def test_embedder_empty_input(isolated_data_dir):
    """Empty text list returns a (0, dim) array, not an error."""
    from app.core.rag.embeddings import Embedder, EMBEDDING_DIM

    e = Embedder(model_name="fake")
    v = e.encode([])
    assert v.shape == (0, EMBEDDING_DIM)


def test_embedder_get_cache_returns_same_instance(isolated_data_dir):
    from app.core.rag.embeddings import get_embedder, reset_embedder_cache

    a = get_embedder()
    b = get_embedder()
    assert a is b, "get_embedder should cache one instance per process"
    reset_embedder_cache()
    c = get_embedder()
    assert c is not a, "reset_embedder_cache should drop the cached instance"


# ── Vector store ──────────────────────────────────────────────────────────


def test_vector_store_upsert_and_search(isolated_data_dir):
    from app.core.rag.embeddings import Embedder
    from app.core.rag.vector_store import VectorStore

    e = Embedder(model_name="fake")
    s = VectorStore(db_path=str(isolated_data_dir / "vec.db"), dim=e.dim)
    chunks = ["concrete pour schedule", "rebar inventory", "drawing revisions"]
    vecs = e.encode(chunks)
    n = s.upsert_chunks("proj_a", "doc_x", chunks, vecs)
    assert n == 3
    assert s.count("proj_a") == 3

    # Search for the first chunk's exact text — should return it as #1
    query_vec = e.encode(["concrete pour schedule"])[0]
    results = s.search("proj_a", query_vec, k=3)
    assert len(results) == 3
    assert results[0].text == "concrete pour schedule"
    # Cosine of identical vectors = 1.0
    assert results[0].score is not None and results[0].score > 0.99


def test_vector_store_project_isolation(isolated_data_dir):
    """A query against project_a must never return chunks from project_b."""
    from app.core.rag.embeddings import Embedder
    from app.core.rag.vector_store import VectorStore

    e = Embedder(model_name="fake")
    s = VectorStore(db_path=str(isolated_data_dir / "vec.db"), dim=e.dim)
    s.upsert_chunks("proj_a", "doc_x", ["shared text"], e.encode(["shared text"]))
    s.upsert_chunks("proj_b", "doc_y", ["shared text"], e.encode(["shared text"]))

    results = s.search("proj_a", e.encode(["shared text"])[0], k=5)
    assert len(results) == 1
    assert results[0].project_id == "proj_a"
    assert results[0].doc_id == "doc_x"


def test_vector_store_upsert_is_idempotent(isolated_data_dir):
    """Re-indexing the same doc replaces its chunks; total count stable."""
    from app.core.rag.embeddings import Embedder
    from app.core.rag.vector_store import VectorStore

    e = Embedder(model_name="fake")
    s = VectorStore(db_path=str(isolated_data_dir / "vec.db"), dim=e.dim)
    chunks = ["a", "b", "c"]
    s.upsert_chunks("proj_a", "doc_x", chunks, e.encode(chunks))
    s.upsert_chunks("proj_a", "doc_x", chunks, e.encode(chunks))
    s.upsert_chunks("proj_a", "doc_x", chunks, e.encode(chunks))
    assert s.count("proj_a") == 3


def test_vector_store_delete_doc(isolated_data_dir):
    from app.core.rag.embeddings import Embedder
    from app.core.rag.vector_store import VectorStore

    e = Embedder(model_name="fake")
    s = VectorStore(db_path=str(isolated_data_dir / "vec.db"), dim=e.dim)
    s.upsert_chunks("proj_a", "doc_x", ["foo", "bar"], e.encode(["foo", "bar"]))
    s.upsert_chunks("proj_a", "doc_y", ["baz"], e.encode(["baz"]))
    deleted = s.delete_doc("proj_a", "doc_x")
    assert deleted == 2
    assert s.count("proj_a") == 1


def test_vector_store_empty_chunks_deletes_doc(isolated_data_dir):
    """upsert with [] means "this doc has no chunks anymore" — should
    remove any prior chunks for the (project, doc) pair."""
    from app.core.rag.embeddings import Embedder
    from app.core.rag.vector_store import VectorStore

    e = Embedder(model_name="fake")
    s = VectorStore(db_path=str(isolated_data_dir / "vec.db"), dim=e.dim)
    s.upsert_chunks("proj_a", "doc_x", ["text"], e.encode(["text"]))
    n = s.upsert_chunks("proj_a", "doc_x", [], np.zeros((0, e.dim), dtype=np.float32))
    assert n == 0
    assert s.count("proj_a") == 0


def test_vector_store_dim_mismatch_raises(isolated_data_dir):
    from app.core.rag.embeddings import Embedder
    from app.core.rag.vector_store import VectorStore

    e = Embedder(model_name="fake")
    s = VectorStore(db_path=str(isolated_data_dir / "vec.db"), dim=e.dim)
    wrong_dim_vecs = np.zeros((1, e.dim - 1), dtype=np.float32)
    with pytest.raises(ValueError, match="embedding dim"):
        s.upsert_chunks("proj_a", "doc_x", ["text"], wrong_dim_vecs)


def test_search_empty_project_returns_empty(isolated_data_dir):
    from app.core.rag.embeddings import Embedder
    from app.core.rag.vector_store import VectorStore

    e = Embedder(model_name="fake")
    s = VectorStore(db_path=str(isolated_data_dir / "vec.db"), dim=e.dim)
    results = s.search("never_indexed", e.encode(["q"])[0], k=5)
    assert results == []


# ── Retriever ─────────────────────────────────────────────────────────────


def test_retriever_chunk_text_short_input(isolated_data_dir):
    from app.core.rag.retriever import chunk_text

    assert chunk_text("") == []
    assert chunk_text("   ") == []
    short = "small text"
    assert chunk_text(short) == ["small text"]


def test_retriever_chunk_text_long_input_has_overlap(isolated_data_dir):
    """Sliding window: consecutive chunks share `overlap` characters so
    sentences that straddle a window boundary still get indexed somewhere."""
    from app.core.rag.retriever import chunk_text

    text = "a" * 1000
    chunks = chunk_text(text, max_chars=300, overlap=50)
    assert len(chunks) > 1
    assert all(len(c) <= 300 for c in chunks)
    # Last 50 chars of chunk[0] should equal first 50 chars of chunk[1]
    assert chunks[0][-50:] == chunks[1][:50]


def test_retriever_chunk_text_rejects_bad_overlap(isolated_data_dir):
    from app.core.rag.retriever import chunk_text

    with pytest.raises(ValueError, match="overlap"):
        chunk_text("anything", max_chars=10, overlap=20)


def test_retriever_retrieve_happy_path(isolated_data_dir):
    """End-to-end: index_chunks → retrieve → get the same text back."""
    from app.core.rag.retriever import retrieve, index_chunks

    n = index_chunks(
        "proj_x", "doc_1",
        ["foundation concrete pour", "rebar quantities", "steel grade requirements"],
    )
    assert n == 3
    results = retrieve("foundation concrete pour", "proj_x", k=2)
    assert len(results) == 2
    assert results[0].text == "foundation concrete pour"


def test_retriever_requires_project_id(isolated_data_dir):
    from app.core.rag.retriever import retrieve

    with pytest.raises(ValueError, match="project_id"):
        retrieve("anything", "", k=5)


def test_retriever_empty_query_returns_empty(isolated_data_dir):
    from app.core.rag.retriever import retrieve, index_chunks

    index_chunks("proj_x", "doc_1", ["something"])
    assert retrieve("", "proj_x") == []
    assert retrieve("   ", "proj_x") == []


def test_retriever_unindexed_project_returns_empty(isolated_data_dir):
    """No crash on a project that's never seen index_chunks."""
    from app.core.rag.retriever import retrieve

    assert retrieve("anything", "never_seen") == []


# ── doc_index integration ────────────────────────────────────────────────


def test_doc_index_writes_embeddings(isolated_data_dir):
    """The doc indexer's RAG hook: after chunking, chunks get embedded.

    We don't fully exercise the doc indexer (it needs an uploaded
    document); instead we directly assert the hook code path. The
    fingerprint-based skip is already covered by tests in test_hydration.py.
    """
    from app.core.rag.retriever import index_chunks, retrieve

    # Simulate what doc_index.index_document does after chunking
    chunks = ["section 1 text", "section 2 text"]
    indexed = index_chunks("proj_doc", "doc_abc", chunks)
    assert indexed == 2

    results = retrieve("section 1 text", "proj_doc", k=1)
    assert results[0].text == "section 1 text"
    assert results[0].doc_id == "doc_abc"


# ── chat block opt-in ────────────────────────────────────────────────────


def _chat_capture_fixture(monkeypatch, captured: dict):
    """Patch ChatBlock._call_cloud on the class with an async stub that
    captures the message. The signature must include `self` because we're
    patching at class level — when called on an instance, Python passes
    self as the first argument."""
    from app.blocks.chat import ChatBlock

    async def fake_call(self, message, model, max_tokens, temperature, stream, key, cfg=None, **kwargs):
        captured["message"] = message
        return {"status": "success", "response": "ok"}
    monkeypatch.setattr(ChatBlock, "_call_cloud", fake_call)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "fake-key")


@pytest.mark.asyncio
async def test_chat_use_rag_false_by_default(isolated_data_dir, monkeypatch):
    """Default chat behavior is unchanged when use_rag is absent."""
    from app.blocks.chat import ChatBlock
    from app.core.rag.retriever import index_chunks

    # Index something so retrieval WOULD have results if it ran
    index_chunks("proj_chat", "doc1", ["this is the indexed document text"])

    captured: dict = {}
    _chat_capture_fixture(monkeypatch, captured)

    cb = ChatBlock()
    await cb.process({"text": "hello"})
    assert captured["message"] == "hello", (
        "Without use_rag, the message must reach the LLM unmodified"
    )


@pytest.mark.asyncio
async def test_chat_use_rag_true_injects_context(isolated_data_dir, monkeypatch):
    """When use_rag=True + project_id given, top-k chunks prepend the message."""
    from app.blocks.chat import ChatBlock
    from app.core.rag.retriever import index_chunks

    index_chunks(
        "proj_chat", "doc1",
        ["foundation rebar spec is grade 60", "concrete pour scheduled Tuesday"],
    )

    captured: dict = {}
    _chat_capture_fixture(monkeypatch, captured)

    cb = ChatBlock()
    await cb.process(
        {"text": "foundation rebar spec is grade 60", "use_rag": True, "project_id": "proj_chat"},
        {"rag_k": 2},
    )
    assert "Relevant project context:" in captured["message"]
    assert "foundation rebar spec is grade 60" in captured["message"]
    assert "User question:" in captured["message"]


@pytest.mark.asyncio
async def test_chat_use_rag_with_no_indexed_chunks_unchanged(isolated_data_dir, monkeypatch):
    """use_rag=True on an empty project must not inject an empty context
    block — the message should reach the LLM unmodified."""
    from app.blocks.chat import ChatBlock

    captured: dict = {}
    _chat_capture_fixture(monkeypatch, captured)

    cb = ChatBlock()
    await cb.process({"text": "what's up", "use_rag": True, "project_id": "no_chunks"})
    assert captured["message"] == "what's up"


# ── HTTP route ───────────────────────────────────────────────────────────


def test_rag_search_route_happy_path(isolated_data_dir, monkeypatch):
    """POST /v1/rag/search returns chunks + metadata, scoped by project."""
    from fastapi.testclient import TestClient
    from app.main import app
    from app.core.rag.retriever import index_chunks

    # Auth: tests typically use a dev API key. Check the env the auth
    # router reads; fall back to bypassing if no key needed in test mode.
    # The auth router accepts the built-in dev key "cb_dev_key" via Bearer.

    index_chunks("proj_route", "doc1", ["alpha beta gamma", "delta epsilon"])

    with TestClient(app) as client:
        r = client.post(
            "/v1/rag/search",
            json={"query": "alpha beta gamma", "project_id": "proj_route", "k": 2},
            headers={"Authorization": "Bearer cb_dev_key"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["available"] is True
        assert body["count"] >= 1
        assert body["chunks"][0]["text"] == "alpha beta gamma"


def test_rag_search_route_validates_query(isolated_data_dir, monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app

    # The auth router accepts the built-in dev key "cb_dev_key" via Bearer.
    with TestClient(app) as client:
        r = client.post(
            "/v1/rag/search",
            json={"query": "", "project_id": "proj_route"},
            headers={"Authorization": "Bearer cb_dev_key"},
        )
        # Pydantic rejects empty query (min_length=1)
        assert r.status_code == 422


def test_rag_search_route_unknown_project_returns_empty(isolated_data_dir, monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app

    # The auth router accepts the built-in dev key "cb_dev_key" via Bearer.
    with TestClient(app) as client:
        r = client.post(
            "/v1/rag/search",
            json={"query": "anything", "project_id": "never_existed", "k": 5},
            headers={"Authorization": "Bearer cb_dev_key"},
        )
        assert r.status_code == 200
        assert r.json()["count"] == 0


# ── Graceful degradation when libs aren't installed ──────────────────────


def test_retrieve_returns_empty_when_embedder_unavailable(monkeypatch, isolated_data_dir):
    """If sentence-transformers can't be imported AND the fake mode isn't
    requested, retrieve() returns [] silently. This is the "RAG isn't
    installed; chat still works" contract."""
    from app.core.rag import embeddings as _emb
    from app.core.rag import retriever as _r

    monkeypatch.setattr(_emb.Embedder, "available", staticmethod(lambda: False))
    _emb.reset_embedder_cache()
    monkeypatch.setattr(_r, "available", lambda: False)

    assert _r.retrieve("anything", "any_project", k=5) == []
    assert _r.index_chunks("any_project", "doc1", ["x"]) == 0
