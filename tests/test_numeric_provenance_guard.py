"""Numeric-provenance guard — cited currency amounts must appear digit-verbatim
in retrieved chunk text (A2 model-hallucination class).

Synthetic North Spur Contract Data only — never live client figures.
"""
from __future__ import annotations

from app.agents.runtime import (
    _numeric_provenance_gate,
    _NP_REFUSAL,
    _postprocess_answer,
)


def _sys(*chunk_texts: str) -> dict:
    body = "\n\n".join(
        f"[doc_id=north_spur chunk={i} score=0.90] {t}"
        for i, t in enumerate(chunk_texts)
    )
    return {"role": "system", "content": "Reference context:\n" + body}


# Sanitized North Spur Contract Data particulars (synthetic).
_NORTH_SPUR_CHUNK = (
    "CONTRACT DATA particulars — filled-in amount / duration / percentage\n"
    "1.1.1 Accepted Contract Amount excluding VAT | SAR 1,234,567.89\n"
    "(One Million Two Hundred Thirty-Four Thousand Five Hundred Sixty-Seven "
    "Saudi Riyals and Eighty-Nine Halalas)"
)

_GROUNDED_ANSWER = (
    "Per Contract Data 1.1.1, the Accepted Contract Amount excluding VAT is "
    "SAR 1,234,567.89."
)

# Near-miss hallucination: same magnitude, wrong digits (A2 shape).
_HALLUCINATED_ANSWER = (
    "Per Contract Data 1.1.1, the Accepted Contract Amount excluding VAT is "
    "SAR 1,274,567.89."
)


def test_grounded_contract_amount_passes():
    out = _numeric_provenance_gate(
        _GROUNDED_ANSWER, _sys(_NORTH_SPUR_CHUNK), messages=[],
    )
    assert out == _GROUNDED_ANSWER
    assert "1,234,567.89" in out


def test_near_miss_hallucinated_amount_is_refused():
    out = _numeric_provenance_gate(
        _HALLUCINATED_ANSWER, _sys(_NORTH_SPUR_CHUNK), messages=[],
    )
    assert out == _NP_REFUSAL
    assert "1,274,567.89" not in out
    assert "1,234,567.89" not in out


def test_tool_result_can_ground_amount():
    answer = "Computed package total is SAR 1,234,567.89."
    tool_msgs = [
        {"role": "tool", "content": '{"total": 1234567.89, "currency": "SAR"}'},
    ]
    out = _numeric_provenance_gate(answer, None, messages=tool_msgs)
    assert out == answer


def test_user_supplied_amount_grounds_echo():
    answer = "You stated the sum is SAR 9,876,543.21; noted."
    msgs = [{"role": "user", "content": "Variance vs SAR 9,876,543.21 — explain."}]
    out = _numeric_provenance_gate(answer, _sys(_NORTH_SPUR_CHUNK), messages=msgs)
    assert out == answer


def test_non_money_answer_untouched():
    answer = "Clause 1.1.1 defines Accepted Contract Amount; see Contract Data."
    out = _numeric_provenance_gate(answer, _sys(_NORTH_SPUR_CHUNK), messages=[])
    assert out == answer


def test_kill_switch_disables_guard(monkeypatch):
    monkeypatch.setenv("NUMERIC_PROVENANCE_GUARD", "0")
    out = _numeric_provenance_gate(
        _HALLUCINATED_ANSWER, _sys(_NORTH_SPUR_CHUNK), messages=[],
    )
    assert out == _HALLUCINATED_ANSWER


def test_postprocess_runs_numeric_guard(monkeypatch):
    """Near-miss within cost-gate float tolerance must still be caught by
    digit-verbatim provenance (A2 class)."""
    monkeypatch.setenv("NUMERIC_PROVENANCE_GUARD", "1")
    monkeypatch.setenv("COST_GROUNDING_GATE", "1")
    # ~0.44% off 1,234,567.89 — inside cost-gate 0.5% tolerance, wrong digits.
    near_miss = (
        "Per Contract Data 1.1.1, the Accepted Contract Amount excluding VAT is "
        "SAR 1,240,000."
    )
    out = _postprocess_answer(
        near_miss,
        _sys(_NORTH_SPUR_CHUNK),
        messages=[
            {
                "role": "user",
                "content": "What is the Accepted Contract Amount excluding VAT?",
            },
        ],
    )
    assert out == _NP_REFUSAL
    assert "1,240,000" not in out


def test_empty_corpus_refuses_money_figure():
    out = _numeric_provenance_gate(
        "The sum is SAR 1,234,567.89.", None, messages=[],
    )
    assert out == _NP_REFUSAL
