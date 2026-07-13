"""Phase-2 grounding gate — confidence/validation stamp gating (increment 1).

The predefined-dispatch synthesis prompt TELLS the LLM to ground every figure in
the tool result, but nothing ENFORCES it. This gate FLAGS (never blocks) a
validation/confidence stamp asserted in the prose that the computed result does
not back with a corresponding field. It must fire on an unbacked stamp and must
NOT fire on (a) a stamp the result actually backs, or (b) legitimate checklist /
instruction language ("verify the torque"), which is the dominant false-positive
risk.
"""

from __future__ import annotations

from app.core.predefined_reasoning import _gate_confidence_stamps as gate

_CAVEAT = "treat any"


def _flagged(text, result):
    return _CAVEAT in gate(text, result)


class TestStampFiresWhenUnbacked:
    def test_unbacked_confidence_stamp_flags(self):
        assert _flagged("Analysis complete. Confidence: High.", {}) is True

    def test_unbacked_confidence_percent_flags(self):
        assert _flagged("Result computed with 95% confidence.", {"status": "success"}) is True

    def test_unbacked_validation_stamp_flags(self):
        assert _flagged("Validation passed on all line items.", {"total": 5}) is True

    def test_unbacked_quality_assured_flags(self):
        assert _flagged("This is a quality-assured deliverable.", {}) is True


class TestStampNotFlaggedWhenBacked:
    def test_backed_confidence_field(self):
        assert _flagged("Confidence: High.", {"confidence": 0.92}) is False

    def test_backed_validation_field(self):
        assert _flagged("Verified against the calculation engine.",
                        {"validation": {"pass": True}}) is False

    def test_backed_checks_field(self):
        assert _flagged("Quality-assured result.", {"checks": [{"pass": True}]}) is False

    def test_backed_source_note_field(self):
        # historical_benchmark carries source_note/confidence — a real backing.
        assert _flagged("Confidence: medium.", {"source_note": "2024 rate book"}) is False

    def test_backing_field_nested_deep(self):
        assert _flagged("Confidence: high.",
                        {"a": {"b": [{"confidence_score": 0.8}]}}) is False


class TestZeroFalsePositiveOnLegitLanguage:
    """The dominant FP risk: checklist / instruction prose that uses
    'verify'/'validate' as verbs, and plain figures — none are stamps."""

    def test_instructional_verify_not_flagged(self):
        assert _flagged("Verify the torque settings before energizing.", {}) is False

    def test_validated_by_person_not_flagged(self):
        assert _flagged("Ensure all connections are validated by the QA engineer.", {}) is False

    def test_verification_noun_not_flagged(self):
        # Real commissioning-checklist language observed live.
        assert _flagged("Perform verification of relay settings and record results.", {}) is False

    def test_plain_and_money_figures_not_flagged(self):
        assert _flagged("Total: 700 man-hours across 42 trades. Peak Week 1.", {}) is False
        assert _flagged("Net due this period: SAR 120,000.", {}) is False


class TestGateNeverBlocks:
    def test_flag_appends_never_replaces(self):
        original = "Analysis complete. Confidence: High."
        out = gate(original, {})
        # The original text is preserved in full; only a caveat is appended.
        assert out.startswith(original)
        assert len(out) > len(original)
