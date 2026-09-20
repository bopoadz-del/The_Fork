"""Fleet Sync 2 — empty-turn recovery, user-derived cost grounding, cut-off.

Live 72fcfd2: a successful construction_calc can still ship
``_EMPTY_RESPONSE_FALLBACK``; the cost gate grounds qty×rate but refuses
the same figures once the answer adds a×b×c, a per-unit line, or
a×(1+p%) waste/contingency from numbers the user typed; a mid-stream
``_SynthStreamError`` with tokens already out is saved as a complete
answer.

Synthetic data only. No live client amounts.
"""
from __future__ import annotations

import json

from app.agents.runtime import (
    _CG_REFUSAL,
    _EMPTY_RESPONSE_FALLBACK,
    _STREAM_CUTOFF_NOTICE,
    _cost_grounding_gate,
    _format_construction_calc,
    _mark_stream_cutoff,
    _recover_answer_from_tool_messages,
)

# ── EMPTY TURNS ─────────────────────────────────────────────────────────────

_PLASTER_ASK = (
    "How much will the remaining 3,400 m2 of plastering cost if productivity "
    "stays at 42 m2 per gang-day and a gang costs SAR 1,900?"
)
_PLASTER_ENVELOPE = {
    "status": "success",
    "calculation": "productivity_rate",
    "result": {
        "rate_per_hour": 42.0,
        "gang_days": 80.952,
        "total_cost": 153809.52,
        "note": (
            "3,400 m2 / 42 m2 per gang-day = 80.952 gang-days; "
            "x SAR 1,900 = SAR 153,809.52."
        ),
    },
}

_Y16_ASK = "What is the weight of 12 tonnes of Y16 bars in metres run?"
_Y16_ENVELOPE = {
    "status": "success",
    "calculation": "rebar_weight",
    "result": {
        "unit_mass_kg_m": 1.578,
        "total_mass_t": 12.0,
        "total_bar_length_m": 7604.56,
        "note": (
            "Y16 unit mass 1.578 kg/m; 12 t = 12,000 kg / 1.578 = "
            "7,604.56 m run."
        ),
    },
}


def _tool_msgs(ask: str, envelope: dict) -> list[dict]:
    return [
        {"role": "user", "content": ask},
        {
            "role": "tool",
            "name": "construction_calc",
            "content": json.dumps(envelope),
        },
    ]


def test_empty_fallback_recovers_plastering_calc():
    recovered = _recover_answer_from_tool_messages(
        _EMPTY_RESPONSE_FALLBACK, _tool_msgs(_PLASTER_ASK, _PLASTER_ENVELOPE),
    )
    assert recovered != _EMPTY_RESPONSE_FALLBACK
    assert "153,809.52" in recovered or "153809.52" in recovered
    assert "unable to generate" not in recovered.lower()


def test_empty_fallback_recovers_y16_metres_run():
    recovered = _recover_answer_from_tool_messages(
        _EMPTY_RESPONSE_FALLBACK, _tool_msgs(_Y16_ASK, _Y16_ENVELOPE),
    )
    assert recovered != _EMPTY_RESPONSE_FALLBACK
    assert "7604.56" in recovered or "7,604.56" in recovered
    assert "unable to generate" not in recovered.lower()


def test_format_generic_calc_keeps_concrete_volume_path():
    envelope = {
        "status": "success",
        "calculation": "concrete_volume",
        "result": {
            "volume_m3": 945.0,
            "net_volume_m3": 900.0,
            "waste_factor": 0.05,
            "note": "Net = 30*20*1.5 = 900.000 m3; +5% waste = 945.000 m3.",
        },
    }
    out = _format_construction_calc(envelope)
    assert "945" in out


def test_failed_calc_is_not_recovered_as_an_answer():
    envelope = {
        "status": "error",
        "calculation": "productivity_rate",
        "error": "labor_hours must be > 0",
    }
    recovered = _recover_answer_from_tool_messages(
        _EMPTY_RESPONSE_FALLBACK, _tool_msgs(_PLASTER_ASK, envelope),
    )
    assert recovered == _EMPTY_RESPONSE_FALLBACK


# ── COST GATE: user-derived a×b×c and a×(1+p%) ──────────────────────────────

_PAD_ASK = (
    "Take off the concrete volume for 14 pad footings, each 2.4 x 2.4 x 0.6 m, "
    "and price it at SAR 410 per m3."
)
_PAD_MSGS = [{"role": "user", "content": _PAD_ASK}]
_PIPE_ASK = "Price 500 m of 300 mm uPVC drainage pipe at SAR 95 per metre"
_PIPE_MSGS = [{"role": "user", "content": _PIPE_ASK}]


def test_pad_footing_qty_times_user_rate_passes():
    answer = "48.384 m3 x SAR 410 = SAR 19,837.44"
    assert _cost_grounding_gate(answer, None, _PAD_MSGS) == answer


def test_pad_footing_with_per_footing_line_passes():
    answer = (
        "Net volume 48.384 m3. Per footing 3.456 m3 x SAR 410 = SAR 1,416.96. "
        "Total 48.384 m3 x SAR 410 = SAR 19,837.44."
    )
    assert _cost_grounding_gate(answer, None, _PAD_MSGS) == answer


def test_pad_footing_with_five_percent_waste_passes():
    answer = (
        "Net 48.384 m3 x SAR 410 = SAR 19,837.44. "
        "Including 5% waste: 50.8032 m3 x SAR 410 = SAR 20,829.31."
    )
    assert _cost_grounding_gate(answer, None, _PAD_MSGS) == answer


def test_pad_footing_with_ten_percent_contingency_passes():
    answer = (
        "48.384 m3 x SAR 410 = SAR 19,837.44. "
        "Add 10% contingency: SAR 21,821.18."
    )
    assert _cost_grounding_gate(answer, None, _PAD_MSGS) == answer


def test_user_supplied_pipe_rate_times_length_passes():
    answer = "500 m x SAR 95 per metre = SAR 47,500."
    assert _cost_grounding_gate(answer, None, _PIPE_MSGS) == answer


def test_invented_rate_still_refused_on_pad_footing_ask():
    answer = "Use SAR 465 per m3; total SAR 22,498.56."
    assert _cost_grounding_gate(answer, None, _PAD_MSGS) == _CG_REFUSAL


def test_invented_rate_still_refused_on_pipe_ask():
    answer = "I would allow SAR 140 per metre; total SAR 70,000."
    assert _cost_grounding_gate(answer, None, _PIPE_MSGS) == _CG_REFUSAL


# ── CUT-OFF MARKER ──────────────────────────────────────────────────────────

def test_cutoff_marker_appended_once():
    partial = "The volume is 48.384 m3 and the extension is SAR 19,837.44."
    marked = _mark_stream_cutoff(partial)
    assert marked.startswith(partial)
    assert _STREAM_CUTOFF_NOTICE in marked
    assert marked.count(_STREAM_CUTOFF_NOTICE) == 1
    assert _mark_stream_cutoff(marked).count(_STREAM_CUTOFF_NOTICE) == 1


def test_cutoff_marker_on_empty_partial():
    assert _mark_stream_cutoff("") == _STREAM_CUTOFF_NOTICE
    assert _mark_stream_cutoff("   ") == _STREAM_CUTOFF_NOTICE
