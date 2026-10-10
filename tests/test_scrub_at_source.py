"""Scrub-at-source: synthetic rules only. Never a real identifier."""
from __future__ import annotations

import importlib
import logging
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

SYNTHETIC_RULES = r"\bZXQ9QUORUM\b => the token"


@pytest.fixture
def scrub_env(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("RAG_EMBEDDING_MODEL", "fake")
    monkeypatch.setenv("RAG_GENERAL_KNOWLEDGE_PROJECTS", "syn_shared_gk")
    monkeypatch.setenv("RAG_SCRUB_IDENTIFIERS", "true")
    monkeypatch.setenv("RAG_SCRUB_RULES", SYNTHETIC_RULES)
    monkeypatch.delenv("RAG_SCRUB_EXTRA_TERMS", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    from app.core.rag import embeddings as _emb, vector_store as _vs
    _emb.reset_embedder_cache()
    _vs.reset_store_cache()
    importlib.reload(importlib.import_module("app.core.identifier_scrub"))
    yield
    _emb.reset_embedder_cache()
    _vs.reset_store_cache()


def _index(pid: str, doc: str, text: str) -> None:
    from app.core.rag.retriever import index_chunks
    assert index_chunks(pid, doc, [text]) == 1


def _load():
    return importlib.import_module("scrub_at_source")


def test_count_splits_shared_and_project_own(scrub_env, capsys):
    _index("syn_shared_gk", "g1", "shared row carries ZXQ9QUORUM once")
    _index("syn_own_proj", "o1", "own row carries ZXQ9QUORUM once")
    _index("syn_own_proj", "o2", "own row has no marker")
    mod = _load()
    counts = mod.run("count")
    assert counts["shared_matches"] == 1
    assert counts["project_own_matches"] == 1
    out = capsys.readouterr().out
    assert "SCRUB_AT_SOURCE" in out
    assert "shared_matches=1" in out
    assert "project_own_matches=1" in out
    assert "ZXQ9QUORUM" not in out
    assert "shared row" not in out


def test_clean_requires_confirm(scrub_env):
    _index("syn_shared_gk", "g1", "ZXQ9QUORUM")
    mod = _load()
    with pytest.raises(SystemExit):
        mod.run("clean", confirm="")


def test_clean_rewrites_shared_only_and_reembeds(scrub_env, capsys):
    from app.core.rag.embeddings import get_embedder
    from app.core.rag.vector_store import get_store

    _index("syn_shared_gk", "g1", "shared ZXQ9QUORUM row")
    _index("syn_own_proj", "o1", "own ZXQ9QUORUM row")
    emb = get_embedder()
    store = get_store(dim=emb.dim)

    mod = _load()
    counts = mod.run("clean", confirm="CLEAN")
    assert counts["shared_rewritten"] == 1
    assert counts["embeddings_updated"] == 1
    out = capsys.readouterr().out
    assert "ZXQ9QUORUM" not in out

    with store._session_factory()() as session:
        shared = session.get(store._rag_chunk_cls, "syn_shared_gk:g1:0")
        own = session.get(store._rag_chunk_cls, "syn_own_proj:o1:0")
        assert shared is not None and own is not None
        assert "ZXQ9QUORUM" not in shared.text
        assert "the token" in shared.text
        assert "ZXQ9QUORUM" in own.text


def test_dryrun_shared_only(scrub_env, capsys):
    _index("syn_shared_gk", "g1", "ZXQ9QUORUM")
    _index("syn_own_proj", "o1", "ZXQ9QUORUM")
    mod = _load()
    counts = mod.run("dryrun")
    assert counts["shared_matches"] == 1
    out = capsys.readouterr().out
    assert "shared_matches=1" in out
    assert "project_own_matches" not in out
    assert "ZXQ9QUORUM" not in out


def test_no_logs_contain_text_or_rules(scrub_env, caplog):
    _index("syn_shared_gk", "g1", "ZXQ9QUORUM secret-looking row")
    mod = _load()
    with caplog.at_level(logging.DEBUG):
        mod.run("count")
    blob = "\n".join(r.getMessage() for r in caplog.records)
    assert "ZXQ9QUORUM" not in blob
    assert r"\bZXQ9QUORUM\b" not in blob
    assert "secret-looking" not in blob
