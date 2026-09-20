"""Set3 C3: demolition-bill issue date + RFP must come from the later pack.

Live on master_corpus: "On what date was the Demolition and Site Clearance
bill issued, and under which RFP number?" answered from DD-2022-175.

The 2022 pack is a demolition-titled tender. Its filenames contain
"Demolition and Site Clearance", so cosine and the document-identity
filename rescue lock onto that year. The executed 2023 infrastructure
pack carries the same bill inside Volume 4; the filename does not repeat
those words. The date and RFP sit on the BOQ page stamp
(``Date: …`` / ``RFP No. PREFIX-YEAR-SEQ``), which the cover-block
detector never treated as identity evidence. Arrival-order year-lock
then deleted the later pack.

Sanitized fixture. The two PREFIX-YEAR-SEQ ids are the live ones already
in git. The dates are fixture-only (July 10, 2099 / March 4, 2098) and
must never be replaced with live client figures. What is under test is
which pack's stamp reaches the top-k.
"""
from __future__ import annotations

import pytest

from app.core.rag.vector_store import Chunk


ACTIVE = "p_master"
DD23 = "dd23sched"
DD22 = "dd22demo"

C3_ASK = (
    "On what date was the Demolition and Site Clearance bill issued, "
    "and under which RFP number?"
)
LIVE_PREFIX = "Answer only from the client project documents. "
LIVE_C3 = LIVE_PREFIX + C3_ASK

DD23_NAME = (
    "DD-2023-118_the client project II Infrastructure Package 1_"
    "Vol 4 - Schedule (1 of 2).pdf"
)
DD22_NAME = (
    "DD-2022-175 - Site Demolition and Site Clearance Works Package 1 "
    "Volume 4 Schedules - BOQ.pdf"
)

# Live header shape, fixture year. Do not put July 10, 2023 in the repo.
DD23_STAMP = (
    "Date: July 10, 2099 Procurement of Contractor for Example Package 1 "
    "RFP No. DD-2023-118 Classification - Public "
    "PROJECT: EXAMPLE INFRASTRUCTURE PACKAGE 1 BILL OF QUANTITIES "
    "DEMOLITION AND SITE CLEARANCE BILL SECTION d PART Nr. 3"
)
DD22_STAMP = (
    "Date: March 4, 2098 Procurement of Contractor for Demolition Package "
    "RFP No. DD-2022-175 Classification - Public "
    "PROJECT: DEMOLITION AND SITE CLEARANCE WORKS PACKAGE 1 "
    "BILL OF QUANTITIES DEMOLITION AND SITE CLEARANCE BILL SECTION d"
)
DD22_ITEMS = (
    "Demolition and Site Clearance. Page d/3/1. "
    "D110 General site clearance 158 ha 2,500.00 395,000.00"
)

DD23_DATE = "July 10, 2099"
DD22_DATE = "March 4, 2098"


def _chunk(cid, doc_id, score, text, index=0):
    return Chunk(
        chunk_id=cid,
        project_id=ACTIVE,
        doc_id=doc_id,
        chunk_index=index,
        text=text,
        score=score,
    )


@pytest.fixture
def ret(monkeypatch):
    """Live shape: cosine + filename match prefer the 2022 demolition pack.

    The 2023 stamp is indexed but never pooled unless stamp rescue walks
    ``chunks_containing_all``. Filename ``require_all`` cannot see Volume 4.
    """
    from app.core.rag import retriever

    semantic = [
        _chunk("d22s", DD22, 0.91, DD22_STAMP, 2),
        _chunk("d22i", DD22, 0.84, DD22_ITEMS, 40),
    ]
    docs = {
        DD22: [
            _chunk("d22s", DD22, 0.0, DD22_STAMP, 2),
            _chunk("d22i", DD22, 0.0, DD22_ITEMS, 40),
        ],
        DD23: [_chunk("d23s", DD23, 0.0, DD23_STAMP, 3)],
    }
    names = {DD22: DD22_NAME, DD23: DD23_NAME}
    seen = {}

    def fake_filename_terms(pid, terms, *, min_terms=2, require_letter=False,
                            limit=8, require_all=False):
        seen["terms"] = list(terms)
        seen["require_all"] = require_all
        out = []
        for did in (DD22, DD23):
            blob = names[did].lower()
            hits = sum(1 for t in terms if t.lower() in blob)
            if (hits == len(terms)) if require_all else (hits >= min_terms):
                out.append({
                    "id": did,
                    "original_name": names[did],
                    "file_path": "",
                })
        return out[:limit]

    def fake_chunks_for_docs(self, pid, doc_ids, k_per_doc=12, **_kw):
        return [c for d in doc_ids for c in docs.get(d, [])][: k_per_doc * len(doc_ids)]

    def fake_containing_all(self, pid, needles, k=20, **_kw):
        seen.setdefault("needles", list(needles))
        want = [n.lower() for n in needles]
        out = []
        for group in docs.values():
            for chunk in group:
                blob = (chunk.text or "").lower()
                if all(n in blob for n in want):
                    out.append(chunk)
        return out[:k]

    vs = "app.core.rag.vector_store.VectorStore."
    monkeypatch.setattr(
        vs + "search",
        lambda self, pid, qvec, k, query_text=None: semantic[:k],
    )
    monkeypatch.setattr(
        vs + "identifier_search",
        lambda self, pid, identifiers, k=20: [],
    )
    monkeypatch.setattr(vs + "chunks_for_docs", fake_chunks_for_docs)
    monkeypatch.setattr(vs + "chunks_containing_all", fake_containing_all)
    monkeypatch.setattr(vs + "count", lambda self, pid=None: 11)
    monkeypatch.setattr(vs + "_verify_embedding_identity", lambda self: None)
    monkeypatch.setattr(
        retriever, "_doc_name_for_id",
        lambda did: names.get(did, ""), raising=False,
    )
    monkeypatch.setattr(
        "app.core.projects.documents_matching_title_phrase",
        lambda pid, phrase, limit=8: [],
    )
    monkeypatch.setattr(
        "app.core.projects.documents_matching_filename_terms",
        fake_filename_terms,
    )
    monkeypatch.setenv("RAG_EMBEDDING_MODEL", "fake")
    monkeypatch.setenv("RAG_GENERAL_KNOWLEDGE_PROJECTS", "")
    monkeypatch.delenv("MASTER_CORPUS_SOURCE_PROJECT_ID", raising=False)
    monkeypatch.delenv("RAG_DOCUMENT_IDENTITY_RESCUE", raising=False)
    monkeypatch.delenv("RAG_LAYERED", raising=False)
    retriever._seen_c3_lookup = seen
    return retriever


def _top_texts(ret, question=C3_ASK):
    chunks, _ = ret.retrieve_with_filter(question, ACTIVE, k=5)
    return [c.text for c in chunks]


def test_c3_ask_is_frozen():
    """The battery question is the measurement. Do not tidy it."""
    assert C3_ASK == (
        "On what date was the Demolition and Site Clearance bill issued, "
        "and under which RFP number?"
    )


def test_c3_is_a_bill_issue_identity_ask():
    from app.core.rag.retriever import (
        document_identity_title_terms,
        query_asks_for_bill_issue_identity,
        query_asks_for_document_identity,
    )

    assert query_asks_for_document_identity(C3_ASK)
    assert query_asks_for_document_identity(LIVE_C3)
    assert query_asks_for_bill_issue_identity(C3_ASK)
    assert query_asks_for_bill_issue_identity(LIVE_C3)
    assert document_identity_title_terms(C3_ASK) == [
        "bill", "clearance", "demolition", "site",
    ]


@pytest.mark.parametrize(
    "question",
    [
        "What is the Part Summary total for page d/3/1 of the "
        "Demolition and Site Clearance bill?",
        "Which specification document covers the Variation Procedure, "
        "and what is its number?",
        "What is the Time for Completion for the whole of the Works?",
        "Generate a high-level WBS for the demolition and site clearance "
        "scope in this project's BOQ.",
        "What is the document number and revision of the priced Bill of "
        "Quantities, and who prepared it?",
        "What is the date of the priced Bill of Quantities and the "
        "Employer's contract reference on it?",
    ],
)
def test_neighbor_asks_stay_off_the_bill_issue_path(question):
    from app.core.rag.retriever import query_asks_for_bill_issue_identity

    assert not query_asks_for_bill_issue_identity(question), question


def test_a_date_rfp_stamp_is_recognised():
    from app.core.rag.retriever import (
        chunk_states_bill_issue_stamp,
        chunk_states_document_control_block,
    )

    assert chunk_states_bill_issue_stamp(DD23_STAMP)
    assert chunk_states_bill_issue_stamp(DD22_STAMP)
    # A control-block cover is a different shape. Date+RFP alone is not one.
    assert not chunk_states_document_control_block(DD23_STAMP)
    assert not chunk_states_bill_issue_stamp(
        "Demolition and Site Clearance. Page d/3/1. D110 General site clearance."
    )


def test_year_lock_elects_the_later_pack_stamp():
    from app.core.rag.retriever import elect_answer_bearing_contract

    ranked = [
        (DD22_NAME, DD22_STAMP),
        (DD23_NAME, DD23_STAMP),
    ]
    assert elect_answer_bearing_contract(C3_ASK, ranked) == "dd-2023-118"
    assert elect_answer_bearing_contract(LIVE_C3, ranked) == "dd-2023-118"


def test_fence_drops_the_older_demolition_pack():
    from app.core.rag.retriever import _ContractScope

    scope = _ContractScope(
        C3_ASK,
        [(DD22_NAME, DD22_STAMP), (DD23_NAME, DD23_STAMP)],
    )
    assert scope.winning == "dd-2023-118"
    assert scope.allow(DD23_NAME, DD23_STAMP)
    assert not scope.allow(DD22_NAME, DD22_STAMP)


def test_c3_grounds_on_the_2023_stamp_not_the_2022_pack(ret):
    texts = _top_texts(ret)
    assert texts, "C3 retrieved nothing"
    blob = "\n".join(texts)
    assert DD23_DATE in texts[0]
    assert "DD-2023-118" in texts[0]
    assert DD22_DATE not in blob
    assert "DD-2022-175" not in blob


def test_prefixed_live_ask_is_the_same_election(ret):
    texts = _top_texts(ret, LIVE_C3)
    assert texts and DD23_DATE in texts[0]
    assert "RFP No. DD-2023-118" in texts[0]


def test_kill_switch_restores_the_2022_win(ret, monkeypatch):
    monkeypatch.setenv("RAG_DOCUMENT_IDENTITY_RESCUE", "0")
    texts = _top_texts(ret)
    blob = "\n".join(texts)
    assert DD22_DATE in blob
    assert DD23_DATE not in blob
