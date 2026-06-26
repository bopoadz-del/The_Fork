"""Source contract tests for streaming and non-streaming chat.

Every successful RAG-backed chat answer must expose a structured ``sources``
array (doc_id, doc_name, page_or_section, score, confidence) in addition to
any inline citations.
"""
from __future__ import annotations

import pytest

from app.agents.runtime import Agent


def _make_agent() -> Agent:
    return Agent(
        name="test-agent",
        description="test",
        system_prompt="You are a test assistant. Cite sources.",
        allowed_blocks=[],
    )


def _patch_guardrail(monkeypatch):
    monkeypatch.setattr(
        "app.agents.runtime.project_is_rag_ready", lambda _pid: True
    )


def _fake_rag_audit():
    return {
        "project_id": "proj_a",
        "chunks": [
            {
                "doc_id": "doc_1",
                "chunk_index": 1,
                "chunk_id": "proj_a:doc_1:1",
                "score": 0.82,
            }
        ],
    }


@pytest.mark.asyncio
async def test_chat_returns_structured_sources(monkeypatch):
    agent = _make_agent()

    async def fake_call_llm(messages, api_key, *, project_id=None, user_id=None, with_tools=True):
        return {
            "status": "success",
            "choice": {
                "message": {
                    "content": "Answer from source [source: SomeDoc.pdf, chunk 1].",
                    "tool_calls": [],
                },
                "finish_reason": "stop",
            },
        }

    def fake_rag_inject(**kwargs):
        return (
            {"role": "system", "content": "Relevant project context."},
            _fake_rag_audit(),
        )

    monkeypatch.setattr(agent, "_call_llm", fake_call_llm)
    monkeypatch.setattr("app.agents.runtime.rag_inject", fake_rag_inject)
    monkeypatch.setattr(
        "app.core.projects.get_document", lambda did: {"original_name": "SomeDoc.pdf"}
    )
    _patch_guardrail(monkeypatch)

    result = await agent.chat("What does the spec say?", project_id="proj_a")

    assert result["status"] == "success"
    assert "sources" in result
    assert len(result["sources"]) == 1
    src = result["sources"][0]
    assert src["doc_id"] == "doc_1"
    assert src["doc_name"] == "SomeDoc.pdf"
    assert src["page_or_section"] == "chunk #1"
    assert src["score"] == 0.82
    assert src["confidence"] == "High"
    assert src["project_id"] == "proj_a"


@pytest.mark.asyncio
async def test_chat_stream_end_event_includes_sources(monkeypatch):
    agent = _make_agent()

    async def fake_call_llm(messages, api_key, *, project_id=None, user_id=None, with_tools=True):
        return {
            "status": "success",
            "choice": {
                "message": {
                    "content": "Answer from source [source: SomeDoc.pdf, chunk 1].",
                    "tool_calls": [],
                },
                "finish_reason": "stop",
            },
        }

    def fake_rag_inject(**kwargs):
        return (
            {"role": "system", "content": "Relevant project context."},
            _fake_rag_audit(),
        )

    monkeypatch.setattr(agent, "_call_llm", fake_call_llm)
    monkeypatch.setattr("app.agents.runtime.rag_inject", fake_rag_inject)
    _patch_guardrail(monkeypatch)

    events = []
    async for event in agent.chat_stream("What does the spec say?", project_id="proj_a"):
        events.append(event)

    end_events = [e for e in events if e["type"] == "end"]
    assert len(end_events) == 1
    end = end_events[0]
    assert "sources" in end
    assert len(end["sources"]) == 1
    assert end["sources"][0]["doc_id"] == "doc_1"


@pytest.mark.asyncio
async def test_chat_source_labels_do_not_expose_raw_paths(monkeypatch):
    agent = _make_agent()

    async def fake_call_llm(messages, api_key, *, project_id=None, user_id=None, with_tools=True):
        return {
            "status": "success",
            "choice": {
                "message": {
                    "content": "See [source: G:\\My Drive\\SomeDoc.pdf, chunk 1].",
                    "tool_calls": [],
                },
                "finish_reason": "stop",
            },
        }

    def fake_rag_inject(**kwargs):
        return (
            {"role": "system", "content": "Relevant project context."},
            _fake_rag_audit(),
        )

    monkeypatch.setattr(agent, "_call_llm", fake_call_llm)
    monkeypatch.setattr("app.agents.runtime.rag_inject", fake_rag_inject)
    monkeypatch.setattr(
        "app.core.projects.get_document", lambda did: {"original_name": "SomeDoc.pdf"}
    )
    _patch_guardrail(monkeypatch)

    result = await agent.chat("What does the spec say?", project_id="proj_a")

    assert "G:\\My Drive" not in result["answer"]
    assert "[source: SomeDoc.pdf, chunk 1]" in result["answer"]
    assert result["sources"][0]["doc_name"] == "SomeDoc.pdf"


@pytest.mark.asyncio
async def test_chat_returns_empty_sources_when_no_rag(monkeypatch):
    agent = _make_agent()

    async def fake_call_llm(messages, api_key, *, project_id=None, user_id=None, with_tools=True):
        return {
            "status": "success",
            "choice": {
                "message": {"content": "Plain answer.", "tool_calls": []},
                "finish_reason": "stop",
            },
        }

    def fake_rag_inject(**kwargs):
        return None, {"chunks": []}

    monkeypatch.setattr(agent, "_call_llm", fake_call_llm)
    monkeypatch.setattr("app.agents.runtime.rag_inject", fake_rag_inject)

    result = await agent.chat("Hello?", project_id="proj_a")

    assert result["status"] == "success"
    assert result.get("sources") == []


# ── P0B source-contract hardening additions ──────────────────────────────────

from app.agents.runtime import (
    _answer_is_caveat,
    _sanitize_inline_paths,
)


def test_answer_is_caveat_detects_refusal_phrases():
    assert _answer_is_caveat("I could not locate any record for that.") is True
    assert _answer_is_caveat("I cannot confirm this from the sources.") is True
    assert _answer_is_caveat("The design review procedure is as follows.") is False


def test_sanitize_inline_paths_cleans_markdown_table_source_cell():
    raw = "| Source |\n| G:\\My Drive\\PRC-501.pdf |"
    cleaned = _sanitize_inline_paths(raw)
    assert "G:\\My Drive" not in cleaned
    assert "PRC-501.pdf" in cleaned


def test_build_sources_returns_empty_for_caveat(monkeypatch):
    audit = {
        "project_id": "proj_x",
        "chunks": [
            {"doc_id": "d1", "chunk_index": 0, "chunk_id": "c1", "score": 0.6},
        ],
    }
    monkeypatch.setattr(
        "app.core.projects.get_document",
        lambda did: {"original_name": "SomeDoc.pdf"},
    )
    from app.agents.runtime import _build_sources_from_audit
    out = _build_sources_from_audit(audit, "I could not locate any record for that.")
    assert out == []
