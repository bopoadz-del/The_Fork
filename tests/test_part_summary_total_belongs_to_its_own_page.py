"""A page total belongs to the page printed under it -- and to no other.

Live on b64bbd2, three runs, 0/3:

    "What is the Part Summary total for page d/3/1 of the Demolition and
     Site Clearance bill?"

The figure is in the index, in one chunk, next to its page number. It was not
retrieved, and the reason is worse than a ranking miss.

A scanned bill prints each page like this, and the chunker cuts it like this:

    ...last item  To Part Summary  <stamp noise>  SAR 1,234,567.00
    Date: ...  RFP No. ...  Classification - Public  Page d/3/1  124 of 675
    PROJECT: ...  BILL SECTION d  PART Nr. 3  PAGE Nr. d/3/2  Item Description

Three things follow from that shape, and the code had all three wrong:

1. The page number sits ~170 characters AFTER the total. The composer looked
   80 characters ahead, never saw it, and treated the total as unlabelled.
2. An unlabelled total was accepted as the asked page. With 675 pages, dozens
   of footers have an unreadable page number; every one of them tied at the
   same score as the right chunk, and a lone one would have been STATED to
   the user as "the d/3/1 total".
3. ``PAGE Nr. d/3/2`` on the same chunk is the NEXT page's header. It made
   the d/3/1 total count as evidence for d/3/2 as well.

And OCR glues the footer: ``Paged/3/12`` has no word boundary before the
``d``, so it was read as "no page number" -- and then accepted as d/3/1.
"""
from __future__ import annotations

import pytest

from app.core.rag.retriever import (
    chunk_states_part_summary_total,
    compose_part_summary_total,
)
from app.core.rag.vector_store import Chunk

ASK = ("What is the Part Summary total for page d/3/1 of the Demolition and "
       "Site Clearance bill?")
ASK_NEXT_PAGE = ASK.replace("d/3/1", "d/3/2")
ASK_PAGE_12 = ASK.replace("d/3/1", "d/3/12")

FOOTER = (
    "Date: July 10, 2099 Procurement of Contractor for Example Package 1 "
    "RFP No. XX-2099-001 Classification - Public "
)
HEADER = (
    "PROJECT: EXAMPLE INFRASTRUCTURE PACKAGE 1 BILL OF QUANTITIES DEMOLITION "
    "AND SITE CLEARANCE BILL SECTION d PART Nr. 3 "
)

# The real cut: total, then the footer naming ITS page, then the next header.
PAGE_1 = (
    "scape areas D290.2 Nr 1,239 1,185.00 1,468,215.00 liy jlu 4 J !l r "
    "To Part Summary U,. .Io. 1 K'l.A SAR 1,234,567.00 "
    + FOOTER + "Page d/3/1 124 of 675 " + HEADER + "PAGE Nr. d/3/2 Item Description"
)
PAGE_2 = (
    "D599.4 m 36.00 Rate only 1,852,848.00 To Part Summary SAR 7,654,321.00 "
    + FOOTER + "Page d/3/2 125 of 675 " + HEADER + "PAGE Nr. d/3/3 Item Description"
)
# OCR glued the footer. Still page 12, and still not page 1.
PAGE_12_GLUED = (
    "D999.48 m 825 364.00 300,300.00 To Part Summary SAR Closed "
    "9,999,999.00 134 of 675 RFP No. XX-2099-001 Classification - Public "
    "Paged/3/12 Date: July 10, 2099"
)
# The page number did not survive the scan at all.
UNLABELLED = (
    "A 250.26 J Coating Stripping Test; ASTM D 1664. "
    "To Part Summary SAR 2,717.00 C.R. stamp noise"
)


# ── which page does a total belong to ─────────────────────────────────────

def test_the_real_chunk_is_evidence_for_its_own_page():
    assert chunk_states_part_summary_total(PAGE_1, ["d/3/1"])


def test_the_next_pages_header_is_not_this_pages_total():
    """``PAGE Nr. d/3/2`` is printed above the items that FOLLOW."""
    assert not chunk_states_part_summary_total(PAGE_1, ["d/3/2"])
    assert chunk_states_part_summary_total(PAGE_2, ["d/3/2"])


def test_a_glued_footer_is_still_a_page_number():
    assert chunk_states_part_summary_total(PAGE_12_GLUED, ["d/3/12"])
    assert not chunk_states_part_summary_total(PAGE_12_GLUED, ["d/3/1"])


def test_a_total_with_no_page_number_is_evidence_for_no_page():
    assert not chunk_states_part_summary_total(UNLABELLED, ["d/3/1"])
    # Asked without a page, it is still a Part Summary total.
    assert chunk_states_part_summary_total(UNLABELLED)


# ── what gets STATED to the user ──────────────────────────────────────────

def test_compose_reads_the_page_number_printed_after_the_total():
    parsed = compose_part_summary_total(ASK, PAGE_1)
    assert parsed and parsed["page"] == "d/3/1"
    assert parsed["amount"] == 1234567.0
    assert parsed["currency"] == "SAR"


@pytest.mark.parametrize("wrong", [UNLABELLED, PAGE_12_GLUED, PAGE_2])
def test_compose_never_states_another_pages_total(wrong):
    """The dangerous one. Alone in the excerpt, each of these used to come
    back as {'page': 'd/3/1', 'amount': <its own figure>}."""
    assert compose_part_summary_total(ASK, wrong) is None


def test_compose_picks_the_asked_page_out_of_a_crowded_excerpt():
    """Live shape: five footers retrieved, one of them the right one. The old
    composer returned None here (ambiguous) and the answer was "not found"."""
    excerpt = "\n\n".join((UNLABELLED, PAGE_12_GLUED, PAGE_2, PAGE_1))
    parsed = compose_part_summary_total(ASK, excerpt)
    assert parsed and parsed["amount"] == 1234567.0


def test_an_unlabelled_total_does_not_borrow_the_next_totals_footer():
    """Two totals in one excerpt, the first with no page number of its own.
    The footer after the SECOND total names the second total's page."""
    excerpt = (
        "A 250.26 Coating Stripping Test To Part Summary SAR 2,717.00 "
        "D290.2 Nr 1,239 1,185.00 To Part Summary SAR 1,234,567.00 "
        + FOOTER + "Page d/3/1 124 of 675"
    )
    assert compose_part_summary_total(ASK, excerpt)["amount"] == 1234567.0


def test_compose_follows_the_question_to_the_other_page():
    excerpt = "\n\n".join((PAGE_1, PAGE_2, PAGE_12_GLUED))
    assert compose_part_summary_total(ASK_NEXT_PAGE, excerpt)["amount"] == 7654321.0
    assert compose_part_summary_total(ASK_PAGE_12, excerpt)["amount"] == 9999999.0


# ── retrieval: the named page beats the crowd ─────────────────────────────

def _chunk(cid, score, text, index):
    return Chunk(chunk_id=cid, project_id="p_master", doc_id="boq",
                 chunk_index=index, text=text, score=score)


def test_retrieval_returns_the_asked_page_and_not_the_other_footers(monkeypatch):
    from app.core.rag import retriever as ret

    crowd = [
        _chunk(f"u{i}", 0.80 - i / 100, UNLABELLED + f" sheet {i}", 200 + i)
        for i in range(8)
    ]
    others = [_chunk("p12", 0.79, PAGE_12_GLUED, 40), _chunk("p2", 0.78, PAGE_2, 5)]
    right = _chunk("p1", 0.40, PAGE_1, 3)
    semantic = crowd + others + [right]

    vs = "app.core.rag.vector_store.VectorStore."
    monkeypatch.setattr(
        vs + "search",
        lambda self, pid, qvec, k, query_text=None: semantic[:k],
    )
    monkeypatch.setattr(vs + "identifier_search",
                        lambda self, pid, identifiers, k=20: [])
    monkeypatch.setattr(vs + "chunks_for_docs",
                        lambda self, pid, doc_ids, k_per_doc=12, **_kw: [])
    monkeypatch.setattr(vs + "chunks_containing_all",
                        lambda self, pid, needles, k=20, **_kw: [])
    monkeypatch.setattr(vs + "count", lambda self, pid=None: 11)
    monkeypatch.setattr(vs + "_verify_embedding_identity", lambda self: None)
    monkeypatch.setattr(ret, "_doc_name_for_id",
                        lambda did: "Example - Demolition BOQ.pdf", raising=False)
    monkeypatch.setattr("app.core.projects.documents_matching_title_phrase",
                        lambda pid, phrase, limit=8: [])
    monkeypatch.setattr("app.core.projects.documents_matching_filename_terms",
                        lambda *a, **k: [])
    monkeypatch.setenv("RAG_EMBEDDING_MODEL", "fake")
    monkeypatch.setenv("RAG_GENERAL_KNOWLEDGE_PROJECTS", "")
    monkeypatch.delenv("MASTER_CORPUS_SOURCE_PROJECT_ID", raising=False)
    monkeypatch.delenv("COMPOSE_PART_SUMMARY", raising=False)
    monkeypatch.delenv("RAG_LAYERED", raising=False)

    chunks, _ = ret.retrieve_with_filter(ASK, "p_master", k=5)

    assert [c.chunk_id for c in chunks] == ["p1"]
