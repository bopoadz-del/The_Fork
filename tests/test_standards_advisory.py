"""Standards advisory — ADVISORY, never blocking.

Wires the previously-dormant construction_knowledge.enforce_critical_rules into
the agent answer path: a deviation from a critical standard (e.g. 'APPROVED' on
a design document, PRC-501) appends a flagged note but NEVER rejects, edits, or
halts the answer. Operators bend rules deliberately; the platform flags, it does
not stop.
"""
from __future__ import annotations

from app.agents.runtime import (
    _standards_advisory,
    _postprocess_answer,
    _CG_REFUSAL,
)

_DEVIATION = "The design drawing package has been APPROVED by the consultant."


def test_deviation_is_flagged_not_blocked():
    out = _standards_advisory(_DEVIATION)
    # Original answer is preserved verbatim (task performed, not halted)...
    assert out.startswith(_DEVIATION)
    # ...and a flagged, non-blocking note is appended.
    assert "Standards note" in out
    assert "not blocking" in out.lower()
    assert "PRC-501" in out


def test_clean_answer_is_untouched():
    clean = "The RFI was issued to the engineer for the pier reinforcement query."
    assert _standards_advisory(clean) == clean


def test_answer_content_is_never_removed():
    # The deviation note is additive only — every character of the answer stays.
    out = _standards_advisory(_DEVIATION)
    assert _DEVIATION in out
    assert len(out) > len(_DEVIATION)


def test_advisory_disabled_by_env(monkeypatch):
    monkeypatch.setenv("STANDARDS_ADVISORY", "0")
    assert _standards_advisory(_DEVIATION) == _DEVIATION


def test_postprocess_runs_cost_gate_then_advisory():
    # A grounded, non-cost answer with a standards deviation: cost gate passes it
    # (no money figure), advisory appends the note. Answer preserved + flagged.
    out = _postprocess_answer(_DEVIATION, rag_sys_msg=None, messages=[])
    assert out.startswith(_DEVIATION)
    assert "Standards note" in out


def test_postprocess_refused_cost_answer_is_not_annotated():
    # An ungrounded cost claim is refused by the cost gate; the refusal text has
    # no design-APPROVED deviation, so no standards note is bolted onto a refusal.
    ungrounded_cost = "The design package rate is 999 SAR/m3."
    out = _postprocess_answer(ungrounded_cost, rag_sys_msg=None, messages=[])
    assert out == _CG_REFUSAL  # refused, and not annotated
