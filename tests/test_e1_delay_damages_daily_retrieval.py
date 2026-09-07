"""OLD-pack E1: retrieve rate AND ACA so compose can state SAR/day.

Live Master Corpus (tip 8f4b465 / leftover pack E1):

* A5 PASS — rate rescue surfaces ``0.1% of the Contract Price per
  calendar day``.
* A2 PASS — filename / incl-VAT rescue surfaces the ACA.
* E1 FAIL — "Calculate the delay damages per calendar day in SAR for
  the whole of the Works" retrieved Spec TOC / Daywork / insurance
  and refused to compute. Expected SAR 1,754,504.46/day = 0.1% of
  excl-VAT ACA SAR 1,754,504,456.25.

E1 is a monetary-base ask, so A5's exclusive rate rescue is correctly
off (that fence would drop the ACA). This path is a dual-operand
rescue: scanned Contract Data rate + ACA, then the existing compose
graft. Kill-switch ``RAG_DELAY_DAMAGES_DAILY_RESCUE=0`` restores the
FAIL (lookalikes, no SAR/day). Do not steal A2/A3/A5/A6/A9.
"""
from __future__ import annotations

import json
from pathlib import Path

from app.agents.runtime import (
    _CG_REFUSAL,
    _graft_composed_delay_damages_daily,
    _postprocess_answer,
)
from app.core.rag.vector_store import Chunk
from app.lib.construction_formulas_commercial import (
    compose_delay_damages_daily_from_excerpts,
    query_asks_delay_damages_daily_amount,
)


CATALOG = json.loads(
    (Path(__file__).parent / "fixtures" / "ui_phys" / "questions.json")
    .read_text(encoding="utf-8")
)
A2_ASK = CATALOG["cases"]["A2"]["ask"]
A3_ASK = CATALOG["cases"]["A3"]["ask"]
A5_ASK = CATALOG["cases"]["A5"]["ask"]
A6_ASK = CATALOG["cases"]["A6"]["ask"]
A9_ASK = CATALOG["cases"]["A9"]["ask"]
E1_ASK = CATALOG["cases"]["E1"]["ask"]
LIVE_PREFIX = "Answer only from the client project documents. "
LIVE_E1 = LIVE_PREFIX + (
    "Calculate the delay damages per calendar day in SAR for the "
    "whole of the Works."
)
LIVE_A5 = LIVE_PREFIX + A5_ASK

# Fixture figures already used by E1 compose / A2 graft tests.
NET_ACA = 1_754_504_456.25
GROSS_ACA = 2_017_680_124.69
DAILY = 1_754_504.46
RATE = "0.1% of the Contract Price per calendar day"
NET_ACA_TXT = f"SAR {NET_ACA:,.2f}"
GROSS_ACA_TXT = f"SAR {GROSS_ACA:,.2f}"

DD23_NAME = (
    "DD-2023-118_the client project II Infrastructure Package 1_"
    "Vol 1 - Conditions of Contract.pdf"
)
CD_SCANNED_NAME = (
    "DD-2023-118_DG2 Infra P1_Vol 1.0_Cond of Contract "
    "(complete)_Contract Data.pdf"
)
SPEC_NAME = "DD-2023-118_Vol 2 Specifications — Table of Contents.pdf"
DAYWORK_NAME = "Daywork Schedule — Labour per calendar day.pdf"
INSURANCE_NAME = "Works All Risks Insurance Policy.pdf"

SCANNED_RATE = (
    "CONTRACT DATA\n"
    "8.8\nDelay \nDamages for the whole of the Works\n"
    f"{RATE}\n"
)
SCANNED_NET_ACA = (
    "CONTRACT DATA\n"
    "1.1.1\nAccepted\nContract\nAmount (excluding VAT)\n"
    f"{NET_ACA_TXT}\n"
)
SCANNED_GROSS_ACA = (
    "CONTRACT DATA\n"
    "1.1.1\nAccepted\nContract\nAmount (including VAT)\n"
    f"{GROSS_ACA_TXT}\n"
)
SPEC_TOC = (
    "Specification table of contents. Section 01 The Works. "
    "Section 02 Materials. Daywork is scheduled separately. "
    "No delay damages rate is stated here."
)
DAYWORK = (
    "Daywork Schedule. Labour per calendar day. Plant per hour. "
    "Rates in SAR for measured dayworks, not liquidated damages."
)
INSURANCE = (
    "Works All Risks insurance policy. Sum insured "
    "SAR 50,000,000.00. Period of insurance for the whole of the Works."
)
GC_8_8 = (
    "Volume 1 - Conditions of Contract. Sub-Clause 8.8 Delay Damages. "
    "The Contractor shall pay delay damages for the whole of the Works "
    "at the rate stated in the Contract Data for every calendar day."
)
# Live leftover E1 on c5c6dfa: chunks 9–11 of the bound Contract Data
# volume stated the 0.1% rate AND a FIDIC worked-example ACA of
# SAR 10,000,000. Retrieval treated that as the money operand, so
# compose emitted SAR 10,000/day.
COC_8_8_TOY_ACA = (
    "Volume 1 - Conditions of Contract. Sub-Clause 8.8 Delay Damages. "
    "The Contractor shall pay delay damages for the whole of the Works "
    f"at {RATE}. For example, if the Accepted Contract Amount "
    "excluding VAT is SAR 10,000,000.00, the daily amount is "
    "SAR 10,000.00."
)
TOY_ACA_TXT = "SAR 10,000,000.00"
PREFIXED_TFC = (
    "CONTRACT DATA particulars — filled-in amount / duration / "
    f"percentage [{DD23_NAME}].\n"
    "1.1.75 Time for Completion for the whole of the Works: 852 days"
)
PREFIXED_DNP = (
    "CONTRACT DATA particulars — filled-in amount / duration / "
    f"percentage [{DD23_NAME}].\n"
    "1.1.27 Defects Notification Period: 365 days from the Taking-Over Certificate"
)
CANNOT_CALCULATE = (
    "I cannot calculate the delay damages per calendar day from the "
    "reference context provided. Retrieved excerpts include the "
    "Specification table of contents, the Daywork Schedule, and an "
    "insurance policy."
)

ACTIVE = "p_master"
SPEC_DOC = "spec118"
DAY_DOC = "daywork"
INS_DOC = "insure"
RATE_DOC = "scanrate"
ACA_DOC = "scanaca"
GROSS_DOC = "scangross"
GC_DOC = "gc118"
TFC_DOC = "tfc118"
DNP_DOC = "dnp118"


def _chunk(cid, doc_id, score, text, project_id=ACTIVE):
    return Chunk(
        chunk_id=cid,
        project_id=project_id,
        doc_id=doc_id,
        chunk_index=0,
        text=text,
        score=score,
    )


def _sys(*chunk_texts: str) -> dict:
    body = "\n\n".join(
        f"[doc_id=doc{i} chunk={i} score=0.80] {t}"
        for i, t in enumerate(chunk_texts)
    )
    return {"role": "system", "content": "Reference context:\n" + body}


def test_catalog_and_ask_class_do_not_steal_a5():
    from app.core.rag.retriever import (
        query_asks_delay_damages_daily_amount as ret_asks,
        query_asks_for_delay_damages_rate,
        query_needs_a_monetary_base,
        query_wants_contract_data_file,
    )

    assert E1_ASK == (
        "Calculate the delay damages per calendar day in SAR for the "
        "whole of the Works."
    )
    assert query_asks_delay_damages_daily_amount(E1_ASK)
    assert query_asks_delay_damages_daily_amount(LIVE_E1)
    assert ret_asks(E1_ASK) == query_asks_delay_damages_daily_amount(E1_ASK)
    assert query_needs_a_monetary_base(LIVE_E1)
    assert query_wants_contract_data_file(LIVE_E1)

    assert not query_asks_for_delay_damages_rate(E1_ASK)
    assert not query_asks_for_delay_damages_rate(LIVE_E1)
    assert not query_asks_delay_damages_daily_amount(A5_ASK)
    assert not query_asks_delay_damages_daily_amount(LIVE_A5)
    assert not query_asks_delay_damages_daily_amount(A2_ASK)
    assert not query_asks_delay_damages_daily_amount(A3_ASK)
    assert not query_wants_contract_data_file(A5_ASK)


def test_scanned_operands_and_lookalikes():
    from app.core.rag.retriever import (
        chunk_answers_asked_particular,
        chunk_states_accepted_contract_amount,
        chunk_states_delay_damages_rate,
    )

    assert chunk_states_delay_damages_rate(SCANNED_RATE)
    assert chunk_states_accepted_contract_amount(SCANNED_NET_ACA)
    assert chunk_states_accepted_contract_amount(SCANNED_GROSS_ACA)
    assert not chunk_states_accepted_contract_amount(SCANNED_RATE)
    assert not chunk_states_delay_damages_rate(SCANNED_NET_ACA)
    assert not chunk_states_accepted_contract_amount(INSURANCE)
    assert not chunk_states_accepted_contract_amount(DAYWORK)
    assert not chunk_states_delay_damages_rate(GC_8_8)
    assert not chunk_states_accepted_contract_amount(COC_8_8_TOY_ACA)
    assert chunk_states_delay_damages_rate(COC_8_8_TOY_ACA)

    assert chunk_answers_asked_particular(LIVE_E1, SCANNED_RATE)
    assert chunk_answers_asked_particular(LIVE_E1, SCANNED_NET_ACA)
    assert not chunk_answers_asked_particular(LIVE_E1, SPEC_TOC)
    assert not chunk_answers_asked_particular(LIVE_E1, GC_8_8)
    # A5 still answers from the rate only — ACA is the neighboring field.
    assert chunk_answers_asked_particular(A5_ASK, SCANNED_RATE)
    assert not chunk_answers_asked_particular(A5_ASK, SCANNED_NET_ACA)


def _install_e1_corpus(monkeypatch, *, semantic, rescue_hits, names, seeded=None):
    from app.core.rag import retriever as ret

    seeded = list(seeded or [])

    def fake_search(self, project_id, qvec, k, query_text=None):
        return [c for c in semantic if c.project_id == project_id][:k]

    def fake_id_search(self, project_id, identifiers, k=20):
        blob = " ".join(identifiers or []).lower()
        out = []
        for chunk in rescue_hits:
            text_l = (chunk.text or "").lower()
            if "delay" in blob and "damages" in blob and "0.1%" in text_l:
                out.append(chunk)
            if (
                "accepted contract" in blob
                and "accepted" in text_l
                and "sar" in text_l
            ):
                out.append(chunk)
        return out[:k]

    def fake_chunks_for_docs(self, project_id, doc_ids, k_per_doc=12):
        hits = []
        for chunk in rescue_hits:
            if chunk.doc_id in (doc_ids or []):
                hits.append(chunk)
        return hits

    def fake_title_match(pid, phrase, limit=8):
        if "contract data" not in (phrase or "").lower():
            return []
        return [d for d in seeded if "contract data" in (d.get("original_name") or "").lower()]

    monkeypatch.setattr("app.core.rag.vector_store.VectorStore.search", fake_search)
    monkeypatch.setattr(
        "app.core.rag.vector_store.VectorStore.identifier_search", fake_id_search,
    )
    monkeypatch.setattr(
        "app.core.rag.vector_store.VectorStore.chunks_for_docs",
        fake_chunks_for_docs,
    )
    monkeypatch.setattr(
        "app.core.rag.vector_store.VectorStore.count", lambda self, pid=None: 6,
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
    monkeypatch.delenv("RAG_DELAY_DAMAGES_DAILY_RESCUE", raising=False)
    monkeypatch.delenv("RAG_DELAY_DAMAGES_RATE_RESCUE", raising=False)
    monkeypatch.delenv("RAG_ACA_INCLUDING_VAT_RESCUE", raising=False)
    monkeypatch.delenv("RAG_TIME_FOR_COMPLETION_RESCUE", raising=False)
    monkeypatch.delenv("RAG_ENGINEER_IDENTITY_RESCUE", raising=False)
    monkeypatch.delenv("RAG_CONTRACT_DATA_FILENAME_RESCUE", raising=False)
    monkeypatch.delenv("RAG_LAYERED", raising=False)
    return ret


def _live_lookalikes_and_operands():
    spec = _chunk("spec", SPEC_DOC, 0.93, SPEC_TOC)
    day = _chunk("day", DAY_DOC, 0.91, DAYWORK)
    ins = _chunk("ins", INS_DOC, 0.89, INSURANCE)
    rate = _chunk("rate", RATE_DOC, 0.22, SCANNED_RATE)
    aca = _chunk("aca", ACA_DOC, 0.21, SCANNED_NET_ACA)
    names = {
        SPEC_DOC: SPEC_NAME,
        DAY_DOC: DAYWORK_NAME,
        INS_DOC: INSURANCE_NAME,
        RATE_DOC: CD_SCANNED_NAME,
        ACA_DOC: CD_SCANNED_NAME,
    }
    seeded = [
        {"id": RATE_DOC, "original_name": CD_SCANNED_NAME, "file_path": CD_SCANNED_NAME},
        {"id": ACA_DOC, "original_name": CD_SCANNED_NAME, "file_path": CD_SCANNED_NAME},
    ]
    return spec, day, ins, rate, aca, names, seeded


def test_e1_surfaces_rate_and_excl_aca_when_spec_daywork_insurance_lead(monkeypatch):
    spec, day, ins, rate, aca, names, seeded = _live_lookalikes_and_operands()
    ret = _install_e1_corpus(
        monkeypatch,
        semantic=[spec, day, ins],
        rescue_hits=[rate, aca],
        names=names,
        seeded=seeded,
    )
    chunks, _ = ret.retrieve_with_filter(LIVE_E1, ACTIVE, k=5)
    blob = " ".join(c.text for c in chunks)
    assert chunks, "operands never reached the pool"
    assert RATE in blob
    assert NET_ACA_TXT in blob
    assert "Specification table of contents" not in blob
    assert "Daywork Schedule" not in blob
    assert "Works All Risks" not in blob
    assert all(
        chunk_id in {RATE_DOC, ACA_DOC}
        for chunk_id in (c.doc_id for c in chunks)
    )


def test_e1_compose_from_retrieved_excerpts_states_the_daily_sar(monkeypatch):
    spec, day, ins, rate, aca, names, seeded = _live_lookalikes_and_operands()
    ret = _install_e1_corpus(
        monkeypatch,
        semantic=[spec, day, ins],
        rescue_hits=[rate, aca],
        names=names,
        seeded=seeded,
    )
    chunks, _ = ret.retrieve_with_filter(LIVE_E1, ACTIVE, k=5)
    excerpts = "\n\n".join(c.text or "" for c in chunks)
    out = compose_delay_damages_daily_from_excerpts(LIVE_E1, excerpts)
    assert out is not None
    assert out["daily_amount"] == DAILY
    assert out["rate_percent"] == 0.1
    assert out["contract_amount"] == NET_ACA
    assert out["currency"] == "SAR"

    rag = _sys(*(c.text or "" for c in chunks))
    msgs = [{"role": "user", "content": LIVE_E1}]
    grafted = _graft_composed_delay_damages_daily(CANNOT_CALCULATE, rag, msgs)
    assert "1,754,504.46" in grafted
    assert "cannot calculate" not in grafted.lower()
    posted = _postprocess_answer(CANNOT_CALCULATE, rag, msgs)
    assert "1,754,504.46" in posted
    assert posted != _CG_REFUSAL


def test_e1_live_prefix_and_catalog_ask_both_rescue(monkeypatch):
    spec, day, ins, rate, aca, names, seeded = _live_lookalikes_and_operands()
    ret = _install_e1_corpus(
        monkeypatch,
        semantic=[spec, day, ins],
        rescue_hits=[rate, aca],
        names=names,
        seeded=seeded,
    )
    for ask in (E1_ASK, LIVE_E1):
        chunks, _ = ret.retrieve_with_filter(ask, ACTIVE, k=5)
        blob = " ".join(c.text for c in chunks)
        assert RATE in blob and NET_ACA_TXT in blob, ask


def test_e1_kill_switch_restores_spec_first(monkeypatch):
    spec, day, ins, rate, aca, names, seeded = _live_lookalikes_and_operands()
    ret = _install_e1_corpus(
        monkeypatch,
        semantic=[spec, day, ins],
        rescue_hits=[rate, aca],
        names=names,
        seeded=seeded,
    )
    monkeypatch.setenv("RAG_DELAY_DAMAGES_DAILY_RESCUE", "0")
    chunks, _ = ret.retrieve_with_filter(LIVE_E1, ACTIVE, k=5)
    blob = " ".join(c.text for c in chunks)
    assert chunks
    assert chunks[0].doc_id in {SPEC_DOC, DAY_DOC, INS_DOC}
    assert RATE not in blob
    assert NET_ACA_TXT not in blob
    excerpts = "\n\n".join(c.text or "" for c in chunks)
    assert compose_delay_damages_daily_from_excerpts(LIVE_E1, excerpts) is None


def test_a5_still_surfaces_the_rate_without_requiring_aca(monkeypatch):
    spec, day, ins, rate, aca, names, seeded = _live_lookalikes_and_operands()
    gc = _chunk("gc", GC_DOC, 0.94, GC_8_8)
    names = dict(names)
    names[GC_DOC] = DD23_NAME
    ret = _install_e1_corpus(
        monkeypatch,
        semantic=[gc, spec, day],
        rescue_hits=[rate, aca],
        names=names,
        seeded=seeded,
    )
    chunks, _ = ret.retrieve_with_filter(LIVE_A5, ACTIVE, k=5)
    blob = " ".join(c.text for c in chunks)
    assert RATE in blob
    assert RATE in chunks[0].text
    assert "at the rate stated in the Contract Data" not in blob
    # Exclusive A5 fence: the money row is the neighboring field.
    assert NET_ACA_TXT not in blob
    assert compose_delay_damages_daily_from_excerpts(
        LIVE_A5, blob,
    ) is None


def test_a2_a3_a6_a9_are_not_stolen_onto_the_e1_rescue(monkeypatch):
    spec, _day, _ins, rate, aca, names, _seeded = _live_lookalikes_and_operands()
    tfc = _chunk("tfc", TFC_DOC, 0.84, PREFIXED_TFC)
    dnp = _chunk("dnp", DNP_DOC, 0.83, PREFIXED_DNP)
    names = dict(names)
    names[TFC_DOC] = DD23_NAME
    names[DNP_DOC] = DD23_NAME
    ret = _install_e1_corpus(
        monkeypatch,
        semantic=[spec, tfc, dnp],
        rescue_hits=[rate, aca],
        names=names,
    )
    a3, _ = ret.retrieve_with_filter(A3_ASK, ACTIVE, k=5)
    a6, _ = ret.retrieve_with_filter(A6_ASK, ACTIVE, k=5)
    a2, _ = ret.retrieve_with_filter(A2_ASK, ACTIVE, k=5)
    a9, _ = ret.retrieve_with_filter(A9_ASK, ACTIVE, k=5)
    for label, chunks in (("A3", a3), ("A6", a6), ("A9", a9)):
        assert all(c.doc_id not in {RATE_DOC, ACA_DOC} for c in chunks), label
    assert any("852 days" in (c.text or "") for c in a3)
    assert any("365 days" in (c.text or "") for c in a6)
    # A2 may pull Contract Data via filename rescue; it must not be
    # fenced onto the delay-damages rate row.
    assert all(RATE not in (c.text or "") for c in a2)


def test_incl_vat_twin_does_not_replace_the_net_base(monkeypatch):
    spec, day, ins, rate, aca, names, seeded = _live_lookalikes_and_operands()
    gross = _chunk("gross", GROSS_DOC, 0.20, SCANNED_GROSS_ACA)
    names = dict(names)
    names[GROSS_DOC] = CD_SCANNED_NAME
    seeded = list(seeded) + [
        {"id": GROSS_DOC, "original_name": CD_SCANNED_NAME, "file_path": CD_SCANNED_NAME},
    ]
    ret = _install_e1_corpus(
        monkeypatch,
        semantic=[spec, day, ins],
        rescue_hits=[rate, aca, gross],
        names=names,
        seeded=seeded,
    )
    chunks, _ = ret.retrieve_with_filter(LIVE_E1, ACTIVE, k=5)
    excerpts = "\n\n".join(c.text or "" for c in chunks)
    out = compose_delay_damages_daily_from_excerpts(LIVE_E1, excerpts)
    assert out is not None
    assert out["contract_amount"] == NET_ACA
    assert out["daily_amount"] == DAILY
    assert GROSS_ACA != out["contract_amount"]


def test_e1_is_not_classified_as_a2():
    from app.core.rag.retriever import query_asks_for_aca_including_vat

    assert not query_asks_for_aca_including_vat(LIVE_E1)
    assert not query_asks_for_aca_including_vat(E1_ASK)
    assert query_asks_delay_damages_daily_amount(LIVE_E1)


def test_e1_surfaces_rate_and_excl_when_coc_and_incl_vat_lead(monkeypatch):
    """Live leftover E1 after #523: CoC 8.8 chunks 9–11 + including-VAT.

    The bound Contract Data volume's Sub-Clause 8.8 windows occupied
    top-k (filename keep) and the last-slot money reserve elected the
    A2 including-VAT twin. Compose never saw 0.1%; the answer was
    only SAR 2,017,680,124.69.
    """
    gc9 = _chunk("gc9", GC_DOC, 0.94, GC_8_8)
    gc10 = _chunk("gc10", GC_DOC, 0.93, GC_8_8)
    gc11 = _chunk("gc11", GC_DOC, 0.92, GC_8_8)
    gross = _chunk("gross", GROSS_DOC, 0.91, SCANNED_GROSS_ACA)
    rate = _chunk("rate", RATE_DOC, 0.22, SCANNED_RATE)
    aca = _chunk("aca", ACA_DOC, 0.21, SCANNED_NET_ACA)
    names = {
        GC_DOC: CD_SCANNED_NAME,
        GROSS_DOC: CD_SCANNED_NAME,
        RATE_DOC: CD_SCANNED_NAME,
        ACA_DOC: CD_SCANNED_NAME,
    }
    seeded = [
        {"id": RATE_DOC, "original_name": CD_SCANNED_NAME, "file_path": CD_SCANNED_NAME},
        {"id": ACA_DOC, "original_name": CD_SCANNED_NAME, "file_path": CD_SCANNED_NAME},
        {"id": GROSS_DOC, "original_name": CD_SCANNED_NAME, "file_path": CD_SCANNED_NAME},
        {"id": GC_DOC, "original_name": CD_SCANNED_NAME, "file_path": CD_SCANNED_NAME},
    ]
    ret = _install_e1_corpus(
        monkeypatch,
        semantic=[gc9, gc10, gc11, gross],
        rescue_hits=[rate, aca, gross],
        names=names,
        seeded=seeded,
    )
    chunks, _ = ret.retrieve_with_filter(LIVE_E1, ACTIVE, k=5)
    blob = " ".join(c.text for c in chunks)
    assert RATE in blob
    assert NET_ACA_TXT in blob
    excerpts = "\n\n".join(c.text or "" for c in chunks)
    out = compose_delay_damages_daily_from_excerpts(LIVE_E1, excerpts)
    assert out is not None
    assert out["daily_amount"] == DAILY
    assert out["contract_amount"] == NET_ACA
    assert out["currency"] == "SAR"

    rag = _sys(*(c.text or "" for c in chunks))
    msgs = [{"role": "user", "content": LIVE_E1}]
    live_fail = (
        "The Accepted Contract Amount including VAT is "
        f"{GROSS_ACA_TXT}."
    )
    posted = _postprocess_answer(live_fail, rag, msgs)
    assert "1,754,504.46" in posted
    assert posted != live_fail
    assert posted != _CG_REFUSAL


def test_e1_inject_hint_is_compose_not_including_vat():
    from app.core.rag.inject import format_chunks_as_system_message

    rate = _chunk("rate", RATE_DOC, 0.9, SCANNED_RATE)
    aca = _chunk("aca", ACA_DOC, 0.8, SCANNED_NET_ACA)
    gross = _chunk("gross", GROSS_DOC, 0.7, SCANNED_GROSS_ACA)
    e1 = format_chunks_as_system_message(
        [rate, aca, gross], 1, query=LIVE_E1,
    )["content"]
    assert "DELAY DAMAGES PER CALENDAR DAY" in e1
    assert "including VAT. That IS the answer" not in e1
    a2 = format_chunks_as_system_message(
        [gross], 1, query=LIVE_PREFIX + A2_ASK,
    )["content"]
    assert "INCLUDING VAT" in a2
    assert "Lead with that including-VAT figure" in a2


def test_e1_surfaces_excl_vat_when_coc_chunks_carry_toy_ten_million(monkeypatch):
    """Live leftover E1 on c5c6dfa: chunks 9–11 + toy ACA 10,000,000.

    The bound Contract Data volume's Sub-Clause 8.8 windows stated the
    0.1% rate and a FIDIC worked-example ACA of SAR 10,000,000. That
    money satisfied the reservation, so the filled excl-VAT row never
    entered top-k and compose emitted SAR 10,000/day.
    """
    gc9 = _chunk("gc9", GC_DOC, 0.94, COC_8_8_TOY_ACA)
    gc10 = _chunk("gc10", GC_DOC, 0.93, COC_8_8_TOY_ACA)
    gc11 = _chunk("gc11", GC_DOC, 0.92, COC_8_8_TOY_ACA)
    rate = _chunk("rate", RATE_DOC, 0.22, SCANNED_RATE)
    aca = _chunk("aca", ACA_DOC, 0.21, SCANNED_NET_ACA)
    names = {
        GC_DOC: CD_SCANNED_NAME,
        RATE_DOC: CD_SCANNED_NAME,
        ACA_DOC: CD_SCANNED_NAME,
    }
    seeded = [
        {"id": RATE_DOC, "original_name": CD_SCANNED_NAME, "file_path": CD_SCANNED_NAME},
        {"id": ACA_DOC, "original_name": CD_SCANNED_NAME, "file_path": CD_SCANNED_NAME},
        {"id": GC_DOC, "original_name": CD_SCANNED_NAME, "file_path": CD_SCANNED_NAME},
    ]
    ret = _install_e1_corpus(
        monkeypatch,
        semantic=[gc9, gc10, gc11],
        rescue_hits=[rate, aca],
        names=names,
        seeded=seeded,
    )
    chunks, _ = ret.retrieve_with_filter(LIVE_E1, ACTIVE, k=5)
    blob = " ".join(c.text for c in chunks)
    assert RATE in blob
    assert NET_ACA_TXT in blob
    excerpts = "\n\n".join(c.text or "" for c in chunks)
    out = compose_delay_damages_daily_from_excerpts(LIVE_E1, excerpts)
    assert out is not None
    assert out["daily_amount"] == DAILY
    assert out["contract_amount"] == NET_ACA
    assert out["currency"] == "SAR"
    assert out["contract_amount"] != 10_000_000.0

    rag = _sys(*(c.text or "" for c in chunks))
    msgs = [{"role": "user", "content": LIVE_E1}]
    live_fail = (
        "Delay damages for the whole of the Works are "
        "SAR 10,000.00 per calendar day "
        f"(0.1% of Accepted Contract Amount {TOY_ACA_TXT})."
    )
    posted = _postprocess_answer(live_fail, rag, msgs)
    assert "1,754,504.46" in posted
    assert posted != live_fail
    assert posted != _CG_REFUSAL
    assert "10,000,000.00" not in posted.split("\n", 1)[0]
