"""Live Phase 2: variation_order_manager must draft on the FIRST ask.

Ask1 used to refuse / return hollow Status:Success / route to
change_order_impact (or the empty-fixture unindexed gate). Ask2 sometimes
drafted VO-D-002 with ADD/OMIT lines MATCH. Owner requirement: the first
variation-order ask that already carries synthetic ADD/OMIT + rates must
return a drafted VO body (description, lines, net).
"""
from __future__ import annotations

import json

import pytest

from tests.conftest import requires_construction_kit


# Self-contained first ask — no project corpus, no prior turn, priced lines
# in the operator message. ADD 120 × 85 = 10_200; OMIT 40 × 45 = 1_800;
# net = 8_400.
FIRST_VO_ASK = (
    "Draft a variation order for the following priced lines:\n"
    "ADD 120 m2 ceramic tiling @ 85 SAR/m2\n"
    "OMIT 40 m2 carpet @ 45 SAR/m2"
)

ADD_AMOUNT = 10_200.0
OMIT_AMOUNT = 1_800.0
NET_AMOUNT = 8_400.0


def _matched_actions(message: str) -> list[str]:
    from app.blocks.smart_orchestrator import SmartOrchestratorBlock

    return [
        m["action"]
        for m in SmartOrchestratorBlock()._match_actions(message, None)
    ]


def _assert_drafted_vo_body(result: dict) -> None:
    """A first-ask VO must be a real draft, not hollow success / empty."""
    assert isinstance(result, dict), result
    assert result.get("status") == "success", result
    assert result.get("action") not in {
        None,
        "change_order_analysis",
        "change_order_impact",
        "intelligent_workflow",
    }, result
    assert result.get("error") in (None, ""), result

    description = (
        result.get("description")
        or result.get("vo_description")
        or ""
    )
    assert str(description).strip(), f"missing description: {result}"
    assert "draft a variation order" not in str(description).strip().lower() or (
        "tiling" in str(description).lower() or "carpet" in str(description).lower()
    )

    lines = result.get("lines") or result.get("vo_lines") or []
    assert isinstance(lines, list) and len(lines) >= 2, result
    blob = json.dumps(lines).upper()
    assert "ADD" in blob, lines
    assert "OMIT" in blob, lines
    assert "120" in json.dumps(lines)
    assert "40" in json.dumps(lines)
    assert "85" in json.dumps(lines)
    assert "45" in json.dumps(lines)

    net = result.get("net")
    if net is None:
        net = (result.get("pricing") or {}).get("total_value")
    assert net is not None, result
    assert float(net) == pytest.approx(NET_AMOUNT, rel=1e-4)

    body = (
        result.get("document_content")
        or result.get("vo_document")
        or result.get("draft")
        or ""
    )
    assert str(body).strip(), f"hollow success / empty draft: {result}"
    vo_no = str(result.get("vo_number") or "")
    assert vo_no, result
    assert vo_no.upper().startswith("VO"), vo_no


@requires_construction_kit
def test_first_vo_draft_ask_prefers_variation_order_manager():
    """change_order_impact must not steal a VO *draft* intent on ask1."""
    matched = _matched_actions(FIRST_VO_ASK)
    assert matched, FIRST_VO_ASK
    assert matched[0] == "variation_order_manager", matched
    assert "change_order_impact" not in matched


@requires_construction_kit
def test_impact_ask_still_reaches_change_order_impact():
    """Impact analysis stays on change_order_impact (not a draft intent)."""
    matched = _matched_actions(
        "assess the cost and time impact of variation order VO-12 "
        "adding 300m of storm drain"
    )
    assert "change_order_impact" in matched, matched


@requires_construction_kit
@pytest.mark.asyncio
async def test_first_vo_ask_with_add_omit_rates_drafts_body():
    """First ask (no history, no vo_data) drafts description / lines / net."""
    from app.containers.construction import ConstructionContainer

    result = await ConstructionContainer().variation_order_manager(
        {"message": FIRST_VO_ASK},
        {},
    )
    _assert_drafted_vo_body(result)
    assert str(result.get("vo_number", "")).upper().startswith("VO-D"), result


@requires_construction_kit
@pytest.mark.asyncio
async def test_first_vo_ask_via_route_is_not_change_order_only():
    from app.containers.construction import ConstructionContainer

    container = ConstructionContainer()
    routed = await container.route(
        "variation_order_manager",
        {"message": FIRST_VO_ASK, "text": FIRST_VO_ASK},
        {},
    )
    _assert_drafted_vo_body(routed)

    stolen = await container.change_order_impact(
        {"message": FIRST_VO_ASK, "text": FIRST_VO_ASK},
        {},
    )
    # Impact-only is the live steal: success/error with no VO lines/net.
    stolen_lines = stolen.get("lines") or stolen.get("vo_lines") or []
    stolen_net = stolen.get("net")
    assert not (
        stolen.get("status") == "success"
        and stolen.get("action") == "change_order_analysis"
        and not stolen_lines
        and stolen_net is None
    ) or routed.get("action") != "change_order_analysis"


@requires_construction_kit
@pytest.mark.asyncio
async def test_first_vo_ask_predispatch_injects_draft():
    from app.agents.runtime import (
        _message_wants_vo_draft,
        _predispatch_remaining_deliverables,
    )

    assert _message_wants_vo_draft(FIRST_VO_ASK)

    class _A:
        allowed_blocks = ["construction"]
        name = "contracts-manager"

    msgs = [{"role": "user", "content": FIRST_VO_ASK}]
    out = await _predispatch_remaining_deliverables(_A(), msgs, "proj")
    assert out is not None
    assert out["name"] == "variation_order_manager"
    assert out.get("ok") is True
    _assert_drafted_vo_body(out["result"])
    injected = msgs[-1]["content"]
    assert "PLATFORM PRE-DISPATCH" in injected
    assert "ADD" in injected.upper()
    assert "OMIT" in injected.upper()
    assert "8400" in injected.replace(",", "").replace(" ", "") or "8,400" in injected


def test_empty_fixture_vo_draft_is_not_an_unindexed_refusal():
    """Empty FIXTURE projects must not early-return the unindexed refusal."""
    from app.agents.runtime import (
        _project_has_non_rag_context,
        _should_short_circuit_rag_miss,
    )

    assert _project_has_non_rag_context("empty-fixture", FIRST_VO_ASK) is True
    assert _should_short_circuit_rag_miss(
        {
            "identifier_miss": True,
            "threshold_fired": True,
            "extracted_identifiers": ["85/m2", "45/m2", "VO-D-001"],
        },
        None,
        FIRST_VO_ASK,
    ) is False
