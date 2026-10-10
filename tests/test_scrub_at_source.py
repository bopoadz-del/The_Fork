"""Scrub-at-source: synthetic rules only. Never a real identifier."""
from __future__ import annotations

import importlib
import logging
import os
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
    with pytest.raises(mod.Refused):
        mod.run("clean", confirm="")
    assert mod.main(["--mode", "clean"]) == 2


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


def test_dryrun_reads_zero_after_clean(scrub_env, capsys):
    _index("syn_shared_gk", "g1", "shared ZXQ9QUORUM row")
    _index("syn_own_proj", "o1", "own ZXQ9QUORUM row")
    mod = _load()
    mod.run("clean", confirm="CLEAN")
    assert mod.run("dryrun")["shared_matches"] == 0
    # The project's own row is untouched and still counted as its own.
    assert mod.run("count")["project_own_matches"] == 1


@pytest.mark.parametrize("python_pat,pg", [
    (r"\bZXQ9\b", r"\yZXQ9\y"),
    (r"\BZXQ9", r"\YZXQ9"),
    (r"ZXQ9\s+Annex(?:\s+\d+)?", r"ZXQ9\s+Annex(?:\s+\d+)?"),
    (r"a\.b", r"a\.b"),
])
def test_rules_translate_to_postgres(python_pat, pg):
    assert _load().pg_pattern(python_pat) == pg


@pytest.mark.parametrize("python_pat", [r"(?P<n>ZXQ9)", r"(?i)ZXQ9", r"ZXQ9\Z", r"ZXQ9++"])
def test_python_only_rules_are_refused_not_approximated(python_pat):
    assert _load().pg_pattern(python_pat) is None


def test_a_replacement_with_a_backreference_is_refused():
    mod = _load()
    assert mod.pg_replacement("the token") == "the token"
    assert mod.pg_replacement(r"\1 ltd") is None


# ── PostgreSQL: the live engine. Runs in the test-postgres CI shards. ──────
_PG = os.getenv("DATABASE_URL", "").startswith("postgresql")


@pytest.fixture
def pg_scrub_env(tmp_path, monkeypatch):
    if not _PG:
        pytest.skip("needs DATABASE_URL on PostgreSQL")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("RAG_EMBEDDING_MODEL", "fake")
    monkeypatch.setenv("RAG_GENERAL_KNOWLEDGE_PROJECTS", "syn_pg_shared_gk")
    monkeypatch.setenv("RAG_SCRUB_IDENTIFIERS", "true")
    monkeypatch.setenv("RAG_SCRUB_RULES", r"\bZXQ9PGMARK\b => the token")
    monkeypatch.delenv("RAG_SCRUB_EXTRA_TERMS", raising=False)
    from app.core.rag import embeddings as _emb, vector_store as _vs
    _emb.reset_embedder_cache()
    _vs.reset_store_cache()
    importlib.reload(importlib.import_module("app.core.identifier_scrub"))
    mod = _load()

    def _no_row_reads(*_a, **_k):
        raise AssertionError("rows were read out of PostgreSQL")
    monkeypatch.setattr(mod, "_local_rows", _no_row_reads)
    yield mod
    from app.core.rag.vector_store import get_store
    store = get_store(dim=_emb.get_embedder().dim)
    for pid, doc in (("syn_pg_shared_gk", "g1"), ("syn_pg_shared_gk", "g2"), ("syn_pg_own", "o1")):
        store.delete_doc(pid, doc)
    _emb.reset_embedder_cache()
    _vs.reset_store_cache()


def test_postgres_clean_runs_in_the_database_and_spares_own_rows(pg_scrub_env):
    from app.core.rag.embeddings import get_embedder
    from app.core.rag.vector_store import get_store

    mod = pg_scrub_env
    _index("syn_pg_shared_gk", "g1", "shared ZXQ9PGMARK row")
    _index("syn_pg_shared_gk", "g2", "shared row without the marker")
    _index("syn_pg_own", "o1", "own ZXQ9PGMARK row")
    store = get_store(dim=get_embedder().dim)
    with store._session_factory()() as session:
        before = list(session.get(store._rag_chunk_cls, "syn_pg_shared_gk:g1:0").embedding)

    counts = mod.run("count")
    assert counts["shared_matches"] == 1 and counts["project_own_matches"] >= 1
    cleaned = mod.run("clean", confirm="CLEAN")
    assert cleaned["shared_rewritten"] == 1 and cleaned["embeddings_updated"] == 1
    assert mod.run("dryrun")["shared_matches"] == 0

    with store._session_factory()() as session:
        shared = session.get(store._rag_chunk_cls, "syn_pg_shared_gk:g1:0")
        plain = session.get(store._rag_chunk_cls, "syn_pg_shared_gk:g2:0")
        own = session.get(store._rag_chunk_cls, "syn_pg_own:o1:0")
        assert shared.text == "shared the token row"
        assert list(shared.embedding) != before
        assert plain.text == "shared row without the marker"
        assert own.text == "own ZXQ9PGMARK row"


def test_postgres_refuses_a_rule_it_cannot_run_exactly(pg_scrub_env, monkeypatch, capsys):
    monkeypatch.setenv("RAG_SCRUB_RULES", r"(?P<n>ZXQ9PGMARK) => the token")
    mod = pg_scrub_env
    _index("syn_pg_shared_gk", "g1", "shared ZXQ9PGMARK row")
    assert mod.main(["--mode", "count"]) == 2
    out = capsys.readouterr().out
    assert "error=rules_unsupported" in out and "rules_unsupported=1" in out
    assert "ZXQ9PGMARK" not in out


def test_count_and_dryrun_never_load_the_embedder(scrub_env, monkeypatch):
    _index("syn_shared_gk", "g1", "shared ZXQ9QUORUM row")
    from app.core.rag import embeddings, vector_store
    vector_store.reset_store_cache()

    def _no_model(*_a, **_k):
        raise AssertionError("count loaded an embedding model")
    monkeypatch.setattr(embeddings, "get_embedder", _no_model)
    monkeypatch.setattr(vector_store, "get_embedder", _no_model)
    mod = _load()
    assert mod.run("count")["shared_matches"] == 1
    assert mod.run("dryrun")["shared_matches"] == 1
