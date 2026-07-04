"""After a successful deliverable (non-search) tool result, the next LLM call
must be tool-free (force_synthesis). This stops the tool-loop and, on Groq,
avoids the large-context tool_use_failed(400)/429 -> slow-fallback hang, since a
tool-free request can neither emit a mis-parsed tool call nor loop.

Regression guard for the commissioning-hang fix.
"""
from __future__ import annotations

import asyncio
from unittest.mock import patch, AsyncMock

import pytest

from app.agents.runtime import Agent


def _pa_agent():
    return Agent(
        name="project-assistant", description="pa",
        system_prompt="x", allowed_blocks=["construction"],
    )


def _run(coro):
    return asyncio.run(coro)


def _drain(agen):
    async def go():
        return [ev async for ev in agen]
    return _run(go())


def _script_two_calls(with_tools_seen):
    """Fake _call_llm: 1st call returns a commissioning tool_call, 2nd returns a
    final answer. Records the with_tools kwarg of every call."""
    state = {"n": 0}

    async def fake(_self, messages, api_key, **kwargs):
        with_tools_seen.append(kwargs.get("with_tools", True))
        state["n"] += 1
        if state["n"] == 1:
            return {"status": "success", "choice": {"message": {
                "role": "assistant", "content": "",
                "tool_calls": [{"id": "c1", "type": "function", "function": {
                    "name": "commissioning_checklist",
                    "arguments": '{"systems":["electrical"]}'}}]}}}
        return {"status": "success", "choice": {"message": {
            "role": "assistant", "content": "Here is the commissioning checklist."}}}

    return fake


async def _fake_tool_ok(self, tool_call, **kwargs):
    return {"name": "commissioning_checklist", "ok": True,
            "result": {"status": "success", "checklists_by_system": {"electrical": []}}}


async def _fake_tool_search(self, tool_call, **kwargs):
    return {"name": "search_project_documents", "ok": True,
            "result": {"status": "success", "results": []}}


def test_force_synthesis_after_deliverable_tool(monkeypatch):
    """A successful commissioning_checklist result -> 2nd call is tool-free."""
    agent = _pa_agent()
    seen: list = []
    with patch.object(Agent, "_call_llm", _script_two_calls(seen)), \
         patch.object(Agent, "_run_tool_call", _fake_tool_ok):
        events = _drain(agent.chat_stream(
            user_message="Generate a commissioning checklist for electrical.",
            history=[], project_id=None, conversation_id=None, user_id=None,
        ))
    assert seen == [True, False], seen  # 1st call has tools, synthesis does not
    assert any(e.get("type") == "token" for e in events)


def test_search_result_does_not_force_synthesis(monkeypatch):
    """A search result is exploratory — it must NOT force synthesis, so the
    model can keep working (2nd call still has tools)."""
    agent = _pa_agent()
    seen: list = []
    with patch.object(Agent, "_call_llm", _script_two_calls(seen)), \
         patch.object(Agent, "_run_tool_call", _fake_tool_search):
        _drain(agent.chat_stream(
            user_message="What does the spec say about concrete cover?",
            history=[], project_id=None, conversation_id=None, user_id=None,
        ))
    assert seen == [True, True], seen  # search does not disable tools


def test_force_synthesis_env_disable(monkeypatch):
    """AGENT_FORCE_SYNTHESIS=0 restores the old always-offer-tools behaviour."""
    monkeypatch.setenv("AGENT_FORCE_SYNTHESIS", "0")
    agent = _pa_agent()
    seen: list = []
    with patch.object(Agent, "_call_llm", _script_two_calls(seen)), \
         patch.object(Agent, "_run_tool_call", _fake_tool_ok):
        _drain(agent.chat_stream(
            user_message="Generate a commissioning checklist for electrical.",
            history=[], project_id=None, conversation_id=None, user_id=None,
        ))
    assert seen == [True, True], seen  # kill-switch keeps tools on both calls
