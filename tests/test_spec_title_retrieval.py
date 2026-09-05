"""C2: a titled specification already in the corpus must be retrieved.

Live Master Corpus pack C2 (SHA 567147a): "Which specification document
covers the Variation Procedure, and what is its number?" retrieved
DD-2022-175 Demolition Specs Part 3. The governing spec
``DGDAX-DGD-PMO-SPE-012650-1.0 Variation Procedure`` was already in
Neon. Term rescue treated the demolition volume's in-chunk
"specification" / "procedure" overlap as already-grounded. Cosine
preferred the long demolition volume over the short titled spec.

The filename is the discriminator Demolition Specs cannot fake.

Not #483 (A3/A5/A9 unnamed year-lock particulars) and not #490 (D1
letter filename). This fence uses the live C2 ask from
``tests/fixtures/ui_phys/questions.json`` and the live / fixture title
shapes. No live client figures.
"""
from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest

from app.core.rag.vector_store import Chunk


CATALOG = json.loads(
    (Path(__file__).parent / "fixtures" / "ui_phys" / "questions.json")
    .read_text(encoding="utf-8")
)
C2_ASK = CATALOG["cases"]["C2"]["ask"]
C2_ALT = (
    "Which specification section sets out the procedure for variations "
    "and adjustments?"
)

SPEC_NAME_LIVE = "DGDAX-DGD-PMO-SPE-012650-1.0 Variation Procedure.pdf"
SPEC_NAME_FIXTURE = "FX-2044-PMO-SPE-010001-1.0 Variation Procedure.md"
DEMO_NAME = "DD-2022-175 Demolition Specs Part 3.pdf"
VOL2_NAME = "DD-2023-118_DG2 Infra P1_Vol 2 - Specification (8 of 9).pdf"
LIVE_PREFIX = "Answer only from the client project documents. "

SPEC_TEXT = (
    "Document number: DGDAX-DGD-PMO-SPE-012650-1.0 Variation Procedure. "
    "This specification sets out the Variation Procedure for the Works."
)
DEMO_TEXT = (
    "Demolition Specifications Part 3. This specification document "
    "covers demolition procedures, waste handling, and variations "
    "to the Works under the contract."
)
# Live d7a4ca8 C2: cosine cites the CSI heading; the document number is
# the register line in the same volume. No standalone titled upload.
VOL2_SECTION_TEXT = (
    "012650 – Variation and Adjustments\n"
    "SECTION 012650 – VARIATION AND ADJUSTMENTS\n"
    "This specification section covers Variation Order procedures "
    "and adjustments to the Contract Price."
)
VOL2_REGISTER_TEXT = (
    "DGDAX-DGD-PMO-SPE-012650-1.0 Variation Procedure\n"
    "DGDAX-DGD-PMO-SPE-012900-1.0 Payment Procedure"
)

ENGINEER_QUERY = "Who is the Engineer under this contract?"
PARTICULARS_QUERY = (
    "What is the Time for Completion for the whole of the Works?"
)
C3_QUERY = (
    "What scheduling method does Specification 003113 require for programmes?"
)
D1_QUERY = (
    "Who signed the letter about the UBCC Concrete Batching Plant at "
    "Wadi Safar, and in what capacity?"
)

ACTIVE = "p_master"
SPEC_DOC = "spe012650"
DEMO_DOC = "dd2022175"
VOL2_DOC = "vol2spec8"


def _chunk(cid, doc_id, score, text):
    return Chunk(
        chunk_id=cid,
        project_id=ACTIVE,
        doc_id=doc_id,
        chunk_index=0,
        text=text,
        score=score,
    )


def test_c2_catalog_ask_is_frozen():
    """The battery question is the measurement. Do not tidy it."""
    assert C2_ASK == (
        "Which specification document covers the Variation Procedure, "
        "and what is its number?"
    )


def test_c2_query_is_a_spec_title_ask():
    from app.core.rag.retriever import (
        extract_document_title_phrases,
        query_asks_which_specification_document,
    )

    assert query_asks_which_specification_document(C2_ASK)
    assert query_asks_which_specification_document(C2_ALT)
    assert extract_document_title_phrases(C2_ASK) == ["variation procedure"]
    # The measured paraphrase that already worked has no Title-Case title.
    assert extract_document_title_phrases(C2_ALT) == []


def test_contract_and_letter_asks_are_not_spec_title_asks():
    """#483 / #490 / C3 stay off this path."""
    from app.core.rag.retriever import (
        extract_document_title_phrases,
        query_asks_which_specification_document,
    )

    for q in (ENGINEER_QUERY, PARTICULARS_QUERY, C3_QUERY, D1_QUERY):
        assert not query_asks_which_specification_document(q), q
    # D1's site name is Title-Case; the gate is the ask shape, not the phrase.
    for q in (ENGINEER_QUERY, PARTICULARS_QUERY, C3_QUERY):
        assert extract_document_title_phrases(q) == [], q


def test_titled_spec_filename_beats_demolition_specs():
    from app.core.rag.retriever import spec_title_filename_bonus

    phrases = ["variation procedure"]
    assert spec_title_filename_bonus(SPEC_NAME_LIVE, phrases) == 2.0
    assert spec_title_filename_bonus(SPEC_NAME_FIXTURE, phrases) == 2.0
    assert spec_title_filename_bonus(DEMO_NAME, phrases) == 0.0
    assert spec_title_filename_bonus("contract.pdf", phrases) == 0.0


def _install_c2_corpus(monkeypatch, *, spec_in_semantic: bool, rescue_docs=None):
    """Semantic pool is Demolition Specs (and optionally a buried titled spec)."""
    from app.core.rag import retriever as ret

    demo = _chunk("demo", DEMO_DOC, 0.88, DEMO_TEXT)
    spec = _chunk("spec", SPEC_DOC, 0.31, SPEC_TEXT)
    semantic = [demo, spec] if spec_in_semantic else [demo]

    def fake_search(self, project_id, qvec, k, query_text=None):
        return [c for c in semantic if c.project_id == project_id][:k]

    def fake_id_search(self, project_id, identifiers, k=20):
        return []

    def fake_chunks_for_docs(self, project_id, doc_ids, k_per_doc=12):
        if SPEC_DOC in (doc_ids or []):
            return [_chunk("spec", SPEC_DOC, 0.0, SPEC_TEXT)]
        return []

    def fake_containing_all(self, project_id, needles, k=20):
        return []

    names = {
        SPEC_DOC: SPEC_NAME_LIVE,
        DEMO_DOC: DEMO_NAME,
        VOL2_DOC: VOL2_NAME,
    }
    default_matches = [
        {
            "id": SPEC_DOC,
            "original_name": SPEC_NAME_LIVE,
            "file_path": f"Specs/{SPEC_NAME_LIVE}",
        },
    ]
    seeded = rescue_docs if rescue_docs is not None else default_matches

    def fake_title_match(pid, phrase, limit=8):
        needle = (phrase or "").lower()
        out = []
        for doc in seeded:
            blob = f"{doc.get('original_name', '')} {doc.get('file_path', '')}".lower()
            if needle and needle in blob:
                out.append(doc)
        return out[:limit]

    monkeypatch.setattr("app.core.rag.vector_store.VectorStore.search", fake_search)
    monkeypatch.setattr(
        "app.core.rag.vector_store.VectorStore.identifier_search", fake_id_search,
    )
    monkeypatch.setattr(
        "app.core.rag.vector_store.VectorStore.chunks_for_docs", fake_chunks_for_docs,
    )
    monkeypatch.setattr(
        "app.core.rag.vector_store.VectorStore.chunks_containing_all",
        fake_containing_all,
    )
    monkeypatch.setattr(
        "app.core.rag.vector_store.VectorStore.count", lambda self, pid=None: 2,
    )
    monkeypatch.setattr(
        "app.core.rag.vector_store.VectorStore._verify_embedding_identity",
        lambda self: None,
    )
    monkeypatch.setattr(ret, "_doc_name_for_id", lambda did: names.get(did, ""),
                        raising=False)
    monkeypatch.setattr(
        "app.core.projects.documents_matching_title_phrase",
        fake_title_match,
    )
    monkeypatch.setenv("RAG_EMBEDDING_MODEL", "fake")
    monkeypatch.setenv("RAG_GENERAL_KNOWLEDGE_PROJECTS", "")
    monkeypatch.delenv("MASTER_CORPUS_SOURCE_PROJECT_ID", raising=False)
    monkeypatch.delenv("RAG_SPEC_TITLE_RESCUE", raising=False)
    monkeypatch.delenv("RAG_LAYERED", raising=False)
    return ret


def test_c2_retrieves_titled_spec_when_only_demolition_is_in_the_pool(monkeypatch):
    """The live failure: titled spec is in Neon, cosine never fetches it."""
    ret = _install_c2_corpus(monkeypatch, spec_in_semantic=False)
    chunks, _ = ret.retrieve_with_filter(C2_ASK, ACTIVE, k=5)
    names = [getattr(c, "source_name", "") for c in chunks]
    texts = " ".join(c.text for c in chunks)
    assert any("variation procedure" in (n or "").lower() for n in names), names
    assert "DGDAX-DGD-PMO-SPE-012650" in texts
    assert chunks[0].doc_id == SPEC_DOC


def test_c2_lifts_a_buried_titled_spec_already_in_the_pool(monkeypatch):
    ret = _install_c2_corpus(monkeypatch, spec_in_semantic=True)
    chunks, _ = ret.retrieve_with_filter(C2_ASK, ACTIVE, k=5)
    assert chunks[0].doc_id == SPEC_DOC
    assert "DGDAX-DGD-PMO-SPE-012650" in chunks[0].text


def test_kill_switch_restores_demolition_first_when_spec_is_out_of_pool(monkeypatch):
    ret = _install_c2_corpus(monkeypatch, spec_in_semantic=False)
    monkeypatch.setenv("RAG_SPEC_TITLE_RESCUE", "0")
    chunks, _ = ret.retrieve_with_filter(C2_ASK, ACTIVE, k=5)
    assert chunks
    assert chunks[0].doc_id == DEMO_DOC
    assert "DGDAX-DGD-PMO-SPE-012650" not in " ".join(c.text for c in chunks)


def test_mutation_title_bonus_is_what_lifts_the_spec(monkeypatch):
    """If the bonus is a no-op, Demolition Specs wins again — the live ranking."""
    ret = _install_c2_corpus(monkeypatch, spec_in_semantic=True)
    monkeypatch.setattr(ret, "spec_title_filename_bonus", lambda *a, **k: 0.0)
    monkeypatch.setattr(ret, "chunk_states_spec_document_identity", lambda *a, **k: False)
    chunks, _ = ret.retrieve_with_filter(C2_ASK, ACTIVE, k=5)
    assert chunks[0].doc_id == DEMO_DOC


def test_generic_contract_filename_does_not_earn_a_bonus():
    from app.core.rag.retriever import spec_title_filename_bonus

    assert spec_title_filename_bonus(
        "contract.pdf", ["variation procedure"],
    ) == 0.0
    assert spec_title_filename_bonus(
        "DD-2022-175 Demolition Specs Part 3.pdf", ["variation procedure"],
    ) == 0.0


def test_particulars_query_is_not_stolen_by_a_titled_spec(monkeypatch):
    ret = _install_c2_corpus(monkeypatch, spec_in_semantic=False)
    chunks, _ = ret.retrieve_with_filter(PARTICULARS_QUERY, ACTIVE, k=5)
    assert all(c.doc_id != SPEC_DOC for c in chunks)


def test_c3_numbered_spec_is_not_stolen_by_a_titled_spec(monkeypatch):
    ret = _install_c2_corpus(monkeypatch, spec_in_semantic=False)
    chunks, _ = ret.retrieve_with_filter(C3_QUERY, ACTIVE, k=5)
    assert all(c.doc_id != SPEC_DOC for c in chunks)


@pytest.fixture
def project_store(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    import app.core.db as db_mod

    importlib.reload(db_mod)
    from app.core import projects as projects_mod

    pm = importlib.reload(projects_mod)
    pm._initialized = False
    pm.init_db()
    return pm


def test_title_sql_finds_the_variation_procedure_among_demolition_decoys(project_store):
    from app.core.rag.retriever import extract_document_title_phrases

    p = project_store.create_project("C2 corpus")
    pid = p["id"]
    spec = project_store.add_document(
        pid, SPEC_NAME_LIVE, file_path=f"Specs/{SPEC_NAME_LIVE}", size=12,
    )
    project_store.add_document(pid, DEMO_NAME, size=40)
    project_store.add_document(
        pid, SPEC_NAME_FIXTURE, file_path=f"fixtures/{SPEC_NAME_FIXTURE}", size=8,
    )
    project_store.add_document(
        pid, "DD-2022-175 - Volume 1 - Conditions of Contract.pdf", size=9,
    )

    phrases = extract_document_title_phrases(C2_ASK)
    assert phrases == ["variation procedure"]
    found = project_store.documents_matching_title_phrase(pid, phrases[0])
    names = {d["original_name"] for d in found}
    assert SPEC_NAME_LIVE in names
    assert SPEC_NAME_FIXTURE in names
    assert DEMO_NAME not in names
    assert spec["id"] in {d["id"] for d in found}


def test_title_sql_rejects_wildcard_and_short_phrases(project_store):
    p = project_store.create_project("C2 decoys")
    pid = p["id"]
    project_store.add_document(pid, SPEC_NAME_LIVE, size=12)
    assert project_store.documents_matching_title_phrase(pid, "ab cd") == []
    assert project_store.documents_matching_title_phrase(pid, "variation%") == []
    assert project_store.documents_matching_title_phrase(pid, "") == []
    assert project_store.documents_matching_title_phrase(pid, "variation") == []


def test_real_store_cosine_prefers_demolition_until_the_title_bonus(project_store, monkeypatch):
    """Unmocked store: fake cosine ranks the demolition volume first.

    That is the live C2 ranking. The title bonus must invert it.
    """
    from app.core.rag import embeddings as _emb, vector_store as _vs, retriever as ret

    monkeypatch.setenv("RAG_EMBEDDING_MODEL", "fake")
    monkeypatch.setenv("RAG_GENERAL_KNOWLEDGE_PROJECTS", "")
    monkeypatch.delenv("RAG_SPEC_TITLE_RESCUE", raising=False)
    monkeypatch.delenv("RAG_LAYERED", raising=False)
    _emb.reset_embedder_cache()
    _vs.reset_store_cache()
    from app.core.rag.embeddings import Embedder
    from app.core.rag.vector_store import get_store

    e = Embedder(model_name="fake")
    store = get_store(dim=e.dim)
    p = project_store.create_project("C2 real store")
    pid = p["id"]
    spec = project_store.add_document(pid, SPEC_NAME_LIVE, size=12)
    demo = project_store.add_document(pid, DEMO_NAME, size=40)
    store.upsert_chunks(pid, spec["id"], [SPEC_TEXT], e.encode([SPEC_TEXT]))
    store.upsert_chunks(pid, demo["id"], [DEMO_TEXT], e.encode([DEMO_TEXT]))

    monkeypatch.setenv("RAG_SPEC_TITLE_RESCUE", "0")
    baseline, _ = ret.retrieve_with_filter(C2_ASK, pid, k=5)
    assert baseline[0].doc_id == demo["id"], [c.doc_id for c in baseline]

    monkeypatch.delenv("RAG_SPEC_TITLE_RESCUE", raising=False)
    fixed, _ = ret.retrieve_with_filter(C2_ASK, pid, k=5)
    assert fixed[0].doc_id == spec["id"]
    assert "012650" in (fixed[0].source_name or "")
    assert "Variation Procedure" in (fixed[0].source_name or "")


def test_register_line_is_spec_identity_section_heading_is_not():
    from app.core.rag.retriever import chunk_states_spec_document_identity

    phrases = ["variation procedure"]
    assert chunk_states_spec_document_identity(VOL2_REGISTER_TEXT, phrases)
    assert chunk_states_spec_document_identity(SPEC_TEXT, phrases)
    assert not chunk_states_spec_document_identity(VOL2_SECTION_TEXT, phrases)
    assert not chunk_states_spec_document_identity(DEMO_TEXT, phrases)


def _install_c2_vol2_corpus(monkeypatch, *, register_in_semantic: bool):
    """Semantic pool is the CSI section; the register line is the identity."""
    from app.core.rag import retriever as ret

    section = _chunk("sec", VOL2_DOC, 0.91, VOL2_SECTION_TEXT)
    register = _chunk("reg", VOL2_DOC, 0.22, VOL2_REGISTER_TEXT)
    semantic = [section, register] if register_in_semantic else [section]

    def fake_search(self, project_id, qvec, k, query_text=None):
        return [c for c in semantic if c.project_id == project_id][:k]

    def fake_id_search(self, project_id, identifiers, k=20):
        return []

    def fake_chunks_for_docs(self, project_id, doc_ids, k_per_doc=12):
        return []

    def fake_containing_all(self, project_id, needles, k=20):
        blob = " ".join(needles or []).lower()
        if "spe-" in blob and "variation procedure" in blob:
            return [_chunk("reg", VOL2_DOC, 0.0, VOL2_REGISTER_TEXT)]
        return []

    monkeypatch.setattr("app.core.rag.vector_store.VectorStore.search", fake_search)
    monkeypatch.setattr(
        "app.core.rag.vector_store.VectorStore.identifier_search", fake_id_search,
    )
    monkeypatch.setattr(
        "app.core.rag.vector_store.VectorStore.chunks_for_docs", fake_chunks_for_docs,
    )
    monkeypatch.setattr(
        "app.core.rag.vector_store.VectorStore.chunks_containing_all",
        fake_containing_all,
    )
    monkeypatch.setattr(
        "app.core.rag.vector_store.VectorStore.count", lambda self, pid=None: 2,
    )
    monkeypatch.setattr(
        "app.core.rag.vector_store.VectorStore._verify_embedding_identity",
        lambda self: None,
    )
    monkeypatch.setattr(
        ret, "_doc_name_for_id", lambda did: VOL2_NAME if did == VOL2_DOC else "",
        raising=False,
    )
    monkeypatch.setattr(
        "app.core.projects.documents_matching_title_phrase",
        lambda pid, phrase, limit=8: [],
    )
    monkeypatch.setenv("RAG_EMBEDDING_MODEL", "fake")
    monkeypatch.setenv("RAG_GENERAL_KNOWLEDGE_PROJECTS", "")
    monkeypatch.delenv("MASTER_CORPUS_SOURCE_PROJECT_ID", raising=False)
    monkeypatch.delenv("RAG_SPEC_TITLE_RESCUE", raising=False)
    monkeypatch.delenv("RAG_LAYERED", raising=False)
    return ret


def test_c2_elects_the_spe_register_line_over_vol2_section(monkeypatch):
    """d7a4ca8 live miss: Vol 2 Section 012650, not SPE-012650-1.0."""
    ret = _install_c2_vol2_corpus(monkeypatch, register_in_semantic=False)
    chunks, _ = ret.retrieve_with_filter(C2_ASK, ACTIVE, k=5)
    blob = " ".join(c.text for c in chunks)
    assert chunks, "register line never reached the pool"
    assert "DGDAX-DGD-PMO-SPE-012650" in blob
    assert "SPE-012650" in chunks[0].text
    assert "SECTION 012650" not in blob
    assert all(
        ret.chunk_states_spec_document_identity(c.text, ["variation procedure"])
        for c in chunks
    )


def test_c2_live_prefix_still_elects_the_register_line(monkeypatch):
    ret = _install_c2_vol2_corpus(monkeypatch, register_in_semantic=False)
    chunks, _ = ret.retrieve_with_filter(LIVE_PREFIX + C2_ASK, ACTIVE, k=5)
    assert chunks
    assert "DGDAX-DGD-PMO-SPE-012650" in chunks[0].text


def test_c2_drops_the_section_when_the_register_is_already_in_the_pool(monkeypatch):
    ret = _install_c2_vol2_corpus(monkeypatch, register_in_semantic=True)
    chunks, _ = ret.retrieve_with_filter(C2_ASK, ACTIVE, k=5)
    blob = " ".join(c.text for c in chunks)
    assert "SPE-012650" in chunks[0].text
    assert "SECTION 012650" not in blob


def test_chunks_containing_all_finds_the_register_among_section_decoys(project_store, monkeypatch):
    from app.core.rag import embeddings as _emb, vector_store as _vs

    monkeypatch.setenv("RAG_EMBEDDING_MODEL", "fake")
    _emb.reset_embedder_cache()
    _vs.reset_store_cache()
    from app.core.rag.embeddings import Embedder
    from app.core.rag.vector_store import get_store

    e = Embedder(model_name="fake")
    store = get_store(dim=e.dim)
    p = project_store.create_project("C2 identity SQL")
    pid = p["id"]
    vol = project_store.add_document(pid, VOL2_NAME, size=40)
    store.upsert_chunks(
        pid, vol["id"],
        [VOL2_SECTION_TEXT, VOL2_REGISTER_TEXT],
        e.encode([VOL2_SECTION_TEXT, VOL2_REGISTER_TEXT]),
    )
    hits = store.chunks_containing_all(pid, ["SPE-", "variation procedure"], k=8)
    texts = " ".join(c.text for c in hits)
    assert "SPE-012650" in texts
    assert all("SPE-" in (c.text or "") for c in hits)


def test_c2_kill_switch_restores_the_vol2_section(monkeypatch):
    ret = _install_c2_vol2_corpus(monkeypatch, register_in_semantic=False)
    monkeypatch.setenv("RAG_SPEC_TITLE_RESCUE", "0")
    chunks, _ = ret.retrieve_with_filter(C2_ASK, ACTIVE, k=5)
    assert chunks
    assert "SECTION 012650" in chunks[0].text
    assert "DGDAX-DGD-PMO-SPE-012650" not in " ".join(c.text for c in chunks)
