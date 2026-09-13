"""A synonym-named Contract Data figure keeps one reserved slot in the top-k.

Live gap (2026-09-13): "contract sum before VAT" answered "I don't have it"
though the Accepted Contract Amount document was retrieved. The retriever's
synonym expansion (#591) pulls the canonical chunk into the candidate POOL, but
its cosine to the diluted synonym query is lower than the primary query's own
top hits, so the score-based top-k cut drops it before the model sees it. The
answer-mapping hint (#592) then has nothing to map. Multi-sampled recall for
ACA/"contract sum" stayed 0/3.

reserve_contract_synonym_row mirrors reserve_monetary_base_row: it gives the
canonical-heading chunk one reserved slot (replacing the lowest-ranked
survivor, k unchanged) when the query uses such a synonym.
"""
from __future__ import annotations

from app.core.rag.retriever import reserve_contract_synonym_row
from app.core.rag.vector_store import Chunk

ACA_TEXT = (
    "CONTRACT DATA 1.1.1 Accepted Contract Amount (excluding VAT) "
    "SAR 1,754,504,456.25"
)


def _c(cid: str, text: str) -> Chunk:
    return Chunk(chunk_id=cid, project_id="p", doc_id="d" + cid,
                 chunk_index=0, text=text, score=0.5)


def _kept_without_aca(n: int = 5) -> list[Chunk]:
    return [_c(str(i), f"variation order commercial row {i}") for i in range(n)]


def test_reserves_aca_chunk_for_contract_sum_synonym():
    kept = _kept_without_aca()
    aca = _c("aca", ACA_TEXT)
    ranked = kept + [aca]
    assert reserve_contract_synonym_row(
        "What is the contract sum before VAT as a number?", kept, ranked
    )
    assert any(c.chunk_id == "aca" for c in kept)
    assert len(kept) == 5  # k unchanged — a reservation, not an append


def test_no_swap_when_canonical_already_present():
    aca = _c("aca", ACA_TEXT)
    kept = [aca] + _kept_without_aca(4)
    assert reserve_contract_synonym_row(
        "contract sum before VAT", kept, kept
    ) is False


def test_no_swap_for_unrelated_query():
    kept = _kept_without_aca()
    ranked = kept + [_c("aca", ACA_TEXT)]
    assert reserve_contract_synonym_row(
        "What concrete grade is specified?", kept, ranked
    ) is False


def test_no_swap_for_definition_question():
    kept = _kept_without_aca()
    ranked = kept + [_c("aca", ACA_TEXT)]
    assert reserve_contract_synonym_row(
        "What does contract sum mean?", kept, ranked
    ) is False


def test_respects_contract_scope_allow():
    kept = _kept_without_aca()
    aca = _c("aca", ACA_TEXT)
    ranked = kept + [aca]
    # allow rejects the ACA chunk -> no swap, fence is honoured.
    assert reserve_contract_synonym_row(
        "contract sum before VAT", kept, ranked,
        allow=lambda c: c.chunk_id != "aca",
    ) is False
    assert not any(c.chunk_id == "aca" for c in kept)


def test_reserves_delay_cap_and_defects_headings():
    cap = _c("cap", "Maximum Amount of Delay Damages 10% of the Contract Price")
    kept = _kept_without_aca()
    assert reserve_contract_synonym_row(
        "What is the cap on delay damages?", kept, kept + [cap]
    )
    assert any(c.chunk_id == "cap" for c in kept)

    dnp = _c("dnp", "Defects Notification Period 365 days from Taking Over")
    kept2 = _kept_without_aca()
    assert reserve_contract_synonym_row(
        "What is the maintenance period?", kept2, kept2 + [dnp]
    )
    assert any(c.chunk_id == "dnp" for c in kept2)
