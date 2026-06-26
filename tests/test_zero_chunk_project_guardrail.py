"""Tests for the zero-chunk project guardrail.

Chat must refuse to answer document-corpus questions for projects that
have no indexed chunks instead of hallucinating or silently falling back
on general knowledge.
"""
from __future__ import annotations

import pytest

from app.agents.runtime import Agent, _UNINDEXED_PROJECT_MESSAGE


def make_test_agent() -> Agent:
    return Agent(
        name="test-agent",
        description="test",
        system_prompt="You are a test assistant. Be concise.",
        allowed_blocks=[],
    )


@pytest.mark.asyncio
async def test_chat_refuses_unindexed_project(monkeypatch):
    agent = make_test_agent()

    async def fake_call_llm(*args, **kwargs):
        raise AssertionError("LLM should not be called for an unindexed project")

    monkeypatch.setattr(agent, "_call_llm", fake_call_llm)
    monkeypatch.setattr(
        "app.agents.runtime.project_is_rag_ready",
        lambda _pid: False,
    )

    result = await agent.chat(
        "What is this project about?",
        project_id="empty_proj",
        conversation_id="ws-empty_proj",
    )

    assert result["status"] == "success"
    assert result["answer"] == _UNINDEXED_PROJECT_MESSAGE
    assert result["iterations"] == 0
    assert result["tool_calls"] == []


@pytest.mark.asyncio
async def test_chat_stream_refuses_unindexed_project(monkeypatch):
    agent = make_test_agent()

    async def fake_call_llm(*args, **kwargs):
        raise AssertionError("LLM should not be called for an unindexed project")

    monkeypatch.setattr(agent, "_call_llm", fake_call_llm)
    monkeypatch.setattr(
        "app.agents.runtime.project_is_rag_ready",
        lambda _pid: False,
    )

    events = []
    async for event in agent.chat_stream(
        "What is this project about?",
        project_id="empty_proj",
        conversation_id="ws-empty_proj",
    ):
        events.append(event)

    types = [e["type"] for e in events]
    assert "start" in types
    assert "token" in types
    assert "end" in types
    assert "error" not in types

    token_text = "".join(e.get("content", "") for e in events if e["type"] == "token")
    assert token_text == _UNINDEXED_PROJECT_MESSAGE

    end_event = [e for e in events if e["type"] == "end"][0]
    assert end_event.get("iterations") == 0
    assert end_event.get("sources") == []


@pytest.mark.asyncio
async def test_chat_allows_indexed_project(monkeypatch):
    """When the project has chunks, the guardrail should not fire."""
    agent = make_test_agent()

    called = False

    async def fake_call_llm(messages, api_key, *, project_id=None, user_id=None, with_tools=True):
        nonlocal called
        called = True
        return {
            "status": "success",
            "choice": {
                "message": {"role": "assistant", "content": "Here is the answer.", "tool_calls": []},
                "finish_reason": "stop",
            },
        }

    monkeypatch.setattr(agent, "_call_llm", fake_call_llm)
    monkeypatch.setattr(
        "app.agents.runtime.project_is_rag_ready",
        lambda _pid: True,
    )

    result = await agent.chat(
        "What is this project about?",
        project_id="indexed_proj",
    )

    assert called is True
    assert result["answer"] == "Here is the answer."
