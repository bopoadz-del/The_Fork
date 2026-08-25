"""Contract Data Q&A must retrieve the particulars row, not the glossary.

Live S1 Conditions of Contract Q&A (project-assistant) missed filled-in
figures after a 159-chunk reindex:

  * A1 — "Accepted Contract Amount excluding VAT" grounded on the GC
    defined-term glossary ("X means the amount accepted…") and never
    returned the Contract Data row.
  * A2 — digits vs words of the same amount disagreed (chunk split).
  * A5 — retrieval said Contract Data truncated before delay damages.

This fence uses a sanitized North Spur fixture only. No live client names
or live SAR figures.
"""
from __future__ import annotations

import importlib

import pytest

from app.core import file_crypto
from app.core import projects as projects_mod


# Synthetic particulars — fake employer, fake figures, North Spur package.
NORTH_SPUR_EXCL_VAT = "SAR 1,234,567.89"
NORTH_SPUR_EXCL_VAT_WORDS = (
    "Saudi Riyals One Million Two Hundred Thirty-Four Thousand "
    "Five Hundred Sixty-Seven and Eighty-Nine Halalas"
)
NORTH_SPUR_DELAY = "0.1% of the Contract Price per day"
NORTH_SPUR_TFC = "365 calendar days"

_FILLER_ROWS = "\n".join(
    f"1.9.{i} Spare particulars field {i} | Not used"
    for i in range(1, 80)
)

NORTH_SPUR_CONTRACT = f"""
FX-2044-001 — North Spur Package — Conditions of Contract (fixture)

This is a sanitized stand-in. It is not a live client contract.

CONTRACT DATA

Employer | North Spur Water Authority

1.1.1 Accepted Contract Amount excluding VAT | {NORTH_SPUR_EXCL_VAT}
      ({NORTH_SPUR_EXCL_VAT_WORDS})
1.1.1 Accepted Contract Amount including VAT | SAR 1,419,753.07

{_FILLER_ROWS}

1.1.75 Time for Completion for the whole of the Works | {NORTH_SPUR_TFC}

8.8 Delay Damages | {NORTH_SPUR_DELAY}
    Maximum amount of delay damages | 10% of the Accepted Contract Amount

GENERAL CONDITIONS

1.1 Definitions
"Accepted Contract Amount" means the amount accepted in the Letter of
Acceptance for the execution and completion of the Works and the remedying
of any defects. Accepted Contract Amount excluding VAT is a defined term
used throughout these Conditions of Contract. The Contractor shall be paid
the Accepted Contract Amount as adjusted in accordance with the Contract.
"""

GLOSSARY_ONLY = (
    '"Accepted Contract Amount" means the amount accepted in the Letter of '
    "Acceptance for the execution and completion of the Works and the remedying "
    "of any defects. Accepted Contract Amount excluding VAT is a defined term "
    "used throughout the Conditions of Contract. The Contractor shall be paid "
    "the Accepted Contract Amount excluding VAT as adjusted."
)


def test_parser_keeps_excl_vat_digits_and_words_on_one_row():
    from app.core.contract_data_chunks import (
        contract_data_spans,
        parse_contract_data_rows,
    )

    spans = contract_data_spans(NORTH_SPUR_CONTRACT)
    assert spans, "fixture must contain a Contract Data heading"
    section = NORTH_SPUR_CONTRACT[spans[0][0]:spans[0][1]]
    rows = parse_contract_data_rows(section)
    matched = [
        (k, v) for k, v in rows
        if "excluding vat" in k.lower() or "excluding vat" in v.lower()
    ]
    assert matched, f"excl-VAT row missing: {rows[:8]}"
    key, val = matched[0]
    blob = f"{key} {val}"
    assert NORTH_SPUR_EXCL_VAT in blob
    assert "One Million Two Hundred Thirty-Four Thousand" in blob


def test_parser_keeps_delay_damages_rate_on_the_same_row():
    from app.core.contract_data_chunks import (
        contract_data_spans,
        parse_contract_data_rows,
    )

    spans = contract_data_spans(NORTH_SPUR_CONTRACT)
    section = NORTH_SPUR_CONTRACT[spans[0][0]:spans[0][1]]
    rows = parse_contract_data_rows(section)
    matched = [(k, v) for k, v in rows if "delay damages" in k.lower()]
    assert matched, f"delay-damages row missing: {rows[-8:]}"
    blob = " ".join(f"{k} {v}" for k, v in matched)
    assert "0.1%" in blob


def test_word_chunker_splits_the_table_but_particulars_chunker_does_not():
    """The failure mode: 500-word windows cut the table before delay damages.
    The particulars chunker must keep each filled row intact."""
    from app.core.doc_index import chunk_extracted_document, chunk_text

    word_chunks = chunk_text(NORTH_SPUR_CONTRACT, words_per_chunk=500)
    assert word_chunks
    assert NORTH_SPUR_EXCL_VAT in word_chunks[0]
    assert "0.1%" not in word_chunks[0], (
        "fixture is not long enough to reproduce the A5 split; pad filler rows"
    )

    cd_chunks = chunk_extracted_document(
        NORTH_SPUR_CONTRACT,
        chunker="default",
        filename="S1_North_Spur_Conditions_of_Contract.pdf",
    )
    assert cd_chunks
    assert any("CONTRACT DATA particulars" in c for c in cd_chunks)

    excl = [c for c in cd_chunks if NORTH_SPUR_EXCL_VAT in c]
    assert excl, f"excl-VAT figure missing from particulars chunks: {cd_chunks[:2]}"
    assert all(
        "One Million Two Hundred Thirty-Four Thousand" in c for c in excl
    ), "digits vs words of the ACA were split across chunks"

    delay = [c for c in cd_chunks if "0.1%" in c and "delay damages" in c.lower()]
    assert delay, (
        "delay-damages / 0.1% missing — the table was truncated:\n"
        + "\n---\n".join(cd_chunks[:4])
    )
    assert all("CONTRACT DATA particulars" in c for c in delay)

    # Glossary still exists as a separate (non-particulars) chunk.
    glossary = [c for c in cd_chunks if "means the amount accepted" in c]
    assert glossary, "GC definitions should remain retrievable as prose"
    assert not any("CONTRACT DATA particulars" in c for c in glossary)


def test_prose_mention_of_contract_data_is_not_stolen():
    from app.core.doc_index import chunk_extracted_document, chunk_text

    prose = (
        "The Contractor shall complete the Works within the Time for Completion "
        "stated in the Contract Data and shall pay delay damages if it fails.\n"
        "Further obligations follow in the general specification text. " * 20
    )
    cd = chunk_extracted_document(prose, filename="method_statement.txt")
    default = chunk_text(prose)
    assert cd == default


def test_query_detector_particulars_vs_definition_vs_arithmetic():
    from app.core.rag.retriever import query_asks_for_contract_particulars

    assert query_asks_for_contract_particulars(
        "Accepted Contract Amount excluding VAT"
    )
    assert query_asks_for_contract_particulars(
        "What is the delay damages percentage per day in Contract Data?"
    )
    assert query_asks_for_contract_particulars(
        "How many days is the Time for Completion for the whole of the Works?"
    )
    assert not query_asks_for_contract_particulars(
        "What does Accepted Contract Amount mean?"
    )
    assert not query_asks_for_contract_particulars(
        "Convert 1250 AED/m2 to USD/ft2 using 3.6725 AED = 1 USD"
    )
    assert not query_asks_for_contract_particulars("Tell me about the weather")


def test_particulars_delta_lifts_row_and_demotes_glossary():
    from app.core.rag.retriever import contract_data_particulars_delta

    row = (
        "CONTRACT DATA particulars — filled-in amount / duration / percentage.\n"
        "Contract Data\n"
        f"1.1.1 Accepted Contract Amount excluding VAT: {NORTH_SPUR_EXCL_VAT}"
    )
    glossary = GLOSSARY_ONLY
    assert contract_data_particulars_delta(row) > 0
    assert contract_data_particulars_delta(glossary) < 0


@pytest.fixture
def isolated_store(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("RAG_EMBEDDING_MODEL", "fake")
    monkeypatch.setenv("RAG_GENERAL_KNOWLEDGE_PROJECTS", "")
    monkeypatch.delenv("MASTER_CORPUS_SOURCE_PROJECT_ID", raising=False)
    monkeypatch.delenv("RAG_CD_PARTICULARS_BOOST", raising=False)
    from app.core.rag import embeddings as _emb, vector_store as _vs
    _emb.reset_embedder_cache()
    _vs.reset_store_cache()
    from app.core.rag.embeddings import Embedder
    from app.core.rag.vector_store import get_store
    from sqlalchemy import delete as _sa_delete
    e = Embedder(model_name="fake")
    store = get_store(dim=e.dim)
    with store._lock:
        with store._session_factory()() as session:
            session.execute(_sa_delete(store._rag_chunk_cls))
            session.commit()
    yield store, e
    _emb.reset_embedder_cache()
    _vs.reset_store_cache()


def test_retrieval_returns_particulars_row_not_only_defined_term(isolated_store, monkeypatch):
    """A1: the filled-in excl-VAT row must outrank the glossary definition."""
    from app.core.rag import retriever as ret

    store, e = isolated_store
    glossary = GLOSSARY_ONLY
    particulars = (
        "CONTRACT DATA particulars — filled-in amount / duration / percentage "
        "[S1_North_Spur_Conditions_of_Contract.pdf].\n"
        "Contract Data\n"
        f"1.1.1 Accepted Contract Amount excluding VAT: {NORTH_SPUR_EXCL_VAT}\n"
        f"({NORTH_SPUR_EXCL_VAT_WORDS})"
    )
    delay = (
        "CONTRACT DATA particulars — filled-in amount / duration / percentage "
        "[S1_North_Spur_Conditions_of_Contract.pdf].\n"
        "Contract Data\n"
        f"8.8 Delay Damages: {NORTH_SPUR_DELAY}"
    )
    tfc = (
        "CONTRACT DATA particulars — filled-in amount / duration / percentage "
        "[S1_North_Spur_Conditions_of_Contract.pdf].\n"
        "Contract Data\n"
        f"1.1.75 Time for Completion for the whole of the Works: {NORTH_SPUR_TFC}"
    )
    store.upsert_chunks("north_spur", "s1_glossary", [glossary], e.encode([glossary]))
    store.upsert_chunks("north_spur", "s1_aca", [particulars], e.encode([particulars]))
    store.upsert_chunks("north_spur", "s1_delay", [delay], e.encode([delay]))
    store.upsert_chunks("north_spur", "s1_tfc", [tfc], e.encode([tfc]))

    monkeypatch.setattr(ret, "_doc_name_for_id", lambda _id: "S1_North_Spur_Conditions_of_Contract.pdf")

    chunks, _ = ret.retrieve_with_filter(
        "Accepted Contract Amount excluding VAT", "north_spur", k=5,
    )
    assert chunks, "nothing retrieved"
    blob = "\n".join(c.text for c in chunks)
    assert NORTH_SPUR_EXCL_VAT in blob, (
        "particulars figure missing; only the defined term was retrieved:\n"
        f"{blob[:800]}"
    )
    assert chunks[0].text.startswith("CONTRACT DATA particulars"), (
        f"glossary outranked the particulars row:\n{chunks[0].text[:400]}"
    )
    assert "means the amount accepted" not in chunks[0].text

    delay_chunks, _ = ret.retrieve_with_filter(
        "What is the delay damages percentage per day in Contract Data?",
        "north_spur", k=5,
    )
    delay_blob = "\n".join(c.text for c in delay_chunks)
    assert "0.1%" in delay_blob, f"A5 truncation still hiding delay damages:\n{delay_blob[:800]}"

    tfc_chunks, _ = ret.retrieve_with_filter(
        "How many days is the Time for Completion for the whole of the Works?",
        "north_spur", k=5,
    )
    tfc_blob = "\n".join(c.text for c in tfc_chunks)
    assert "365 calendar days" in tfc_blob


def test_definition_question_is_not_forced_onto_particulars(isolated_store, monkeypatch):
    from app.core.rag import retriever as ret

    store, e = isolated_store
    glossary = GLOSSARY_ONLY
    particulars = (
        "CONTRACT DATA particulars — filled-in amount / duration / percentage.\n"
        f"1.1.1 Accepted Contract Amount excluding VAT: {NORTH_SPUR_EXCL_VAT}"
    )
    store.upsert_chunks("north_spur", "s1_glossary", [glossary], e.encode([glossary]))
    store.upsert_chunks("north_spur", "s1_aca", [particulars], e.encode([particulars]))
    monkeypatch.setattr(ret, "_doc_name_for_id", lambda _id: "S1_North_Spur_Conditions_of_Contract.pdf")

    chunks, _ = ret.retrieve_with_filter(
        "What does Accepted Contract Amount mean?", "north_spur", k=5,
    )
    assert chunks
    blob = "\n".join(c.text for c in chunks)
    assert "means the amount accepted" in blob


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setattr(projects_mod, "_initialized", False)
    projects_mod.init_db()
    return tmp_path


def test_index_document_wires_contract_data_rows_into_rag(fresh_db, tmp_path, monkeypatch):
    """index_document must emit intact particulars rows into the corpus."""
    monkeypatch.delenv("DATA_ENCRYPTION_KEY", raising=False)
    monkeypatch.setenv("RAG_EMBEDDING_MODEL", "fake")
    monkeypatch.setenv("RAG_GENERAL_KNOWLEDGE_PROJECTS", "")
    from app.core import doc_index
    importlib.reload(doc_index)
    from app.core.rag import vector_store as _vs
    from app.core.rag.embeddings import reset_embedder_cache
    _vs.reset_store_cache()
    reset_embedder_cache()

    from app.core import users as users_store
    users_store.init_db()
    proj = projects_mod.create_project("North Spur Package")
    pid = proj["id"]
    raw = NORTH_SPUR_CONTRACT.encode("utf-8")
    path = str(tmp_path / "S1_North_Spur_Conditions_of_Contract.txt")
    file_crypto.write_document(path, raw)
    doc = projects_mod.add_document(
        pid,
        "S1_North_Spur_Conditions_of_Contract.txt",
        file_path=path,
        size=len(raw),
    )
    result = doc_index.index_document(pid, doc["id"])
    assert result.get("indexed"), result

    saved = doc_index._load_index(pid)
    chunks = saved["documents"][0]["chunks"]
    blob = "\n".join(chunks)
    assert "CONTRACT DATA particulars" in blob
    assert NORTH_SPUR_EXCL_VAT in blob
    assert "0.1%" in blob
    assert "365 calendar days" in blob

    from app.core.rag.retriever import retrieve_with_filter
    hits, _ = retrieve_with_filter(
        "Accepted Contract Amount excluding VAT", pid, k=5,
    )
    hit_blob = "\n".join(c.text for c in hits)
    assert NORTH_SPUR_EXCL_VAT in hit_blob, (
        "indexed particulars row was not retrieved:\n" + hit_blob[:800]
    )
    assert hits[0].text.startswith("CONTRACT DATA particulars")


def test_contract_data_qa_is_not_stolen_to_calculator_or_wbs():
    """Keep answers as RAG Q&A — do not steal to calculators or WBS."""
    from app.agents.runtime import (
        _looks_like_self_contained_calculation,
        _message_wants_named_calculator,
    )

    questions = (
        "Accepted Contract Amount excluding VAT",
        "What is the delay damages percentage per day in Contract Data?",
        "How many days is the Time for Completion for the whole of the Works?",
    )
    for q in questions:
        assert not _looks_like_self_contained_calculation(q), q
        assert not _message_wants_named_calculator(q), q
