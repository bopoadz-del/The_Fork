"""Wave-1 DeepSeek A2/A3/A9: asked particular, not the neighboring field.

Live tip 34d64e1 / theshovel.ai Master Corpus (LLM_PROVIDER=deepseek):

* **A2 FAIL** — "Accepted Contract Amount including VAT" answered delay
  damages (SAR 263,175.67/day from the excl-VAT ACA). Expect the
  including-VAT figure.
* **A3 PARTIAL** — Time for Completion retrieved permit-tracker /
  community schedules / PSA recitals. Expect ``852 days``.
* **A9 PARTIAL** — Engineer retrieved CoC 1.3–1.8 + drawing notes.
  Expect JACOBS / CH2M Saudi Limited.

A5 / A6 / B2 and D2 / D4 / D5 stay on their own pins. Fixture figures
for money; 852 days and JACOBS/CH2M are the live expected strings
already used elsewhere in this repo.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from app.agents.runtime import (
    _CG_REFUSAL,
    _graft_asked_contract_particular,
    _postprocess_answer,
)
from app.core.rag.vector_store import Chunk


CATALOG = json.loads(
    (Path(__file__).parent / "fixtures" / "ui_phys" / "questions.json")
    .read_text(encoding="utf-8")
)
A2_ASK = CATALOG["cases"]["A2"]["ask"]
A3_ASK = CATALOG["cases"]["A3"]["ask"]
A5_ASK = CATALOG["cases"]["A5"]["ask"]
A6_ASK = CATALOG["cases"]["A6"]["ask"]
A9_ASK = CATALOG["cases"]["A9"]["ask"]
LIVE_PREFIX = "Answer only from the client project documents. "
LIVE_A2 = LIVE_PREFIX + A2_ASK
LIVE_A3 = LIVE_PREFIX + A3_ASK
LIVE_A9 = LIVE_PREFIX + A9_ASK

# Live expected strings (A2 figure already used by E1 compose tests).
ACA_INCL = "SAR 2,017,680,124.69"
ACA_EXCL = "SAR 1,754,504,456.25"
A3_DAYS = "852 days"
A9_FIRM = "JACOBS (CH2M Saudi Limited)"
A5_RATE = "0.1% of the Contract Price per calendar day"
A6_DAYS = "365 days"

DD23_NAME = (
    "DD-2023-118_the client project II Infrastructure Package 1_"
    "Vol 1 - Conditions of Contract.pdf"
)
CD_NAME = (
    "DD-2023-118_DG2 Infra P1_Vol 1.0_Cond of Contract "
    "(complete)_Contract Data.pdf"
)
DRAWING_NAME = "DD-2023-118_DG2 Infra P1_Vol 3 – Drawings.pdf"
PSA_NAME = "Long Form PSA 26_05_22 Rev 5 with Legal Amendments.docx"
PERMIT_NAME = "CPM 16-01-2024.pdf"

DELAY_DAMAGES_COMPOSE = (
    "Delay damages for the whole of the Works are SAR 263,175.67 "
    f"per calendar day (0.015% of Accepted Contract Amount {ACA_EXCL})."
)
COC_DELAY_CHUNK = (
    "Volume 1 - Conditions of Contract. Sub-Clause 8.8 Delay Damages. "
    "Delay damages for the whole of the Works are 0.015% of the "
    f"Accepted Contract Amount {ACA_EXCL} per calendar day."
)
SCANNED_ACA_INCL = (
    "CONTRACT DATA\n"
    "1.1.1\nAccepted \nContract \nAmount (including VAT)\n"
    f"{ACA_INCL}\n"
)
SCANNED_ACA_EXCL = (
    "CONTRACT DATA\n"
    "1.1.1\nAccepted \nContract \nAmount (excluding VAT)\n"
    f"{ACA_EXCL}\n"
)
SCANNED_TFC = (
    "CONTRACT DATA particulars — filled-in amount / duration / "
    f"percentage [{DD23_NAME}].\n"
    "Particular Conditions Part A - Contract Data\n"
    f"1.1.75 Time for Completion for the whole of the Works: {A3_DAYS}"
)
PERMIT_TRACKER = (
    "Permit tracking register. Southern Community I-01 Media "
    "Jan-24 to Jan-25 commencement-completion schedule. "
    "Community works completion dates are not Time for Completion."
)
# Live leftover after #516: a neighboring 90-day TfC leaked into the
# graft lead-in ahead of 1.1.75 / 852 days.
SECTIONAL_90 = (
    "CONTRACT DATA particulars — sectional completion.\n"
    "Time for Completion for Section A / Community I-01: 90 days"
)
A3_CORRECT_BODY = (
    "I found the answer in the Contract Data section of the document. "
    "Under Clause 1.1.75, the Time for Completion (for the whole of "
    "the Works) is stated as: 852 days from the Commencement Date."
)
A3_LIVE_90_LEAD_IN = (
    "The Time for Completion for the whole of the Works is 90 days.\n\n"
    + A3_CORRECT_BODY
)
_LEADING_90_WHOLE_WORKS_RE = re.compile(
    r"(?is)^\s*The Time for Completion for the whole of the Works is 90 days",
)
PSA_RECITALS = (
    "Long Form Professional Services Agreement recitals. The parties "
    "agree the consultant shall perform the services. No Time for "
    "Completion for the whole of the Works is stated here."
)
SCANNED_ENGINEER = (
    "CONTRACT DATA\n"
    "The Engineer\n"
    f"{A9_FIRM}\n"
)
# Live A9 split: label and firm on consecutive chunks.
ENGINEER_LABEL_ONLY = "CONTRACT DATA\n1.3.1 (b)\nEngineer\n"
ENGINEER_VALUE_ONLY = f"{A9_FIRM}\n"
COC_1_3 = (
    "Volume 1 - Conditions of Contract. Clauses 1.3–1.8 on "
    "communications, law and language, priority of documents, "
    "assignment, and care of documents. The Engineer is named in "
    "the Contract Data."
)
DRAWING_NOTES = (
    "Drawing general notes. Refer to the Engineer for clarification "
    "of dimensions. No appointment is stated on this sheet."
)

ACTIVE = "p_master"
CD_DOC = "cd118"
COC_DOC = "coc118"
TFC_DOC = "tfc118"
ENG_DOC = "eng118"
ENG_LABEL_DOC = "englbl"
ENG_VAL_DOC = "engval"
PSA_DOC = "psa"
PERMIT_DOC = "permit"
DRAW_DOC = "draw"


def _chunk(cid, doc_id, score, text, chunk_index=0):
    return Chunk(
        chunk_id=cid,
        project_id=ACTIVE,
        doc_id=doc_id,
        chunk_index=chunk_index,
        text=text,
        score=score,
    )


def test_catalog_asks_are_frozen():
    assert A2_ASK == "What is the Accepted Contract Amount including VAT?"
    assert A3_ASK == "What is the Time for Completion for the whole of the Works?"
    assert A9_ASK == "Who is the Engineer under this contract?"


def test_a2_a3_a9_predicates():
    from app.core.rag.retriever import (
        chunk_states_aca_including_vat,
        chunk_states_engineer_identity,
        chunk_states_time_for_completion,
        extract_aca_including_vat,
        extract_engineer_identity,
        extract_time_for_completion_days,
        query_asks_for_aca_including_vat,
        query_asks_for_time_for_completion,
        query_asks_who_the_engineer_is,
    )

    assert query_asks_for_aca_including_vat(A2_ASK)
    assert query_asks_for_aca_including_vat(LIVE_A2)
    assert not query_asks_for_aca_including_vat(
        "What is the Accepted Contract Amount, excluding VAT?"
    )
    assert not query_asks_for_aca_including_vat(A5_ASK)

    assert query_asks_for_time_for_completion(A3_ASK)
    assert query_asks_for_time_for_completion(LIVE_A3)
    assert not query_asks_for_time_for_completion(
        "What is the Time for Completion for Milestone 2?"
    )
    assert not query_asks_for_time_for_completion(A5_ASK)

    assert query_asks_who_the_engineer_is(A9_ASK)
    assert query_asks_who_the_engineer_is(LIVE_A9)

    assert chunk_states_aca_including_vat(SCANNED_ACA_INCL)
    assert not chunk_states_aca_including_vat(SCANNED_ACA_EXCL)
    assert not chunk_states_aca_including_vat(COC_DELAY_CHUNK)
    assert not chunk_states_aca_including_vat(DELAY_DAMAGES_COMPOSE)

    assert chunk_states_time_for_completion(SCANNED_TFC)
    assert not chunk_states_time_for_completion(PERMIT_TRACKER)
    assert not chunk_states_time_for_completion(PSA_RECITALS)
    assert not chunk_states_time_for_completion(SECTIONAL_90)

    mixed_tfc = SECTIONAL_90 + "\n\n" + SCANNED_TFC
    assert extract_time_for_completion_days(mixed_tfc) == A3_DAYS
    assert extract_time_for_completion_days(SECTIONAL_90) is None

    assert chunk_states_engineer_identity(SCANNED_ENGINEER)
    assert chunk_states_engineer_identity(
        ENGINEER_LABEL_ONLY + ENGINEER_VALUE_ONLY
    )
    assert not chunk_states_engineer_identity(COC_1_3)
    assert not chunk_states_engineer_identity(DRAWING_NOTES)

    amount, currency = extract_aca_including_vat(SCANNED_ACA_INCL)
    assert currency == "SAR"
    assert amount == 2_017_680_124.69
    assert extract_aca_including_vat(COC_DELAY_CHUNK) is None
    assert extract_time_for_completion_days(SCANNED_TFC) == A3_DAYS
    assert extract_engineer_identity(SCANNED_ENGINEER) == A9_FIRM


def _install(monkeypatch, *, semantic, rescue_hits, names, seeded=None):
    from app.core.rag import retriever as ret

    def fake_search(self, project_id, qvec, k, query_text=None):
        return [c for c in semantic if c.project_id == project_id][:k]

    def fake_id_search(self, project_id, identifiers, k=20):
        blob = " ".join(identifiers or []).lower()
        out = []
        for chunk in rescue_hits:
            text_l = (chunk.text or "").lower()
            if "including" in blob and "vat" in blob and "including vat" in text_l:
                out.append(chunk)
            if "time" in blob and "completion" in blob and "852" in text_l:
                out.append(chunk)
            if "1.1.75" in blob and "time" in blob and "completion" in text_l:
                out.append(chunk)
            if "engineer" in blob and (
                "jacobs" in text_l or "ch2m" in text_l or "engineer" in text_l
            ):
                out.append(chunk)
        return out[:k]

    def fake_chunks_for_docs(self, project_id, doc_ids, k_per_doc=12):
        out = []
        for chunk in rescue_hits:
            if chunk.doc_id in (doc_ids or []):
                out.append(chunk)
        return out[: max(1, int(k_per_doc or 12))]

    seeded = seeded or []

    def fake_title_match(pid, phrase, limit=8):
        needle = (phrase or "").lower()
        return [
            doc for doc in seeded
            if needle and needle in (
                f"{doc.get('original_name', '')} {doc.get('file_path', '')}".lower()
            )
        ][:limit]

    monkeypatch.setattr("app.core.rag.vector_store.VectorStore.search", fake_search)
    monkeypatch.setattr(
        "app.core.rag.vector_store.VectorStore.identifier_search", fake_id_search,
    )
    monkeypatch.setattr(
        "app.core.rag.vector_store.VectorStore.chunks_for_docs", fake_chunks_for_docs,
    )
    monkeypatch.setattr(
        "app.core.rag.vector_store.VectorStore.chunks_containing_all",
        lambda self, project_id, needles, k=20: [],
    )
    monkeypatch.setattr(
        "app.core.rag.vector_store.VectorStore.count", lambda self, pid=None: 4,
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
    monkeypatch.setattr(
        "app.core.projects.documents_matching_filename_terms",
        lambda *a, **k: [],
    )
    monkeypatch.setenv("RAG_EMBEDDING_MODEL", "fake")
    monkeypatch.setenv("RAG_GENERAL_KNOWLEDGE_PROJECTS", "")
    monkeypatch.delenv("MASTER_CORPUS_SOURCE_PROJECT_ID", raising=False)
    monkeypatch.delenv("RAG_CONTRACT_DATA_FILENAME_RESCUE", raising=False)
    monkeypatch.delenv("RAG_ACA_INCLUDING_VAT_RESCUE", raising=False)
    monkeypatch.delenv("RAG_TIME_FOR_COMPLETION_RESCUE", raising=False)
    monkeypatch.delenv("RAG_ENGINEER_IDENTITY_RESCUE", raising=False)
    monkeypatch.delenv("RAG_DELAY_DAMAGES_RATE_RESCUE", raising=False)
    monkeypatch.delenv("RAG_LAYERED", raising=False)
    return ret


def test_a2_surfaces_including_vat_not_delay_damages(monkeypatch):
    """Live A2: CoC delay-damages chunk leads; including-VAT row is absent."""
    delay = _chunk("dd", COC_DOC, 0.93, COC_DELAY_CHUNK)
    excl = _chunk("ex", CD_DOC, 0.88, SCANNED_ACA_EXCL)
    incl = _chunk("in", CD_DOC, 0.22, SCANNED_ACA_INCL)
    names = {COC_DOC: DD23_NAME, CD_DOC: CD_NAME}
    seeded = [{"id": CD_DOC, "original_name": CD_NAME, "file_path": CD_NAME}]
    ret = _install(
        monkeypatch,
        semantic=[delay, excl],
        rescue_hits=[incl],
        names=names,
        seeded=seeded,
    )
    chunks, _ = ret.retrieve_with_filter(LIVE_A2, ACTIVE, k=5)
    blob = " ".join(c.text for c in chunks)
    assert chunks
    assert ACA_INCL in blob
    assert ACA_INCL in chunks[0].text
    assert DELAY_DAMAGES_COMPOSE.split(" are ")[0] not in blob
    assert "0.015%" not in blob
    assert "263,175.67" not in blob


def test_a3_surfaces_852_days_not_90_day_sectional(monkeypatch):
    """Neighboring 90-day TfC must not occupy top-k over 1.1.75 / 852."""
    sectional = _chunk("s90", PERMIT_DOC, 0.92, SECTIONAL_90)
    tfc = _chunk("tfc", TFC_DOC, 0.21, SCANNED_TFC)
    names = {PERMIT_DOC: PERMIT_NAME, TFC_DOC: DD23_NAME}
    ret = _install(
        monkeypatch,
        semantic=[sectional],
        rescue_hits=[tfc],
        names=names,
    )
    chunks, _ = ret.retrieve_with_filter(LIVE_A3, ACTIVE, k=5)
    blob = " ".join(c.text for c in chunks)
    assert chunks
    assert A3_DAYS in blob
    assert A3_DAYS in chunks[0].text
    assert "90 days" not in blob


def test_a3_surfaces_852_days_not_permit_tracker(monkeypatch):
    permit = _chunk("pm", PERMIT_DOC, 0.91, PERMIT_TRACKER)
    psa = _chunk("psa", PSA_DOC, 0.88, PSA_RECITALS)
    tfc = _chunk("tfc", TFC_DOC, 0.21, SCANNED_TFC)
    names = {
        PERMIT_DOC: PERMIT_NAME, PSA_DOC: PSA_NAME, TFC_DOC: DD23_NAME,
    }
    ret = _install(
        monkeypatch,
        semantic=[permit, psa],
        rescue_hits=[tfc],
        names=names,
    )
    chunks, _ = ret.retrieve_with_filter(LIVE_A3, ACTIVE, k=5)
    blob = " ".join(c.text for c in chunks)
    assert chunks
    assert A3_DAYS in blob
    assert A3_DAYS in chunks[0].text
    assert "Permit tracking" not in blob
    assert "Southern Community" not in blob


def test_a9_surfaces_jacobs_ch2m_not_coc_or_drawings(monkeypatch):
    coc = _chunk("coc", COC_DOC, 0.90, COC_1_3)
    draw = _chunk("dwg", DRAW_DOC, 0.86, DRAWING_NOTES)
    eng = _chunk("eng", ENG_DOC, 0.20, SCANNED_ENGINEER)
    names = {
        COC_DOC: DD23_NAME, DRAW_DOC: DRAWING_NAME, ENG_DOC: CD_NAME,
    }
    ret = _install(
        monkeypatch,
        semantic=[coc, draw],
        rescue_hits=[eng],
        names=names,
        seeded=[{"id": ENG_DOC, "original_name": CD_NAME, "file_path": CD_NAME}],
    )
    chunks, _ = ret.retrieve_with_filter(LIVE_A9, ACTIVE, k=5)
    blob = " ".join(c.text for c in chunks)
    assert chunks
    assert "JACOBS" in blob
    assert "CH2M" in blob
    assert "JACOBS" in chunks[0].text
    assert "1.3–1.8" not in blob
    assert "Drawing general notes" not in blob


def test_a9_pairs_split_engineer_label_and_firm(monkeypatch):
    """Live scanned table: Engineer on one chunk, JACOBS/CH2M on the next."""
    coc = _chunk("coc", COC_DOC, 0.90, COC_1_3)
    label = _chunk("lbl", ENG_LABEL_DOC, 0.18, ENGINEER_LABEL_ONLY, chunk_index=4)
    value = _chunk("val", ENG_LABEL_DOC, 0.17, ENGINEER_VALUE_ONLY, chunk_index=5)
    names = {COC_DOC: DD23_NAME, ENG_LABEL_DOC: CD_NAME}
    ret = _install(
        monkeypatch,
        semantic=[coc],
        rescue_hits=[label, value],
        names=names,
        seeded=[{"id": ENG_LABEL_DOC, "original_name": CD_NAME, "file_path": CD_NAME}],
    )
    chunks, _ = ret.retrieve_with_filter(A9_ASK, ACTIVE, k=5)
    blob = " ".join(c.text for c in chunks)
    assert "JACOBS" in blob
    assert "CH2M" in blob


def test_a5_and_a6_are_not_stolen_onto_a2_a3_a9_rescue(monkeypatch):
    delay = _chunk("dd", COC_DOC, 0.92, COC_DELAY_CHUNK)
    dnp = _chunk(
        "dnp", TFC_DOC, 0.84,
        "CONTRACT DATA particulars.\n"
        f"1.1.27 Defects Notification Period: {A6_DAYS} from the TOC",
    )
    incl = _chunk("in", CD_DOC, 0.20, SCANNED_ACA_INCL)
    tfc = _chunk("tfc", TFC_DOC, 0.19, SCANNED_TFC)
    eng = _chunk("eng", ENG_DOC, 0.18, SCANNED_ENGINEER)
    names = {
        COC_DOC: DD23_NAME, TFC_DOC: DD23_NAME, CD_DOC: CD_NAME, ENG_DOC: CD_NAME,
    }
    ret = _install(
        monkeypatch,
        semantic=[delay, dnp],
        rescue_hits=[incl, tfc, eng],
        names=names,
    )
    a5, _ = ret.retrieve_with_filter(A5_ASK, ACTIVE, k=5)
    a6, _ = ret.retrieve_with_filter(A6_ASK, ACTIVE, k=5)
    assert all(c.doc_id != CD_DOC for c in a5)
    assert all(c.doc_id != ENG_DOC for c in a5)
    assert all(c.doc_id != CD_DOC for c in a6)
    assert all(c.doc_id != ENG_DOC for c in a6)
    assert any(A6_DAYS in (c.text or "") for c in a6)


def _sys(*chunk_texts: str) -> dict:
    body = "\n\n".join(
        f"[doc_id=doc{i} chunk={i} score=0.80] {t}"
        for i, t in enumerate(chunk_texts)
    )
    return {"role": "system", "content": "Reference context:\n" + body}


def test_graft_a2_replaces_delay_damages_with_including_vat():
    rag = _sys(COC_DELAY_CHUNK, SCANNED_ACA_INCL)
    msgs = [{"role": "user", "content": LIVE_A2}]
    out = _graft_asked_contract_particular(DELAY_DAMAGES_COMPOSE, rag, msgs)
    assert "2,017,680,124.69" in out
    assert "including VAT" in out
    assert "263,175.67" not in out
    assert "delay damages" not in out.lower()


def test_graft_a3_does_not_lead_with_90_when_852_is_known():
    """Live leftover #516: 90-day neighbor must not open the answer."""
    rag = _sys(SECTIONAL_90, SCANNED_TFC)
    msgs = [{"role": "user", "content": LIVE_A3}]
    out = _graft_asked_contract_particular(A3_CORRECT_BODY, rag, msgs)
    assert A3_DAYS in out
    assert not _LEADING_90_WHOLE_WORKS_RE.search(out)
    first_whole = re.search(
        r"(?i)Time for Completion for the whole of the Works is\s+(\d+)\s+days",
        out,
    )
    if first_whole:
        assert first_whole.group(1) == "852"


def test_graft_a3_strips_spurious_90_lead_in_alongside_852():
    """Pin: a leading 90-day whole-Works claim next to 852 is a fail."""
    rag = _sys(SECTIONAL_90, SCANNED_TFC)
    msgs = [{"role": "user", "content": LIVE_A3}]
    out = _graft_asked_contract_particular(A3_LIVE_90_LEAD_IN, rag, msgs)
    assert A3_DAYS in out
    assert not _LEADING_90_WHOLE_WORKS_RE.search(out)
    assert not out.lstrip().startswith(
        "The Time for Completion for the whole of the Works is 90 days"
    )
    before_852 = out.split("852", 1)[0]
    assert "90 days" not in before_852


def test_postprocess_a3_drops_90_lead_in_when_852_is_in_excerpts():
    rag = _sys(SECTIONAL_90, SCANNED_TFC)
    msgs = [{"role": "user", "content": LIVE_A3}]
    out = _postprocess_answer(A3_LIVE_90_LEAD_IN, rag, msgs)
    assert A3_DAYS in out
    assert not _LEADING_90_WHOLE_WORKS_RE.search(out)
    assert out != _CG_REFUSAL


def test_graft_a3_states_852_days_when_model_said_absent():
    rag = _sys(PERMIT_TRACKER, SCANNED_TFC)
    msgs = [{"role": "user", "content": LIVE_A3}]
    missing = (
        "The retrieved context contains permit-tracker tables and PSA "
        "recitals — none of which state a Time for Completion. "
        "What I did not find in these excerpts: a stated duration."
    )
    out = _graft_asked_contract_particular(missing, rag, msgs)
    assert A3_DAYS in out
    assert out.startswith("The Time for Completion")


def test_graft_a9_states_jacobs_ch2m_when_model_could_not_confirm():
    rag = _sys(COC_1_3, SCANNED_ENGINEER)
    msgs = [{"role": "user", "content": LIVE_A9}]
    missing = (
        "I could not confirm the identity of the Engineer in the "
        "retrieved excerpts. I can search again if you give the "
        "exact document name."
    )
    out = _graft_asked_contract_particular(missing, rag, msgs)
    assert "JACOBS" in out
    assert "CH2M" in out


def test_graft_does_not_invent_when_excerpt_lacks_the_particular():
    rag = _sys(COC_DELAY_CHUNK)
    msgs = [{"role": "user", "content": LIVE_A2}]
    assert _graft_asked_contract_particular(DELAY_DAMAGES_COMPOSE, rag, msgs) == (
        DELAY_DAMAGES_COMPOSE
    )


def test_graft_kill_switch(monkeypatch):
    monkeypatch.setenv("GRAFT_ASKED_CONTRACT_PARTICULAR", "0")
    rag = _sys(SCANNED_ACA_INCL)
    msgs = [{"role": "user", "content": LIVE_A2}]
    assert _graft_asked_contract_particular(DELAY_DAMAGES_COMPOSE, rag, msgs) == (
        DELAY_DAMAGES_COMPOSE
    )


def test_postprocess_a2_delay_damages_becomes_including_vat():
    rag = _sys(SCANNED_ACA_INCL)
    msgs = [{"role": "user", "content": LIVE_A2}]
    out = _postprocess_answer(DELAY_DAMAGES_COMPOSE, rag, msgs)
    assert "2,017,680,124.69" in out
    assert out != _CG_REFUSAL


def test_inject_hints_name_the_asked_particular():
    from app.core.rag.inject import format_chunks_as_system_message

    incl = _chunk("in", CD_DOC, 0.9, SCANNED_ACA_INCL)
    tfc = _chunk("tfc", TFC_DOC, 0.9, SCANNED_TFC)
    eng = _chunk("eng", ENG_DOC, 0.9, SCANNED_ENGINEER)
    a2 = format_chunks_as_system_message([incl], 1, query=LIVE_A2)["content"]
    a3 = format_chunks_as_system_message([tfc], 1, query=LIVE_A3)["content"]
    a9 = format_chunks_as_system_message([eng], 1, query=LIVE_A9)["content"]
    assert "INCLUDING VAT" in a2
    assert "delay damages" in a2.lower()
    assert "TIME FOR COMPLETION" in a3
    assert "ENGINEER APPOINTMENT" in a9
