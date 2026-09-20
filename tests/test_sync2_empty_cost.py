"""SYNC2 Agent A — empty turns after calc (A2) and user-supplied rates (A3).

Live master_corpus FAILs on 72fcfd2:

A2-1  plastering 3,400 m2 @ 42 m2/gang-day, gang SAR 1,950
      → construction_calc ×6 then raw JSON
      ``{"status":"error","error":"Unknown calculation 'None'."}``
A2-2  weight of 12 tonnes of Y16 bars in metres run
      → construction_calc then "I was unable to generate a response…"

A3-1  14 pad footings 2.4×2.4×0.6 m @ SAR 410/m3 + 5% waste + 10% contingency
      → refused "I don't have a rate on file" despite the user giving 410
A3-2  500 m of 300 mm uPVC @ SAR 95 per metre
      → refused the user-supplied 95
"""
from __future__ import annotations

import json

from app.agents.runtime import (
    _CG_REFUSAL,
    _EMPTY_RESPONSE_FALLBACK,
    _cost_grounding_gate,
    _format_tool_error_plain,
    _looks_like_tool_error_json,
    _plain_sentence_for_tool_error_json,
    _postprocess_answer,
    _recover_answer_from_tool_messages,
    _sanitize_final_text,
    _text_needs_tool_recovery,
)


# ── Live asks (synthetic figures only) ──────────────────────────────────────

A3_PADS = (
    "Take off concrete volume for 14 pad footings each 2.4×2.4×0.6 m, "
    "price at SAR 410/m3, include 5% waste and 10% contingency"
)
A3_PIPE = "Price 500 m of 300 mm uPVC drainage pipe at SAR 95 per metre"

# 14 × 2.4 × 2.4 × 0.6 = 48.384; ×1.05 = 50.8032; ×410 = 20,829.31; ×1.10 = 22,912.24
A3_PADS_ANSWER = (
    "Net volume 14 × 2.4 × 2.4 × 0.6 = 48.384 m3. "
    "With 5% waste: 50.803 m3. At the instructed SAR 410/m3 = SAR 20,829.31. "
    "Plus 10% contingency: SAR 22,912.24."
)
A3_PIPE_ANSWER = (
    "500 m × SAR 95 per metre = SAR 47,500 (operator-instructed rate)."
)

UNKNOWN_NONE = json.dumps({
    "status": "error",
    "error": "Unknown calculation 'None'.",
    "available": ["concrete_volume", "rebar_weight", "excavation_volume"],
    "request_id": "req-sync2-a2",
})

REBAR_OK = {
    "status": "success",
    "calculation": "rebar_weight",
    "result": {
        "unit_mass_kg_m": 1.578,
        "total_mass_kg": 12000.0,
        "total_mass_t": 12.0,
        "note": (
            "Unit mass = 1.578 kg/m for Y16; 12 tonnes is 12,000 kg → "
            "7,605 metres run."
        ),
    },
}


def _user(text: str) -> dict:
    return {"role": "user", "content": text}


def _tool(payload, *, name="construction_calc") -> dict:
    return {"role": "tool", "name": name, "content": json.dumps(payload)}


# ── A3: user-supplied rates ground derived arithmetic ───────────────────────


def test_a3_pad_footings_user_rate_with_waste_and_contingency_passes():
    msgs = [_user(A3_PADS)]
    assert _cost_grounding_gate(A3_PADS_ANSWER, None, msgs) == A3_PADS_ANSWER
    assert _cost_grounding_gate(A3_PADS_ANSWER, None, msgs) != _CG_REFUSAL


def test_a3_pipe_user_rate_times_qty_passes():
    msgs = [_user(A3_PIPE)]
    assert _cost_grounding_gate(A3_PIPE_ANSWER, None, msgs) == A3_PIPE_ANSWER
    assert "47,500" in _cost_grounding_gate(A3_PIPE_ANSWER, None, msgs)


def test_a3_plastering_user_gang_rate_times_days_passes():
    ask = (
        "How much will the remaining 3,400 m2 of plastering cost if "
        "productivity stays at 42 m2 per gang-day and a gang costs "
        "SAR 1,950 per day?"
    )
    answer = "3,400 / 42 gang-days × SAR 1,950 = SAR 157,857.14."
    msgs = [_user(ask)]
    assert _cost_grounding_gate(answer, None, msgs) == answer


def test_a3_invented_rate_still_refused():
    msgs = [_user("What is the unit rate for ready-mix C40?")]
    answer = "Use SAR 650/m3 for C40 ready-mix."
    assert _cost_grounding_gate(answer, None, msgs) == _CG_REFUSAL


def test_a3_user_gave_410_but_answer_invents_650_refused():
    msgs = [_user(A3_PADS)]
    answer = "Price the pads at SAR 650/m3 — that is the market rate."
    assert _cost_grounding_gate(answer, None, msgs) == _CG_REFUSAL


# ── A2: tool-error JSON never reaches the user ──────────────────────────────


def test_a2_unknown_calculation_json_is_detected():
    assert _looks_like_tool_error_json(UNKNOWN_NONE) is True
    assert _looks_like_tool_error_json('{"answer": "ok", "result": 1}') is False
    assert _looks_like_tool_error_json("Plain sentence about a calculation.") is False


def test_a2_sanitize_replaces_unknown_calc_json_with_plain_sentence():
    out = _sanitize_final_text(UNKNOWN_NONE)
    assert "{" not in out
    assert "Unknown calculation" not in out
    assert "available" not in out
    assert "calculator" in out.lower() or "formula" in out.lower()
    assert "req-sync2-a2" in out


def test_a2_markdown_fenced_error_json_is_also_plain():
    fenced = "```json\n" + UNKNOWN_NONE + "\n```"
    out = _sanitize_final_text(fenced)
    assert "{" not in out
    assert "req-sync2-a2" in out


def test_a2_format_keeps_request_id_from_payload():
    sentence = _format_tool_error_plain(
        {"status": "error", "error": "Unknown calculation 'None'.",
         "request_id": "abc-123"},
    )
    assert "Request id: abc-123" in sentence
    assert "{" not in sentence


def test_a2_empty_after_failed_calc_is_plain_sentence_not_json():
    msgs = [
        _user("How much will the remaining 3,400 m2 of plastering cost "
              "if productivity stays at 42 m2 per gang-day and a gang "
              "costs SAR 1,950 per day?"),
        _tool(json.loads(UNKNOWN_NONE)),
    ]
    recovered = _recover_answer_from_tool_messages(_EMPTY_RESPONSE_FALLBACK, msgs)
    assert recovered != _EMPTY_RESPONSE_FALLBACK
    assert "{" not in recovered
    assert "unable to generate" not in recovered.lower()
    assert "calculator" in recovered.lower() or "formula" in recovered.lower()


def test_a2_empty_after_successful_calc_uses_tool_note():
    msgs = [
        _user("What is the weight of 12 tonnes of Y16 bars in metres run?"),
        _tool(REBAR_OK),
    ]
    recovered = _recover_answer_from_tool_messages(_EMPTY_RESPONSE_FALLBACK, msgs)
    assert recovered != _EMPTY_RESPONSE_FALLBACK
    assert "unable to generate" not in recovered.lower()
    assert "7,605" in recovered or "1.578" in recovered
    assert "{" not in recovered


def test_a2_postprocess_empty_turn_after_calc_is_not_silence():
    msgs = [
        _user("What is the weight of 12 tonnes of Y16 bars in metres run?"),
        _tool(REBAR_OK),
    ]
    out = _postprocess_answer("", None, msgs)
    assert out.strip()
    assert out != _EMPTY_RESPONSE_FALLBACK
    assert "unable to generate" not in out.lower()
    assert "7,605" in out or "1.578" in out


def test_a2_postprocess_error_json_is_not_raw_json():
    msgs = [
        _user("How much will the remaining 3,400 m2 of plastering cost "
              "if a gang costs SAR 1,950 per day?"),
        _tool(json.loads(UNKNOWN_NONE)),
    ]
    raw = _sanitize_final_text(UNKNOWN_NONE)
    out = _postprocess_answer(raw, None, msgs)
    assert "{" not in out
    assert out != _CG_REFUSAL
    assert "unable to generate" not in out.lower()


def test_a2_text_needs_recovery_on_error_json_and_empty():
    assert _text_needs_tool_recovery("")
    assert _text_needs_tool_recovery(_EMPTY_RESPONSE_FALLBACK)
    assert _text_needs_tool_recovery(UNKNOWN_NONE)
    assert not _text_needs_tool_recovery("Y16 unit mass is 1.578 kg/m.")


def test_a2_plain_helper_matches_sanitize():
    assert _plain_sentence_for_tool_error_json(UNKNOWN_NONE) == _sanitize_final_text(
        UNKNOWN_NONE
    )
