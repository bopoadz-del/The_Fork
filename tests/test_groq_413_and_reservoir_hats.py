"""Live Infra Pack empties after #408: Groq 413, reservoir holiday/spark,
WIR steal on job requisition, and missing deliverable drafts.

No live LLM. Pins the machinery that used to leave M1/M3/M6/M7/M9/M12/M14
empty on theshovel.ai once Kimi filtered and Groq TPM-8000 413'd.
"""
from __future__ import annotations

import json

import pytest

from app.agents.runtime import (
    _EMPTY_RESPONSE_FALLBACK,
    _compact_messages_for_tpm,
    _format_as_built_note,
    _format_claim_notice,
    _format_job_requisition,
    _format_payment_certificate,
    _format_rfp_draft,
    _format_wbs_result,
    _infer_commissioning_systems,
    _message_wants_first_run_wbs,
    _message_wants_job_requisition,
    _predispatch_remaining_deliverables,
    _recover_answer_from_tool_messages,
    _text_needs_tool_recovery,
)
from tests.conftest import requires_construction_kit

M1 = (
    "From Annexure 2 - Baseline Program XER.xer and Contract Data, build a WBS "
    "for the ten access-gated community milestones. Time for Completion of the "
    "whole of the Works is 852 days. Show Milestone 1 (Southern Community 1a "
    "Media, 397 days) and Milestone 3 (Boulevard Community 1c Boulevard "
    "South-West, 487 days) as separate branches with durations in calendar days."
)
M3 = (
    "Generate a commissioning checklist for the PWPS-02 reservoir at "
    "1e-North Center before the first wet test. Ground it in Cast In-Situ "
    "Concretes works for PWPS-02.pdf and the Week 52 pour record: C21-OPC "
    "reservoir blinding, planned 350 m3, poured 310 m3 on 7 and 9 January 2025."
)
M6 = (
    "Prepare a job requisition for street-lighting installation. Scope is the "
    "AM Rev Design NOC for Street Lighting (expires 17 July 2025) plus the "
    "Grand Mosque Phase 2 VO items: solar 6 to 8 m single-arm 1x120W 230V "
    "60Hz poles, traffic signage, and road markings. Include prequalification "
    "and a shortlist rubric."
)
M7 = (
    "Draft Interim Payment Certificate No. 4 from Contract Data. Accepted "
    "Contract Amount SAR 1,754,504,456.25. Advance payment 10%, retention 10% "
    "of each IPC, amortisation 10% of each IPC. This period certified work is "
    "SAR 42,800,000. Show gross, advance recovery, retention, and net payable "
    "in SAR."
)
M9 = (
    "Draft a delay claim notice under Contract Data clause 8.8.1 for late "
    "access to Milestone 1 (Southern Community 1a Media). Access was given 28 "
    "calendar days after the Engineer instructed date. Milestone 1 delay "
    "damages are 0.015% of the Contract Price per calendar day. Use Aconex as "
    "the approved communication method."
)
M12 = (
    "Prepare an as-built deviation note for PWPS-02 reservoir blinding: "
    "planned 350 m3 versus poured 310 m3 on 7 and 9 January 2025. State the "
    "40 m3 shortfall and percent."
)
M14 = (
    "Prepare an RFP for a stormwater manhole-rationalisation subcontract: "
    "remove the 24-manhole radius cluster in RFI002 and install a formed GRP "
    "radiused or closed concrete channel. Write the invitation letter, scope "
    "of works, prequalification, evaluation method, and key dates in full "
    "paragraphs. Reference SOPR and UMA Stormwater DDC 20212200076."
)


def test_text_needs_tool_recovery_on_413_and_empty():
    assert _text_needs_tool_recovery("")
    assert _text_needs_tool_recovery(_EMPTY_RESPONSE_FALLBACK)
    assert _text_needs_tool_recovery(
        'groq HTTP 413: {"error":{"message":"Request too large ... TPM": Limit 8000'
    )
    assert not _text_needs_tool_recovery("Claim notice under 8.8.1")


def test_compact_messages_cuts_tool_dumps():
    huge = json.dumps({"activities": ["x" * 80] * 200})
    messages = [
        {"role": "system", "content": "you are a test"},
        {"role": "user", "content": M1},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "1", "type": "function",
             "function": {"name": "primavera_parser", "arguments": "{}"}},
        ]},
        {"role": "tool", "tool_call_id": "1", "name": "primavera_parser",
         "content": huge},
    ]
    assert len(huge) > 1200
    out = _compact_messages_for_tpm(messages, budget=2000)
    tool = next(m for m in out if m.get("role") == "tool")
    assert len(str(tool.get("content") or "")) < len(huge)
    assert "truncated" in str(tool.get("content") or "").lower() or len(
        str(tool.get("content") or "")
    ) <= 1200


def test_recover_413_from_commissioning_tool():
    payload = {
        "status": "success",
        "action": "commissioning_checklist_generated",
        "summary": {"systems_covered": 1, "total_tests": 2, "pending": 2},
        "checklists_by_system": {
            "reservoir": [
                {
                    "test": "First wet test / watertightness",
                    "standard": "BS 8007",
                    "witness_required": True,
                    "hold_point": True,
                    "acceptance_criteria": "No visible leakage",
                }
            ]
        },
    }
    recovered = _recover_answer_from_tool_messages(
        "groq HTTP 413: TPM Limit 8000",
        [
            {"role": "user", "content": M3},
            {"role": "tool", "content": json.dumps(payload)},
        ],
    )
    assert "First wet test" in recovered
    assert "holiday" not in recovered.lower()
    assert "spark" not in recovered.lower()


def test_message_wants_job_req_and_first_run_wbs():
    assert _message_wants_job_requisition(M6)
    assert not _message_wants_job_requisition(M3)
    assert _message_wants_first_run_wbs(M1)
    assert not _message_wants_first_run_wbs("what is a WBS?")


@requires_construction_kit
@pytest.mark.asyncio
async def test_reservoir_checklist_has_no_holiday_spark():
    from app.containers.construction import ConstructionContainer

    out = await ConstructionContainer().commissioning_checklist(
        {}, {"systems": ["reservoir"]}
    )
    assert out["status"] == "success"
    tests = out["checklists_by_system"]["reservoir"]
    names = {t["test"] for t in tests}
    assert "First wet test / watertightness" in names
    assert "Holiday / spark test" not in names
    blob = json.dumps(out).lower()
    assert "holiday" not in blob
    assert "spark" not in blob
    assert "d4787" not in blob


@requires_construction_kit
@pytest.mark.asyncio
async def test_waterproofing_still_has_holiday_spark():
    from app.containers.construction import ConstructionContainer

    out = await ConstructionContainer().commissioning_checklist(
        {}, {"systems": ["waterproofing"]}
    )
    names = {t["test"] for t in out["checklists_by_system"]["waterproofing"]}
    assert "Holiday / spark test" in names


@requires_construction_kit
@pytest.mark.asyncio
async def test_job_requisition_drafts_m6():
    from app.containers.construction import ConstructionContainer

    out = await ConstructionContainer().job_requisition({"text": M6}, {})
    assert out["status"] == "success"
    assert out["action"] == "job_requisition"
    assert "Street-lighting" in out["scope"] or "street" in out["scope"].lower()
    assert out["prequalification"]
    assert out["shortlist_rubric"]
    rendered = _format_job_requisition(out)
    assert "17 July 2025" in rendered or "NOC" in rendered


@requires_construction_kit
@pytest.mark.asyncio
async def test_rfp_drafts_m14():
    from app.containers.construction import ConstructionContainer

    out = await ConstructionContainer().rfp_draft({"text": M14}, {})
    assert out["status"] == "success"
    assert "GRP" in out["scope_of_works"] or "channel" in out["scope_of_works"].lower()
    rendered = _format_rfp_draft(out)
    assert "invitation" in rendered.lower()
    assert "RFI002" in rendered or "SOPR" in rendered


@requires_construction_kit
@pytest.mark.asyncio
async def test_as_built_volume_note_m12():
    from app.containers.construction import ConstructionContainer

    out = await ConstructionContainer().as_built_deviation_report(
        {"text": M12}, {},
    )
    assert out["status"] == "success"
    assert out["planned_m3"] == 350
    assert out["poured_m3"] == 310
    assert out["shortfall_m3"] == 40
    assert out["shortfall_percent"] == 11.43
    assert "40" in _format_as_built_note(out)


@requires_construction_kit
@pytest.mark.asyncio
async def test_delay_claim_notice_m9():
    from app.containers.construction import ConstructionContainer

    out = await ConstructionContainer().claims_builder({"text": M9}, {})
    assert out["status"] == "success"
    assert out["delay_days"] == 28
    assert out["rate_percent_per_day"] == 0.015
    assert out["clause"] == "8.8.1"
    assert "Aconex" in (out.get("communication") or "")
    rendered = _format_claim_notice(out)
    assert "28" in rendered
    assert "0.015" in rendered
    assert "Aconex" in rendered


@requires_construction_kit
@pytest.mark.asyncio
async def test_ipc_parses_accepted_contract_amount_m7():
    from app.containers.construction import ConstructionContainer

    out = await ConstructionContainer().payment_certificate(
        {"message": M7}, {},
    )
    assert out["status"] == "success"
    val = out["valuation"]
    assert val["contract_value"] == 1_754_504_456.25
    assert val["gross_valuation"] == 42_800_000
    assert out["deductions"]["retention_percent"] == 10
    assert out["deductions"]["advance_recovery"] == 4_280_000
    assert out["payment"]["net_due_this_period"] == 34_240_000
    rendered = _format_payment_certificate(out)
    assert "42800000" in rendered.replace(",", "") or "42,800,000" in rendered


@requires_construction_kit
@pytest.mark.asyncio
async def test_wbs_keeps_operator_milestones_m1():
    from app.containers.construction import ConstructionContainer

    out = await ConstructionContainer().generate_wbs(
        {}, {"brief": M1, "target_count": 20},
    )
    assert out["status"] == "success"
    assert out["project_type"] == "infrastructure"
    op = out.get("operator_milestones") or {}
    assert op.get("time_for_completion_days") == 852
    ids = {m["id"]: m["days"] for m in op.get("milestones") or []}
    assert ids[1] == 397
    assert ids[3] == 487
    rendered = _format_wbs_result(out)
    assert "852" in rendered
    assert "397" in rendered
    assert "487" in rendered


@requires_construction_kit
@pytest.mark.asyncio
async def test_predispatch_claim_and_ipc(monkeypatch):
    class _A:
        allowed_blocks = ["construction"]
        name = "contracts-manager"

    msgs = [{"role": "user", "content": M9}]
    out = await _predispatch_remaining_deliverables(_A(), msgs, "proj")
    assert out is not None
    assert out["name"] == "claims_builder"
    assert "8.8.1" in msgs[-1]["content"] or "28" in msgs[-1]["content"]

    msgs = [{"role": "user", "content": M7}]
    out = await _predispatch_remaining_deliverables(_A(), msgs, "proj")
    assert out is not None
    assert out["name"] == "payment_certificate"


@requires_construction_kit
@pytest.mark.asyncio
async def test_infer_then_container_reservoir_for_m3():
    assert _infer_commissioning_systems(M3) == ["reservoir"]
    from app.containers.construction import ConstructionContainer

    out = await ConstructionContainer().commissioning_checklist(
        {}, {"systems": _infer_commissioning_systems(M3)}
    )
    blob = json.dumps(out).lower()
    assert "holiday" not in blob
    assert "spark" not in blob
