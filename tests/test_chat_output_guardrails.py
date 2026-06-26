"""Tests for chat output guardrails:

* raw internal tool-call JSON must not reach the user
* empty final answers must be replaced by a controlled fallback
* DSML markup must still be stripped
"""
from __future__ import annotations

import json

import pytest


from app.agents.runtime import (
    Agent,
    _looks_like_internal_tool_json,
    _sanitize_final_text,
    _EMPTY_RESPONSE_FALLBACK,
)


def test_looks_like_internal_tool_json_detects_tool_call_shape():
    raw = json.dumps({"name": "search_project_documents", "arguments": {"query": "PRC-501"}})
    assert _looks_like_internal_tool_json(raw) is True

    raw_list = json.dumps([{"type": "function", "function": {"name": "foo", "arguments": "{}"}}])
    assert _looks_like_internal_tool_json(raw_list) is True

    assert _looks_like_internal_tool_json("Plain text answer") is False
    assert _looks_like_internal_tool_json('{"answer": "plain json"}') is False


def test_sanitize_final_text_strips_dsml_and_tool_json():
    assert _sanitize_final_text("Hello world") == "Hello world"
    assert _sanitize_final_text("  Hello world  ") == "Hello world"
    assert _sanitize_final_text("") == ""

    dsml = "Answer here <｜｜DSML｜｜tool_calls>...</｜｜DSML｜｜tool_calls>"
    assert _sanitize_final_text(dsml) == "Answer here"

    raw_tool = json.dumps({"name": "search_project_documents", "arguments": {"query": "x"}})
    assert _sanitize_final_text(raw_tool) == ""


@pytest.mark.asyncio
async def test_chat_replaces_raw_tool_json_with_fallback(monkeypatch):
    """If the LLM returns a raw tool-call JSON object as 'content', the
    user-facing answer must be the safe fallback, not the JSON."""
    agent = Agent(
        name="test-agent",
        description="test",
        system_prompt="You are a test assistant. Be concise.",
        allowed_blocks=[],
    )

    raw_tool = json.dumps({"name": "search_project_documents", "arguments": {"query": "PRC-501", "top_k": 20}})

    async def fake_call_llm(messages, api_key, *, project_id=None, user_id=None, with_tools=True):
        return {
            "status": "success",
            "choice": {
                "message": {"role": "assistant", "content": raw_tool, "tool_calls": []},
                "finish_reason": "stop",
            },
        }

    monkeypatch.setattr(agent, "_call_llm", fake_call_llm)

    result = await agent.chat("What is PRC-501?")
    assert result["status"] == "success"
    assert raw_tool not in result["answer"]
    assert result["answer"] == _EMPTY_RESPONSE_FALLBACK


@pytest.mark.asyncio
async def test_chat_replaces_empty_final_with_fallback(monkeypatch):
    """If the LLM returns an empty final answer, the user sees the fallback."""
    agent = Agent(
        name="test-agent",
        description="test",
        system_prompt="You are a test assistant. Be concise.",
        allowed_blocks=[],
    )

    async def fake_call_llm(messages, api_key, *, project_id=None, user_id=None, with_tools=True):
        return {
            "status": "success",
            "choice": {
                "message": {"role": "assistant", "content": "", "tool_calls": []},
                "finish_reason": "stop",
            },
        }

    monkeypatch.setattr(agent, "_call_llm", fake_call_llm)

    result = await agent.chat("Hello?")
    assert result["status"] == "success"
    assert result["answer"] == _EMPTY_RESPONSE_FALLBACK
