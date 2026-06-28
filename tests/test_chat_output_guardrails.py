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
    _TOOL_FORMAT_FALLBACK,
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
    assert _sanitize_final_text(raw_tool) == _TOOL_FORMAT_FALLBACK

    # Search argument leak observed in production: plain {"query": ..., "top_k": ...}
    search_args = json.dumps({"query": "registered capital", "top_k": 5})
    assert _sanitize_final_text(search_args) == _TOOL_FORMAT_FALLBACK

    # Markdown code block containing only search args.
    md_search = "```json\n{\"query\": \"x\", \"project_id\": \"p\"}\n```"
    assert _sanitize_final_text(md_search) == _TOOL_FORMAT_FALLBACK

    # User-requested JSON example should not be sanitized.
    user_json = '{"answer": "example", "result": "ok"}'
    assert _sanitize_final_text(user_json) == user_json


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
    assert result["answer"] in (_EMPTY_RESPONSE_FALLBACK, _TOOL_FORMAT_FALLBACK)


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


# ── P0B: harden raw-tool-JSON detection ───────────────────────────────────────

from app.agents.runtime import _looks_like_internal_tool_json


def test_looks_like_internal_tool_json_detects_embedded_json_in_prose():
    raw = (
        "We need to emit the tool call. We should output the JSON tool call. "
        '{"name": "search_project_documents", "arguments": {"query": "foo"}}'
    )
    assert _looks_like_internal_tool_json(raw) is True


def test_looks_like_internal_tool_json_ignores_plain_json_without_tool_keys():
    raw = 'The answer is {"answer": "plain json"} and that is fine.'
    assert _looks_like_internal_tool_json(raw) is False


# ── P0C: block raw search-tool argument leakage ───────────────────────────────

from app.agents.runtime import _is_search_tool_args_obj


def test_is_search_tool_args_obj_detects_plain_search_payload():
    assert _is_search_tool_args_obj({"query": "x", "top_k": 5}) is True
    assert _is_search_tool_args_obj({"query": "x", "project_id": "p"}) is True
    assert _is_search_tool_args_obj({"query": "x"}) is False  # ambiguous


def test_is_search_tool_args_obj_detects_tool_envelopes():
    assert _is_search_tool_args_obj({"tool": "search_project_documents", "arguments": {"query": "x"}}) is True
    assert _is_search_tool_args_obj({"name": "search_project_documents", "arguments": {"query": "x"}}) is True
    assert _is_search_tool_args_obj({"function": "search_project_documents", "arguments": {"query": "x"}}) is True


def test_is_search_tool_args_obj_ignores_user_data_json():
    assert _is_search_tool_args_obj({"answer": "example"}) is False
    assert _is_search_tool_args_obj({"items": [{"qty": 1}]}) is False
    assert _is_search_tool_args_obj({"query": "x", "answer": "y"}) is False


def test_sanitize_final_text_allows_user_requested_json():
    boq = '{"items": [{"description": "concrete", "quantity": 10, "unit": "m3"}]}'
    assert _sanitize_final_text(boq) == boq

    code_example = 'Here is an example JSON: `{"status": "ok"}`'
    assert _sanitize_final_text(code_example) == code_example


@pytest.mark.asyncio
async def test_chat_blocks_raw_search_args_leak(monkeypatch):
    """A final answer that is only {"query": ..., "top_k": ...} must not reach the user."""
    agent = Agent(
        name="test-agent",
        description="test",
        system_prompt="You are a test assistant. Be concise.",
        allowed_blocks=[],
    )

    search_args = json.dumps({"query": "registered capital", "top_k": 5})

    async def fake_call_llm(messages, api_key, *, project_id=None, user_id=None, with_tools=True):
        return {
            "status": "success",
            "choice": {
                "message": {"role": "assistant", "content": search_args, "tool_calls": []},
                "finish_reason": "stop",
            },
        }

    monkeypatch.setattr(agent, "_call_llm", fake_call_llm)

    result = await agent.chat("What is the registered capital?")
    assert result["status"] == "success"
    assert search_args not in result["answer"]
    assert "internal search formatting issue" in result["answer"].lower()


@pytest.mark.asyncio
async def test_chat_stream_blocks_raw_search_args_leak(monkeypatch):
    """Streaming must also hide raw search argument payloads."""
    agent = Agent(
        name="test-agent",
        description="test",
        system_prompt="You are a test assistant. Be concise.",
        allowed_blocks=[],
    )

    search_args = json.dumps({"query": "registered capital", "top_k": 5})

    async def fake_call_llm(messages, api_key, *, project_id=None, user_id=None, with_tools=True):
        return {
            "status": "success",
            "choice": {
                "message": {"role": "assistant", "content": search_args, "tool_calls": []},
                "finish_reason": "stop",
            },
        }

    monkeypatch.setattr(agent, "_call_llm", fake_call_llm)

    events = []
    async for event in agent.chat_stream("What is the registered capital?"):
        events.append(event)

    token_text = "".join(e.get("content", "") for e in events if e["type"] == "token")
    assert search_args not in token_text
    assert "internal search formatting issue" in token_text.lower()
    end_events = [e for e in events if e["type"] == "end"]
    assert len(end_events) == 1
    assert end_events[0].get("sources") == []


@pytest.mark.asyncio
async def test_chat_preserves_user_requested_json(monkeypatch):
    """If the model returns legitimate user-requested JSON data, it is not sanitized."""
    agent = Agent(
        name="test-agent",
        description="test",
        system_prompt="You are a test assistant. Be concise.",
        allowed_blocks=[],
    )

    user_json = '{"items": [{"description": "concrete", "quantity": 10, "unit": "m3"}]}'

    async def fake_call_llm(messages, api_key, *, project_id=None, user_id=None, with_tools=True):
        return {
            "status": "success",
            "choice": {
                "message": {"role": "assistant", "content": user_json, "tool_calls": []},
                "finish_reason": "stop",
            },
        }

    monkeypatch.setattr(agent, "_call_llm", fake_call_llm)

    result = await agent.chat("Return a BOQ JSON example.")
    assert result["status"] == "success"
    assert user_json in result["answer"]
