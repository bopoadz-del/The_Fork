"""Phase-4 full-surface campaign batch fixes (F31-F35).

Each test is new-shape and was red on the pre-fix code:

* F31 -- fasttrack_analyzer let a list-shaped ``reductions`` past its
  truthiness check and crashed deep in compress_schedule with
  "unhashable type: 'dict'" instead of stating the contract.
* F32 -- translate read the target language ONLY from params; a target in
  the input dict (the shape agents naturally produce) was silently ignored
  and the text came back in Spanish while reporting success.
* F33 -- cache_manager counted as a deliverable tool, so a live
  document-ingestion turn that ran search + a cache lookup ended on
  "Let me route this through the boq_processor" -- a narrated future
  dispatch as the final answer -- without force-synthesis firing.
* F34 -- the strict RAG grounding directive refused user-supplied
  corrections ("480 SAR/m3 does not appear in the reference context");
  the learning agent's entire job is to record exactly those figures.
* F35 -- external-mcp listed and chatted as a ghost: its only functional
  block (mcp_consumer) loads solely with CEREBRUM_VIRGIN=false, missing
  blocks are silently skipped at tool-build time, and the agent truthfully
  told the user it had no tools.
"""
from __future__ import annotations

import pytest

from app.agents import runtime


# ---------------------------------------------------------------- F31


@pytest.mark.asyncio
async def test_fasttrack_rejects_list_shaped_reductions_with_contract_error():
    from app.blocks.fasttrack_analyzer import FastTrackAnalyzerBlock
    acts = [
        {"id": "A", "name": "a", "duration": 10, "predecessors": []},
        {"id": "B", "name": "b", "duration": 6,
         "predecessors": [{"predecessor_id": "A"}]},
    ]
    res = await FastTrackAnalyzerBlock().process(
        {"activities": acts, "reductions": [{"B": 4}]}, {})
    assert res["status"] == "error"
    assert "mapping" in res["error"]
    assert "unhashable" not in res["error"]


@pytest.mark.asyncio
async def test_fasttrack_rejects_non_numeric_reduction_values():
    from app.blocks.fasttrack_analyzer import FastTrackAnalyzerBlock
    acts = [{"id": "A", "name": "a", "duration": 10, "predecessors": []}]
    res = await FastTrackAnalyzerBlock().process(
        {"activities": acts, "reductions": {"A": "four"}}, {})
    assert res["status"] == "error"
    assert "non-negative" in res["error"]


# ---------------------------------------------------------------- F32


@pytest.mark.asyncio
async def test_translate_honours_target_language_in_input_dict():
    from app.blocks.translate import TranslateBlock
    res = await TranslateBlock().process(
        {"text": "reinforced concrete slab", "target": "ar"},
        {"provider": "mock"},
    )
    assert res["status"] == "success"
    assert res["target_language"] == "ar"


@pytest.mark.asyncio
async def test_translate_params_still_win_over_input_dict():
    from app.blocks.translate import TranslateBlock
    res = await TranslateBlock().process(
        {"text": "hello", "target": "fr"},
        {"provider": "mock", "target": "de"},
    )
    assert res["status"] == "success"
    assert res["target_language"] == "de"


# ---------------------------------------------------------------- F33


def test_cache_manager_is_not_a_deliverable_tool():
    # A cache hit/miss is infrastructure. If this pin breaks, re-read the
    # live document-ingestion transcript in the F33 ledger entry before
    # deciding the set can shrink: search + cache lookup alone must leave
    # force-synthesis armed.
    assert "cache_manager" in runtime._NON_DELIVERABLE_TOOLS
    assert "search_project_documents" in runtime._NON_DELIVERABLE_TOOLS
    assert "smart_orchestrator" in runtime._NON_DELIVERABLE_TOOLS


# ---------------------------------------------------------------- F34


def _msgs(question: str):
    return [{"role": "user", "content": question}]


def test_correction_turn_directive_makes_user_figures_authoritative():
    msgs = _msgs("The actual supplier invoice came to 480 SAR/m3, "
                 "you predicted 420. Record it.")
    applied = runtime._apply_rag_context(
        msgs, {"role": "system", "content": "CTX: indicative rate 420 SAR/m3"},
        user_data_authoritative=True,
    )
    assert applied
    folded = msgs[-1]["content"]
    assert "AUTHORITATIVE first-party data" in folded
    assert "using ONLY the reference context" not in folded


def test_default_agents_keep_strict_grounding():
    msgs = _msgs("What is the retention percentage?")
    runtime._apply_rag_context(
        msgs, {"role": "system", "content": "CTX: retention is 10%"})
    assert "using ONLY the reference context" in msgs[-1]["content"]


def test_strict_grounding_directive_carves_out_tool_extraction():
    # F36 -- "say you don't have it" suppressed ALL tool calls: bim-analyst
    # refused an IFC element census with zero tool calls because the census
    # wasn't in the retrieved text, while bim_extract sat in its roster.
    # The strict directive must rank tools ABOVE refusal.
    msgs = _msgs("How many walls are in qa_building.ifc?")
    runtime._apply_rag_context(
        msgs, {"role": "system", "content": "CTX: unrelated BOQ text"})
    folded = msgs[-1]["content"]
    assert "CALL THE TOOL" in folded
    assert "neither the context nor your tools" in folded


def test_learning_agent_config_declares_user_data_authoritative():
    runtime.load_agents()
    agent = runtime.AGENT_REGISTRY.get("learning")
    assert agent is not None
    assert agent.user_data_authoritative is True
    # and nobody else silently inherits it
    others = [a.name for a in runtime.AGENT_REGISTRY.values()
              if a.user_data_authoritative and a.name != "learning"]
    assert others == []


# ---------------------------------------------------------------- F35


def _agent(blocks):
    return runtime.Agent(
        name="probe", description="", system_prompt="", allowed_blocks=blocks)


def test_agent_with_no_loaded_functional_blocks_is_unavailable(monkeypatch):
    monkeypatch.setattr(runtime, "BLOCK_REGISTRY", {"cache_manager": object()})
    reason = _agent(["mcp_consumer", "cache_manager"]).unavailable_reason()
    assert reason is not None
    assert "mcp_consumer" in reason
    assert "CEREBRUM_VIRGIN" in reason


def test_agent_with_any_loaded_functional_block_is_available(monkeypatch):
    monkeypatch.setattr(
        runtime, "BLOCK_REGISTRY",
        {"boq_processor": object(), "cache_manager": object()})
    assert _agent(["boq_processor", "mcp_consumer"]).unavailable_reason() is None


def test_prose_agent_with_no_blocks_stays_available(monkeypatch):
    monkeypatch.setattr(runtime, "BLOCK_REGISTRY", {})
    assert _agent([]).unavailable_reason() is None


def test_agents_list_surfaces_availability():
    # Route-level shape check against the real registry: every listed agent
    # carries the availability fields, and any unavailable one names why.
    from fastapi.testclient import TestClient
    from app.main import app
    from app.routers.agents import require_user
    app.dependency_overrides[require_user] = lambda: {"user_id": "test-user"}
    try:
        client = TestClient(app)
        data = client.get("/v1/agents").json()
        assert data["count"] >= 1
        for a in data["agents"]:
            assert "available" in a
            if not a["available"]:
                assert a["unavailable_reason"]
    finally:
        app.dependency_overrides.pop(require_user, None)
