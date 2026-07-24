"""Low-confidence routes must not intercept lookup questions.

Live repro (2026-07-24 golden gate, fresh_eot_notice_period): a contract
question containing "EOT" routed to forensic_delay_analysis at confidence
0.2, which errored demanding XER files — the RAG path holding the answer
never ran. The predefined allowlist's own contract says Q&A stays on the
RAG-grounded path.
"""

from __future__ import annotations

from app.core.predefined_reasoning import lookup_question_hijack


EOT_Q = "Within how many days must the Contractor give notice of an EOT claim on this project?"


class TestLookupQuestionHijack:
    def test_the_live_repro_is_caught(self):
        assert lookup_question_hijack(EOT_Q, 0.2) is True

    def test_high_confidence_routes_untouched(self):
        assert lookup_question_hijack(EOT_Q, 0.8) is False

    def test_imperative_deliverables_untouched(self):
        assert lookup_question_hijack(
            "Calculate the interim payment certificate amount: gross valuation SAR 10,000,000.",
            0.2,
        ) is False

    def test_deliverable_verb_wins_even_in_question_form(self):
        # "generate" is an explicit ask for the artifact — predefined may run.
        assert lookup_question_hijack("can you generate a WBS for this project?", 0.2) is False

    def test_question_mark_alone_is_question_shaped(self):
        assert lookup_question_hijack("the notice period for EOT claims?", 0.1) is True

    def test_empty_message_never_hijacks(self):
        assert lookup_question_hijack("", 0.0) is False

    def test_boundary_confidence_is_not_hijack(self):
        assert lookup_question_hijack(EOT_Q, 0.5) is False
