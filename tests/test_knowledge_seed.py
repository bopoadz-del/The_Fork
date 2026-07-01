"""Boot-time seeding of docs/knowledge/*.md into the RAG general-knowledge project."""
import glob
import os

import pytest

from app.core import projects as projects_mod


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setattr(projects_mod, "_initialized", False)
    projects_mod.init_db()
    return tmp_path


def test_seed_knowledge_ingests_and_is_idempotent(fresh_db, monkeypatch):
    monkeypatch.setenv("RAG_GENERAL_KNOWLEDGE_PROJECTS", "training_material")
    # Stub the embedding-backed indexer: record calls instead of running them.
    import app.core.doc_index as di
    calls = []
    monkeypatch.setattr(
        di, "index_document",
        lambda pid, did, **kw: (calls.append((pid, did)), {"indexed": 1})[1],
    )

    from app.core.knowledge_seed import seed_knowledge, KNOWLEDGE_DIR, _gk_project_id
    expected = len(glob.glob(os.path.join(KNOWLEDGE_DIR, "*.md")))
    assert expected >= 2, "expected the units + FIDIC knowledge docs at least"

    seed_knowledge()
    gk = _gk_project_id()
    assert projects_mod.get_project(gk) is not None, "GK project not created"
    docs = projects_mod.list_documents(gk)
    assert len(docs) == expected, f"expected {expected} docs, got {len(docs)}"
    assert len(calls) == expected, "index_document not called for each doc"
    names = {(d.get("original_name") or d.get("name")) for d in docs}
    assert "boq_units_of_measurement.md" in names
    assert "fidic_contracts_red_white.md" in names

    # Second run: idempotent by content sha -> nothing new ingested.
    calls.clear()
    seed_knowledge()
    assert len(projects_mod.list_documents(gk)) == expected
    assert calls == []


def test_seed_knowledge_disabled_when_no_gk_project(fresh_db, monkeypatch):
    monkeypatch.setenv("RAG_GENERAL_KNOWLEDGE_PROJECTS", "")
    from app.core.knowledge_seed import seed_knowledge
    seed_knowledge()  # must be a no-op, never raise
