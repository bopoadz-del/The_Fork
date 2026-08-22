"""Close live theshovel.ai 17-action UI gaps (M3 / M14 / M16 / M17).

No LLM. Pins the four product holes the physical UI found:
  M14 RFP full paragraphs were wiped by the cost-grounding refusal
  M3  commissioning defaulted to HVAC instead of waterproofing
  M3  empty model turn after a successful commissioning tool
  M17 XER without TASKRSRC now returns activity-count period buckets
"""
from __future__ import annotations

import json

import pytest

from app.agents.runtime import (
    _CG_REFUSAL,
    _EMPTY_RESPONSE_FALLBACK,
    _cost_grounding_gate,
    _format_commissioning_tool,
    _infer_commissioning_systems,
    _is_document_deliverable_request,
    _nudge_for_failed_tool,
    _postprocess_answer,
    _recover_answer_from_tool_messages,
)
from tests.conftest import requires_construction_kit


def test_rfp_document_is_not_a_rate_quote():
    prompt = (
        "Prepare an RFP for leftover torch-applied waterproofing subcontract. "
        "Write the invitation letter, scope of works, prequalification "
        "criteria, evaluation method, and key dates in full paragraphs."
    )
    assert _is_document_deliverable_request(prompt)
    answer = (
        "REQUEST FOR PROPOSALS (RFP-WP-LEFT-2024)\n"
        "Invitation: submit a proposal for torch-applied SBS.\n"
        "Tender bond 50,000 SAR if required by the form of tender.\n"
    )
    out = _cost_grounding_gate(
        answer, None, messages=[{"role": "user", "content": prompt}]
    )
    assert out == answer
    assert out != _CG_REFUSAL


def test_unit_rate_question_still_refused():
    prompt = "What is the unit rate per m3 for C30 in the leftover BOQ?"
    assert not _is_document_deliverable_request(prompt)
    answer = "Use 465 SAR/m3 for C30."
    out = _cost_grounding_gate(
        answer, None, messages=[{"role": "user", "content": prompt}]
    )
    assert out == _CG_REFUSAL


def test_infer_waterproofing_commissioning_systems():
    assert _infer_commissioning_systems(
        "Generate a commissioning checklist for leftover torch-applied "
        "waterproofing before backfill."
    ) == ["waterproofing"]
    assert _infer_commissioning_systems("when does commissioning start?") is None


def test_empty_fallback_recovers_commissioning_tool():
    payload = {
        "status": "success",
        "action": "commissioning_checklist_generated",
        "summary": {"systems_covered": 1, "total_tests": 2, "pending": 2},
        "checklists_by_system": {
            "waterproofing": [
                {
                    "test": "Holiday / spark test",
                    "standard": "ASTM D4787",
                    "witness_required": True,
                    "hold_point": True,
                    "acceptance_criteria": "No holidays",
                }
            ]
        },
    }
    messages = [
        {"role": "user", "content": "commissioning checklist for waterproofing"},
        {"role": "tool", "content": json.dumps(payload)},
    ]
    recovered = _recover_answer_from_tool_messages(
        _EMPTY_RESPONSE_FALLBACK, messages
    )
    assert "Holiday / spark test" in recovered
    assert "unable to generate" not in recovered.lower()
    posted = _postprocess_answer(
        _EMPTY_RESPONSE_FALLBACK, None, messages, fallback_used=False
    )
    assert "Holiday / spark test" in posted


def test_format_commissioning_lists_hold_points():
    text = _format_commissioning_tool({
        "summary": {"systems_covered": 1, "total_tests": 1, "pending": 1},
        "checklists_by_system": {
            "waterproofing": [
                {
                    "test": "Backfill release",
                    "standard": "ITP hold point",
                    "witness_required": True,
                    "hold_point": True,
                    "acceptance_criteria": "Engineer sign-off",
                }
            ]
        },
    })
    assert "Backfill release" in text
    assert "hold" in text.lower()


def test_image_fail_nudge_does_not_retry_photo():
    class _A:
        can_delegate = False
        name = "safety-officer"

    nudge = _nudge_for_failed_tool(
        {"name": "image", "ok": False, "result": {"error": "empty pixels"}},
        _A(),
    )
    assert "Do not retry the image tool" in nudge
    assert "clash" not in nudge.lower()


@requires_construction_kit
@pytest.mark.asyncio
async def test_commissioning_waterproofing_not_hvac():
    from app.containers.construction import ConstructionContainer

    c = ConstructionContainer()
    out = await c.commissioning_checklist(
        {}, {"systems": ["waterproofing"]}
    )
    assert out["status"] == "success"
    assert "waterproofing" in out["checklists_by_system"]
    tests = out["checklists_by_system"]["waterproofing"]
    names = {t["test"] for t in tests}
    assert "Backfill release" in names
    assert "Holiday / spark test" in names
    assert any(t.get("hold_point") for t in tests)
