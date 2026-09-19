"""Any Contract Data row the question names is fetched -- not only five of them.

Live on b64bbd2, project master_corpus, three runs each, all 0/3:

    "What is the value of the Performance Bond?"
    "How many Milestones are there and what is the Time for Completion for
     Milestone 5?"
    "What is the approved method of electronic communication under the
     contract?"

Every answer was an honest "not in the retrieved excerpts". Every answer IS in
the corpus, in a correctly labelled ``CONTRACT DATA particulars`` chunk:

    4.3.3(a): | Value of Performance Bond: 10 % of the Accepted Contract Amount
    |: | | Milestone 5 | 731days from the date the Contractor is given ...
    electronic communication: Aconex

Asked WITH the words "Contract Data" in the question those chunks score 5.0
and take all five slots. Asked the way a person asks, they are not in the
result at all -- a volume's table-of-contents page and a drawing title block
win instead.

The cause is the shape of the rescue, not any one row. A scanned Contract
Data table embeds badly, so cosine never puts it in the candidate pool, and
every bonus downstream only re-scores what is already in the pool. The
out-of-pool fetch that fixes this existed -- for the Accepted Contract Amount,
Time for Completion, delay damages, the Defects Notification Period, the
Engineer, the Parent Company Guarantee and the Commencement Date. Seven rows,
each added after that row failed live. A Contract Data sheet has about sixty.

These tests pin the CLASS: a question that names a filled Contract Data row
gets that row, whichever row it is.
"""
from __future__ import annotations

import pytest

from app.core.rag.vector_store import Chunk

ACTIVE = "p_master"
CD_DOC = "cdreal"
TOC_DOC = "vol4"
GC_DOC = "gc"
DRAWING_DOC = "dwg"

CD_NAME = "XX-2099-001_Vol 1.0_Cond of Contract (complete)_Contract Data.pdf"
LABEL = (
    "CONTRACT DATA particulars — filled-in amount / duration / percentage "
    f"[{CD_NAME}].\nCONTRACT DATA\n"
)

# Row shapes copied from the live index; every name and figure is synthetic.
CD_BOND = LABEL + (
    "|: | Example Signatory (signatory@example.com) | |\n"
    "3.1: | The Engineer's duties: as set out in the Conditions | |\n"
    "| 4.3.3(a): | Value of Performance Bond: 10 % of the Accepted Contract Amount | |\n"
    "4.3.7: | Parent Company Guarantee Required: No | |\n"
)
CD_MILESTONE_LIST = LABEL + (
    "1.1.50: | | Milestones (if applicable) | Milestone 1 | North Quarter 1a\n"
    "|: | | Milestone 2 | North Quarter 1b\n"
    "|: | | Milestone 3 | South Quarter 2a\n"
    "|: | | Milestone 4 | South Quarter 2b\n"
    "|: | | Milestone 5 | East Quarter 3a\n"
    "|: | | Milestone 6 | East Quarter 3b\n"
    "1.1.67: | | Sections (if applicable) | Not applicable |\n"
)
CD_MILESTONE_TIMES = LABEL + (
    "1.1.75: | | Time for Completion (for\n"
    "the whole of the Works): 900 days from the Commencement Date |\n"
    "1.1.75: | | Time for Completion (by\n"
    "Milestone, if applicable): Milestone 1 | 300 days from the date the "
    "Contractor is given right of access to North Quarter 1a.\n"
    "|: | | Milestone 4 | 480days from the date the Contractor is given right "
    "of access to South Quarter 2b.\n"
    "|: | | Milestone 5 | 640days from the date the Contractor is given right "
    "of access to East Quarter 3a.\n"
)
CD_COMMS = LABEL + (
    "1.1.78: | Nominated Subcontractor | Notapplicable | |\n"
    "1.3.1(a): | Approved methods of\n"
    "electronic communication: ExampleDocs | |\n"
)
CD_INSURANCE = LABEL + (
    "18.2: | Deductible per occurrence: SAR 250,000 | |\n"
    "18.3: | Minimum third party insurance: SAR 20,000,000 | |\n"
)
CD_CHUNKS = (CD_BOND, CD_MILESTONE_LIST, CD_MILESTONE_TIMES, CD_COMMS, CD_INSURANCE)

# What actually won the live top-5.
TOC_PAGE = (
    "Contract No. XX-2099-001 Construction Contract between Example Employer "
    "and Example Contractor Volume 4 of 6 Schedules (2 of 2) TABLE OF CONTENTS "
    "VOLUME 1 CONTRACT AGREEMENT CONTRACT DATA CONDITIONS OF CONTRACT VOLUME 4 "
    "SCHEDULE 8 FORM OF PERFORMANCE BOND SCHEDULE 5 MILESTONES"
)
GC_BOND_CLAUSE = (
    "deliver to the Employer a duly executed Advance Payment Bond and a duly "
    "executed Performance Bond. The Advance Payment Bond and Performance Bond "
    "shall both be payable on demand, unconditional and irrevocable, in the "
    "amounts stated in the Contract Data."
)
GC_NOTICES_CLAUSE = (
    "1.3 Communications. Wherever these Conditions provide for the giving of "
    "notices, these communications shall be in writing and delivered by hand, "
    "or transmitted using any of the approved systems of electronic "
    "transmission as stated in the Contract Data."
)
GC_MILESTONE_DEFINITION = (
    "1.1.50 “Milestone” shall have the meaning given to such term in Schedule 5 "
    "[Project Schedule]. The Contractor shall complete each Milestone within "
    "the Time for Completion stated in the Contract Data."
)
DRAWING_TITLE = (
    "INFRASTRUCTURE DESIGN P.O. Box 000 Example Consultant DATUM ORIENTATION "
    "GEODETIC DATUM: WGS-84 PROJECT SYSTEM: UTM GENERAL ARRANGEMENT"
)
SPEC_CONCRETE = (
    "03 30 00 Cast-in-place concrete. The minimum compressive strength of "
    "concrete for culverts shall be 40 MPa at 28 days. Testing shall be "
    "carried out by an approved laboratory."
)

A7 = "What is the value of the Performance Bond?"
A4 = ("How many Milestones are there and what is the Time for Completion for "
      "Milestone 5?")
A8 = "What is the approved method of electronic communication under the contract?"


def _chunk(cid, doc_id, score, text, index=0):
    return Chunk(chunk_id=cid, project_id=ACTIVE, doc_id=doc_id,
                 chunk_index=index, text=text, score=score)


def _cd_chunks():
    return [_chunk(f"cd{i}", CD_DOC, 0.0, text, index=i)
            for i, text in enumerate(CD_CHUNKS)]


@pytest.fixture
def ret(monkeypatch):
    """The live shape: cosine returns everything EXCEPT the Contract Data."""
    from app.core.rag import retriever

    semantic = [
        _chunk("toc", TOC_DOC, 0.74, TOC_PAGE),
        _chunk("gcbond", GC_DOC, 0.73, GC_BOND_CLAUSE, index=1),
        _chunk("gcnotice", GC_DOC, 0.73, GC_NOTICES_CLAUSE, index=2),
        _chunk("gcms", GC_DOC, 0.72, GC_MILESTONE_DEFINITION, index=3),
        _chunk("dwg", DRAWING_DOC, 0.71, DRAWING_TITLE),
        _chunk("spec", "spec", 0.70, SPEC_CONCRETE),
    ]
    names = {CD_DOC: CD_NAME, TOC_DOC: "Vol 4 - Schedule (2 of 2).pdf",
             GC_DOC: "conditions_of_contract.pdf", DRAWING_DOC: "GA-001.pdf",
             "spec": "033000 Concrete.pdf"}

    def fake_search(self, project_id, qvec, k, query_text=None):
        return [c for c in semantic if c.project_id == project_id][:k]

    def fake_chunks_for_docs(self, project_id, doc_ids, k_per_doc=12, **_kw):
        return _cd_chunks() if CD_DOC in (doc_ids or []) else []

    def fake_title_phrase(pid, phrase, limit=8):
        if "contract data" in (phrase or "").lower():
            return [{"id": CD_DOC, "original_name": CD_NAME, "file_path": ""}]
        return []

    vs = "app.core.rag.vector_store.VectorStore."
    monkeypatch.setattr(vs + "search", fake_search)
    monkeypatch.setattr(vs + "identifier_search",
                        lambda self, pid, identifiers, k=20: [])
    monkeypatch.setattr(vs + "chunks_for_docs", fake_chunks_for_docs)
    monkeypatch.setattr(vs + "chunks_containing_all",
                        lambda self, pid, needles, k=20, **_kw: [])
    monkeypatch.setattr(vs + "count", lambda self, pid=None: 11)
    monkeypatch.setattr(vs + "_verify_embedding_identity", lambda self: None)
    monkeypatch.setattr(retriever, "_doc_name_for_id",
                        lambda did: names.get(did, ""), raising=False)
    monkeypatch.setattr("app.core.projects.documents_matching_title_phrase",
                        fake_title_phrase)
    monkeypatch.setenv("RAG_EMBEDDING_MODEL", "fake")
    monkeypatch.setenv("RAG_GENERAL_KNOWLEDGE_PROJECTS", "")
    monkeypatch.delenv("MASTER_CORPUS_SOURCE_PROJECT_ID", raising=False)
    monkeypatch.delenv("RAG_NAMED_PARTICULARS_ROW_RESCUE", raising=False)
    monkeypatch.delenv("RAG_LAYERED", raising=False)
    return retriever


def _texts(ret, question):
    chunks, _ = ret.retrieve_with_filter(question, ACTIVE, k=5)
    return [c.text for c in chunks]


def test_a7_the_performance_bond_row_leads(ret):
    texts = _texts(ret, A7)
    assert texts, "nothing retrieved"
    assert "Value of Performance Bond: 10 %" in texts[0]


def test_a4_the_milestone_5_duration_is_retrieved(ret):
    blob = "\n".join(_texts(ret, A4))
    assert "Milestone 5 | 640days" in blob


def test_a4_the_milestone_list_is_retrieved_too(ret):
    """The question has two halves; "how many" is answered by the list."""
    blob = "\n".join(_texts(ret, A4))
    assert "Milestones (if applicable)" in blob


def test_a8_the_electronic_communication_row_leads(ret):
    """Not a figure, and not a field any regex names. Still a filled row."""
    texts = _texts(ret, A8)
    assert texts
    assert "electronic communication: ExampleDocs" in texts[0]


@pytest.mark.parametrize(
    "question, expected",
    [
        ("What is the deductible per occurrence?", "Deductible per occurrence"),
        ("What is the minimum third party insurance?", "third party insurance"),
        ("Who is the Nominated Subcontractor?", "Nominated Subcontractor"),
    ],
)
def test_a_row_nobody_has_asked_about_yet_is_rescued(ret, question, expected):
    """Shape-invariance: rows that have never failed live, so no special case
    exists for them and none should be needed."""
    assert expected in "\n".join(_texts(ret, question))


def test_the_rescue_does_not_flood_the_top_k(ret):
    """It fetches the rows the question names, not the Contract Data file."""
    texts = _texts(ret, A7)
    rescued = [t for t in texts if t.startswith("CONTRACT DATA particulars")]
    assert 1 <= len(rescued) <= 2
    assert not any("Deductible per occurrence" in t for t in texts)


def test_a_label_repeated_on_every_page_still_takes_two_slots_at_most(
    ret, monkeypatch,
):
    """A bound volume repeats its running header; five windows that all match
    must not push every other source out of a five-slot answer."""
    pages = [
        _chunk(f"page{i}", CD_DOC, 0.0,
               CD_BOND + f"Page {i} of 46 | Volume 1\n", index=i)
        for i in range(5)
    ]
    monkeypatch.setattr(
        "app.core.rag.vector_store.VectorStore.chunks_for_docs",
        lambda self, pid, doc_ids, k_per_doc=12, **_kw: pages,
    )
    texts = _texts(ret, A7)
    assert sum(t.startswith("CONTRACT DATA particulars") for t in texts) == 2


@pytest.mark.parametrize(
    "question",
    [
        "What is the minimum compressive strength of concrete for culverts?",
        "Which laboratory carries out concrete testing?",
        # One shared word ("Milestone") is a coincidence, not a named row.
        "Who attended the milestone review workshop?",
        # Half the question's words are in the chunk, but never two on one
        # row: "insurance" is on a row, "register" is on none.
        "What is the insurance register?",
        # Two words on one row ("third party"), in a question about
        # something else entirely.
        "Who attended the third party barbecue on Friday evening?",
    ],
)
def test_a_question_that_names_no_row_gets_no_contract_data(ret, question):
    """The control. A rescue that fired on any shared word would put a
    Contract Data chunk into every answer on the platform."""
    assert not any(t.startswith("CONTRACT DATA particulars")
                   for t in _texts(ret, question))


def test_a_definition_question_stays_on_the_glossary(ret):
    assert not any(
        t.startswith("CONTRACT DATA particulars")
        for t in _texts(ret, "What does Performance Bond mean?")
    )


def test_an_unfilled_row_is_not_an_answer(ret, monkeypatch):
    """A key with nothing beside it names the row and states nothing."""
    blank = LABEL + "4.3.3(a): | Value of Performance Bond | |\n"
    monkeypatch.setattr(
        "app.core.rag.vector_store.VectorStore.chunks_for_docs",
        lambda self, pid, doc_ids, k_per_doc=12, **_kw: [
            _chunk("blank", CD_DOC, 0.0, blank)],
    )
    assert not any(t.startswith("CONTRACT DATA particulars")
                   for t in _texts(ret, A7))


def test_the_rest_of_a_bare_label_is_not_its_value(ret):
    """``Time for Completion for the whole of the Works`` with no cell beside
    it: the words after "whole" are still the label. Caught by the year-lock
    suite, which elects a contract on exactly this predicate — a bare key in
    an older contract's window must not take the pool from the newer one."""
    bare = LABEL + (
        "1.1.1 Accepted Contract Amount including VAT: SAR 1,000,000.00\n"
        "1.1.75 Time for Completion for the whole of the Works\n"
        "8.8 Delay Damages for the whole of the Works: 0.1% per day\n"
    )
    ask = "What is the Time for Completion for the whole of the Works?"
    assert ret.named_particulars_row_match(ask, bare) == 0
    # ...while the row below it, which IS filled, still counts for its own ask.
    assert ret.named_particulars_row_match(
        "What are the Delay Damages for the whole of the Works?", bare) > 0


def test_a_key_wrapped_over_two_lines_is_one_row(ret):
    assert ret.named_particulars_row_match(A4, CD_MILESTONE_TIMES) > 0


def test_kill_switch_restores_the_old_ranking(ret, monkeypatch):
    monkeypatch.setenv("RAG_NAMED_PARTICULARS_ROW_RESCUE", "0")
    assert not any(t.startswith("CONTRACT DATA particulars")
                   for t in _texts(ret, A8))
