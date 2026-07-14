"""Cost-grounding gate — a chat cost/rate figure must trace to a rate-semantic
retrieved chunk or a computed tool result, else the answer is refused.

Anchored on the 2026-07-14 live incident: a cost query answered "450 SAR/m³"
by lifting "450" out of a DG2 drawing dimension-table chunk. The gate MUST fail
that answer even though "450" is present in retrieval (it is NOT present in rate
semantics), and MUST pass a real rate answer grounded in the rates chunk.
"""
from __future__ import annotations

from app.agents.runtime import _cost_grounding_gate, _CG_REFUSAL


def _sys(*chunk_texts: str) -> dict:
    """Build a RAG system message the way format_chunks_as_system_message does:
    each chunk prefixed with a [doc_id=... chunk=N ...] marker."""
    body = "\n\n".join(
        f"[doc_id=doc{i} chunk={i} score=0.80] {t}" for i, t in enumerate(chunk_texts)
    )
    return {"role": "system", "content": "Reference context:\n" + body}


# ── The preserved live incident (must FAIL the gate) ────────────────────────
# Verbatim shape of the retrieved DG2 drawing dimension-table chunk: pure
# numbers + bend/pipe labels. "450" IS in this text — but not as a rate.
_DRAWING_SOUP = (
    "700 100 100 100 100 620 450 350 300 1960 11950 5350 1250 1850 9650 4300 "
    "1050 1820 8450 3750 950 11.25 BEND 22.5 BEND 45 BEND TEE H mm A mm B C "
    "200 500 500 900 1250 3400 2000 450 900 2100 1700 250 600 1400 1350"
)
_FABRICATED_ANSWER = (
    "The unit rate for ready-mix concrete (250 kg/cm2) in Saudi Arabia is "
    "450 SAR per m3.\n\nSource: construction_rates_gulf_ksa_2025.md, chunk 52."
)


def test_incident_fabrication_is_refused_even_though_number_is_in_retrieval():
    out = _cost_grounding_gate(_FABRICATED_ANSWER, _sys(_DRAWING_SOUP), messages=[])
    # 450 is present in the drawing soup, but not in rate semantics -> refuse.
    assert out == _CG_REFUSAL
    assert "450" not in out


# ── The real rate answer (must PASS) ────────────────────────────────────────
_RATE_CHUNK = (
    "## Detailed material prices — Saudi Arabia (SAR) | Material | Unit | Price "
    "range (SAR) | Ready-mix concrete (250 kg/cm2) | /m3 | 220 — 280 | "
    "Source: Turner & Townsend KSAMI 2025."
)


def test_real_rate_answer_grounded_in_rate_chunk_passes():
    answer = (
        "Ready-mix concrete (250 kg/cm2) in Saudi Arabia is about 220–280 SAR/m3 "
        "(indicative; Turner & Townsend KSAMI 2025)."
    )
    out = _cost_grounding_gate(answer, _sys(_RATE_CHUNK), messages=[])
    assert out == answer  # untouched — figures trace to the rate-semantic chunk


def test_rate_chunk_present_but_wrong_number_still_refused():
    # The rate chunk grounds 220–280; an answer claiming 450 is still ungrounded.
    answer = "The rate is 450 SAR/m3."
    out = _cost_grounding_gate(answer, _sys(_RATE_CHUNK, _DRAWING_SOUP), messages=[])
    assert out == _CG_REFUSAL


def test_non_cost_answer_is_never_touched():
    answer = "The project has 12 work packages and 3 open RFIs across 2 zones."
    out = _cost_grounding_gate(answer, _sys(_DRAWING_SOUP), messages=[])
    assert out == answer  # no money/rate figure -> passes through


def test_tool_computed_cost_is_grounded():
    # A real cost_estimate tool result is authoritative: its numbers ground the
    # prose even without a RAG rate chunk (prevents false refusal of deliverables).
    answer = "Estimated total cost is SAR 1,250,000 for the concrete package."
    tool_msgs = [{"role": "tool", "content": '{"total_cost": 1250000, "currency": "SAR"}'}]
    out = _cost_grounding_gate(answer, None, messages=tool_msgs)
    assert out == answer


def test_dimension_not_mistaken_for_rate():
    # "450 mm" in the answer is a dimension, not a rate -> not a money figure ->
    # never triggers the gate.
    answer = "Provide a 450 mm pipe with 200 mm bedding."
    out = _cost_grounding_gate(answer, _sys(_DRAWING_SOUP), messages=[])
    assert out == answer


def test_gate_disabled_by_env(monkeypatch):
    monkeypatch.setenv("COST_GROUNDING_GATE", "0")
    out = _cost_grounding_gate(_FABRICATED_ANSWER, _sys(_DRAWING_SOUP), messages=[])
    assert out == _FABRICATED_ANSWER  # flag off -> pass through unchanged
